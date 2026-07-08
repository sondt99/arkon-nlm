import json
from types import SimpleNamespace

import pytest
from fastapi.responses import StreamingResponse

from app.ai.agent_protocol import AssistantTurn, ToolCall
from app.routers import claude_gateway
from app.services.config_service import ConfigService
from app.services.mcp_auth_service import MCPAuthService, ResolvedIdentity


class _FakeSession:
    """claude_gateway does no persistence — only ConfigService(db).get() touches db,
    and that's monkeypatched per-test. This just satisfies the "no config row" path."""

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: None)


def _identity() -> ResolvedIdentity:
    import uuid

    return ResolvedIdentity(
        employee_id=uuid.uuid4(),
        employee_name="Test Employee",
        department_id=uuid.uuid4(),
        department_name="Engineering",
    )


def _fake_registry(fake_llm):
    class _FakeRegistry:
        def __init__(self, _db):
            pass

        async def get_gateway_llm(self):
            return fake_llm

    return _FakeRegistry


def _messages_request(**overrides):
    body = {
        "model": "claude-sonnet-4-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hi"}],
    }
    body.update(overrides)
    return claude_gateway.MessagesRequest(**body)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_gateway_token_rejects_missing_credentials():
    with pytest.raises(claude_gateway.AnthropicError) as exc:
        await claude_gateway.get_identity_from_gateway_token(
            authorization=None, x_api_key=None, db=_FakeSession()
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_gateway_token_rejects_invalid_token(monkeypatch):
    async def fake_verify(self, token):
        return None

    monkeypatch.setattr(MCPAuthService, "verify_token", fake_verify)
    with pytest.raises(claude_gateway.AnthropicError) as exc:
        await claude_gateway.get_identity_from_gateway_token(
            authorization="Bearer ark_bogus", x_api_key=None, db=_FakeSession()
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_gateway_token_accepts_bearer_header(monkeypatch):
    captured = {}

    async def fake_verify(self, token):
        captured["token"] = token
        return _identity()

    monkeypatch.setattr(MCPAuthService, "verify_token", fake_verify)
    identity = await claude_gateway.get_identity_from_gateway_token(
        authorization="Bearer ark_abc123", x_api_key=None, db=_FakeSession()
    )
    assert captured["token"] == "ark_abc123"
    assert identity.employee_name == "Test Employee"


@pytest.mark.asyncio
async def test_gateway_token_accepts_x_api_key_header(monkeypatch):
    captured = {}

    async def fake_verify(self, token):
        captured["token"] = token
        return _identity()

    monkeypatch.setattr(MCPAuthService, "verify_token", fake_verify)
    await claude_gateway.get_identity_from_gateway_token(
        authorization=None, x_api_key="ark_abc123", db=_FakeSession()
    )
    assert captured["token"] == "ark_abc123"


# ---------------------------------------------------------------------------
# claude_gateway_enabled toggle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messages_returns_503_when_disabled(monkeypatch):
    async def disabled(self, key):
        return "false" if key == "claude_gateway_enabled" else None

    monkeypatch.setattr(ConfigService, "get", disabled)

    with pytest.raises(claude_gateway.AnthropicError) as exc:
        await claude_gateway.create_message(_messages_request(), db=_FakeSession(), _identity=_identity())
    assert exc.value.status_code == 503
    assert exc.value.error_type == "api_error"


@pytest.mark.asyncio
async def test_count_tokens_returns_503_when_disabled(monkeypatch):
    async def disabled(self, key):
        return "false" if key == "claude_gateway_enabled" else None

    monkeypatch.setattr(ConfigService, "get", disabled)

    body = claude_gateway.CountTokensRequest(model="claude-sonnet-4-5", messages=[{"role": "user", "content": "Hi"}])
    with pytest.raises(claude_gateway.AnthropicError) as exc:
        await claude_gateway.count_tokens(body, db=_FakeSession(), _identity=_identity())
    assert exc.value.status_code == 503


# ---------------------------------------------------------------------------
# Request translation + non-streaming response shape
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_messages_translates_request_and_returns_anthropic_shape(monkeypatch):
    received = {}

    class _FakeLLM:
        async def generate_with_tools(self, **kwargs):
            received.update(kwargs)
            return AssistantTurn(
                text="Hello world",
                tool_calls=[],
                finish_reason="end_turn",
                usage={"input_tokens": 10, "output_tokens": 5},
            )

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _fake_registry(_FakeLLM()))

    body = _messages_request(
        system="You are a coding assistant.",
        messages=[{"role": "user", "content": "Hi"}],
        tools=[{"name": "read_file", "description": "Read a file", "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}],
    )
    result = await claude_gateway.create_message(body, db=_FakeSession(), _identity=_identity())

    # Translation into the neutral/OpenAI-style shape generate_with_tools expects
    assert received["system"] == "You are a coding assistant."
    assert received["messages"] == [{"role": "user", "content": "Hi"}]
    assert received["tools"] == [{
        "type": "function",
        "function": {"name": "read_file", "description": "Read a file", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}}},
    }]

    # Anthropic-shaped response
    assert result["id"].startswith("msg_")
    assert result["type"] == "message"
    assert result["role"] == "assistant"
    assert result["model"] == "claude-sonnet-4-5"
    assert result["content"] == [{"type": "text", "text": "Hello world"}]
    assert result["stop_reason"] == "end_turn"
    assert result["usage"] == {"input_tokens": 10, "output_tokens": 5}


@pytest.mark.asyncio
async def test_messages_tool_use_round_trip(monkeypatch):
    class _FakeLLM:
        async def generate_with_tools(self, **kwargs):
            return AssistantTurn(
                text=None,
                tool_calls=[ToolCall(id="tc_1", name="read_file", arguments={"path": "a.txt"})],
                finish_reason="tool_use",
                usage={"input_tokens": 3, "output_tokens": 2},
            )

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _fake_registry(_FakeLLM()))

    result = await claude_gateway.create_message(_messages_request(), db=_FakeSession(), _identity=_identity())

    assert result["stop_reason"] == "tool_use"
    assert result["content"] == [{"type": "tool_use", "id": "tc_1", "name": "read_file", "input": {"path": "a.txt"}}]


@pytest.mark.asyncio
async def test_messages_no_provider_configured_returns_500(monkeypatch):
    class _FakeRegistry:
        def __init__(self, _db):
            pass

        async def get_gateway_llm(self):
            raise ValueError("llm_provider not set")

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _FakeRegistry)

    with pytest.raises(claude_gateway.AnthropicError) as exc:
        await claude_gateway.create_message(_messages_request(), db=_FakeSession(), _identity=_identity())
    assert exc.value.status_code == 500


@pytest.mark.asyncio
async def test_messages_generation_failure_returns_502(monkeypatch):
    class _FakeLLM:
        async def generate_with_tools(self, **kwargs):
            raise TimeoutError("provider timed out")

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _fake_registry(_FakeLLM()))

    with pytest.raises(claude_gateway.AnthropicError) as exc:
        await claude_gateway.create_message(_messages_request(), db=_FakeSession(), _identity=_identity())
    assert exc.value.status_code == 502


# ---------------------------------------------------------------------------
# Admin generation overrides — only applied when explicitly configured
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_overrides_win_when_configured(monkeypatch):
    configured = {"claude_gateway_temperature": "0.1", "claude_gateway_max_tokens": "256", "claude_gateway_top_p": "0.5"}

    async def fake_get(self, key):
        return configured.get(key)

    monkeypatch.setattr(ConfigService, "get", fake_get)

    received = {}

    class _FakeLLM:
        async def generate_with_tools(self, **kwargs):
            received.update(kwargs)
            return AssistantTurn(text="ok", finish_reason="end_turn")

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _fake_registry(_FakeLLM()))

    body = _messages_request(temperature=0.9, top_p=0.9, max_tokens=4096)
    await claude_gateway.create_message(body, db=_FakeSession(), _identity=_identity())

    assert received["temperature"] == 0.1
    assert received["max_tokens"] == 256
    assert received["top_p"] == 0.5


@pytest.mark.asyncio
async def test_request_values_pass_through_when_unconfigured(monkeypatch):
    received = {}

    class _FakeLLM:
        async def generate_with_tools(self, **kwargs):
            received.update(kwargs)
            return AssistantTurn(text="ok", finish_reason="end_turn")

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _fake_registry(_FakeLLM()))

    body = _messages_request(temperature=0.9, top_p=0.8, max_tokens=4096)
    await claude_gateway.create_message(body, db=_FakeSession(), _identity=_identity())

    assert received["temperature"] == 0.9
    assert received["top_p"] == 0.8
    assert received["max_tokens"] == 4096


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

def _parse_sse(raw: str) -> list[tuple[str, dict]]:
    events = []
    for block in raw.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line[len("event: "):] for line in lines if line.startswith("event: "))
        data = next(line[len("data: "):] for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


@pytest.mark.asyncio
async def test_stream_message_response_sequence_and_reassembly():
    turn = AssistantTurn(
        text="Hello world",
        tool_calls=[ToolCall(id="tc_1", name="read_file", arguments={"path": "a.txt"})],
        finish_reason="tool_use",
        usage={"input_tokens": 7, "output_tokens": 4},
    )

    chunks = [c async for c in claude_gateway._stream_message_response("claude-sonnet-4-5", turn, 7)]
    events = _parse_sse("".join(chunks))
    event_names = [e for e, _ in events]

    assert event_names == [
        "message_start",
        "content_block_start", "content_block_delta", "content_block_stop",
        "content_block_start", "content_block_delta", "content_block_stop",
        "message_delta",
        "message_stop",
    ]

    # message_start carries input usage, no content yet
    assert events[0][1]["message"]["usage"] == {"input_tokens": 7, "output_tokens": 0}
    assert events[0][1]["message"]["content"] == []

    # Reassemble text from the first block's delta
    assert events[2][1]["delta"] == {"type": "text_delta", "text": "Hello world"}

    # Reassemble tool_use input JSON from the second block's delta
    tool_start = events[4][1]["content_block"]
    assert tool_start == {"type": "tool_use", "id": "tc_1", "name": "read_file", "input": {}}
    tool_delta = events[5][1]["delta"]
    assert tool_delta["type"] == "input_json_delta"
    assert json.loads(tool_delta["partial_json"]) == {"path": "a.txt"}

    # Final stop_reason + output usage
    assert events[-2][1]["delta"]["stop_reason"] == "tool_use"
    assert events[-2][1]["usage"] == {"output_tokens": 4}


@pytest.mark.asyncio
async def test_messages_stream_true_returns_streaming_response(monkeypatch):
    class _FakeLLM:
        async def generate_with_tools(self, **kwargs):
            return AssistantTurn(text="Hi there", finish_reason="end_turn", usage={"input_tokens": 1, "output_tokens": 1})

    monkeypatch.setattr(claude_gateway, "ProviderRegistry", _fake_registry(_FakeLLM()))

    body = _messages_request(stream=True)
    result = await claude_gateway.create_message(body, db=_FakeSession(), _identity=_identity())

    assert isinstance(result, StreamingResponse)
    assert result.media_type == "text/event-stream"


# ---------------------------------------------------------------------------
# count_tokens heuristic
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_count_tokens_heuristic_sanity():
    body = claude_gateway.CountTokensRequest(
        model="claude-sonnet-4-5",
        system="You are a helpful assistant.",
        messages=[{"role": "user", "content": "x" * 400}],
    )
    result = await claude_gateway.count_tokens(body, db=_FakeSession(), _identity=_identity())
    assert result["input_tokens"] > 90  # ~400 chars / 4, plus system text
    assert isinstance(result["input_tokens"], int)
