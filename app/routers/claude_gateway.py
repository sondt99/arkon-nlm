"""
Claude Code Gateway — Anthropic Messages API-compatible endpoint.

Lets the Claude Code CLI (or any Anthropic Messages API client) point its
ANTHROPIC_BASE_URL at Arkon instead of api.anthropic.com, so traffic routes
through whichever LLM provider Arkon has configured (Settings -> Claude Code
Gateway), governed centrally by an Arkon admin. Stateless — unlike the chat
router, the full message history is sent by the caller on every request, so
nothing is persisted here.

Authenticates with the same bearer token used for MCP/Export API
(Employee.mcp_token), accepted via either `Authorization: Bearer <token>`
(ANTHROPIC_AUTH_TOKEN) or `x-api-key: <token>` (ANTHROPIC_API_KEY) since
Claude Code's SDK uses different headers depending on which env var is set.

Endpoints:
  POST /api/claude-gateway/v1/messages               - chat completion (streaming or not)
  POST /api/claude-gateway/v1/messages/count_tokens   - best-effort token estimate

Scope (v1, see docs/API-REFERENCE.md for details): no RAG/KB injection (pure
passthrough), no image/document content blocks, no extended thinking, no
tool_choice enforcement, and streaming is synthesized from one complete
provider response rather than true token-by-token streaming.
"""

import asyncio
import json
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.agent_protocol import (
    AssistantTurn,
    anthropic_messages_to_neutral,
    anthropic_tools_to_neutral,
    assistant_turn_to_anthropic_content,
)
from app.ai.registry import ProviderRegistry
from app.database import get_db
from app.services.mcp_auth_service import MCPAuthService, ResolvedIdentity
from app.services.rate_limiter import check_token_rate_limit

router = APIRouter()


# ---------------------------------------------------------------------------
# Anthropic-shaped errors — a dedicated exception (not HTTPException) so the
# handler registered in app/main.py can emit {"type":"error","error":{...}}
# at the response root, matching what Anthropic clients (incl. Claude Code)
# parse, instead of FastAPI's default {"detail": ...} envelope.
# ---------------------------------------------------------------------------

class AnthropicError(Exception):
    def __init__(self, status_code: int, error_type: str, message: str):
        self.status_code = status_code
        self.error_type = error_type
        self.message = message


# ---------------------------------------------------------------------------
# Schemas — loosely typed to match the Anthropic Messages API wire format.
# `content`/`system` are left as Any since blocks are validated structurally
# by the converters in agent_protocol.py, not by Pydantic sub-models.
# ---------------------------------------------------------------------------

class AnthropicMessage(BaseModel):
    role: str
    content: Any


class AnthropicTool(BaseModel):
    name: str
    description: str = ""
    input_schema: dict = Field(default_factory=lambda: {"type": "object", "properties": {}})


class MessagesRequest(BaseModel):
    model: str
    max_tokens: int
    messages: list[AnthropicMessage]
    system: Optional[Any] = None
    tools: Optional[list[AnthropicTool]] = None
    tool_choice: Optional[dict] = None
    stream: bool = False
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    top_k: Optional[int] = None
    stop_sequences: Optional[list[str]] = None


class CountTokensRequest(BaseModel):
    model: str
    messages: list[AnthropicMessage]
    system: Optional[Any] = None
    tools: Optional[list[AnthropicTool]] = None


# ---------------------------------------------------------------------------
# Auth — accepts Authorization: Bearer <token> or x-api-key: <token>
# ---------------------------------------------------------------------------

async def get_identity_from_gateway_token(
    authorization: Optional[str] = Header(default=None),
    x_api_key: Optional[str] = Header(default=None, alias="x-api-key"),
    db: AsyncSession = Depends(get_db),
) -> ResolvedIdentity:
    token: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    elif x_api_key:
        token = x_api_key.strip()

    if not token:
        raise AnthropicError(401, "authentication_error", "Not authenticated")

    identity = await MCPAuthService(db).verify_token(token)
    if identity is None:
        raise AnthropicError(401, "authentication_error", "Invalid or inactive API key")
    return identity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _require_enabled(db: AsyncSession) -> None:
    from app.services.config_service import ConfigService

    flag = await ConfigService(db).get("claude_gateway_enabled")
    enabled = (flag or "true").strip().lower() not in ("false", "0", "off")
    if not enabled:
        raise AnthropicError(503, "api_error", "Claude Code Gateway is disabled by an administrator")


async def _generation_overrides(db: AsyncSession) -> dict:
    """Read admin-configured temperature/max_tokens/top_p — applied only when set,
    so an unconfigured admin never overrides what Claude Code itself requested."""
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    temperature_raw = await svc.get("claude_gateway_temperature")
    max_tokens_raw = await svc.get("claude_gateway_max_tokens")
    top_p_raw = await svc.get("claude_gateway_top_p")

    overrides: dict = {}
    if temperature_raw:
        try:
            overrides["temperature"] = float(temperature_raw)
        except ValueError:
            pass
    if max_tokens_raw:
        try:
            overrides["max_tokens"] = int(max_tokens_raw)
        except ValueError:
            pass
    if top_p_raw:
        try:
            overrides["top_p"] = float(top_p_raw)
        except ValueError:
            pass
    return overrides


def _normalize_system(system: Any) -> Optional[str]:
    if system is None:
        return None
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("text"))
    return None


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _estimate_input_tokens(system_text: Optional[str], neutral_messages: list[dict]) -> int:
    total_chars = len(system_text or "")
    for m in neutral_messages:
        content = m.get("content")
        if isinstance(content, str):
            total_chars += len(content)
    return max(1, total_chars // 4)


def _resolve_usage(turn: AssistantTurn, input_tokens_estimate: int) -> dict:
    if turn.usage:
        return turn.usage
    return {"input_tokens": input_tokens_estimate, "output_tokens": _estimate_tokens(turn.text or "")}


def _build_message_response(model: str, turn: AssistantTurn, input_tokens_estimate: int) -> dict:
    return {
        "id": f"msg_{uuid.uuid4().hex}",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": assistant_turn_to_anthropic_content(turn),
        "stop_reason": turn.finish_reason or "end_turn",
        "stop_sequence": None,
        "usage": _resolve_usage(turn, input_tokens_estimate),
    }


async def _stream_message_response(model: str, turn: AssistantTurn, input_tokens_estimate: int):
    """Synthesize a spec-correct Anthropic SSE stream from one complete
    AssistantTurn (Phase 1 — see module docstring for the streaming scope note).
    """
    msg_id = f"msg_{uuid.uuid4().hex}"
    usage = _resolve_usage(turn, input_tokens_estimate)
    content_blocks = assistant_turn_to_anthropic_content(turn)

    def sse(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    yield sse("message_start", {
        "type": "message_start",
        "message": {
            "id": msg_id,
            "type": "message",
            "role": "assistant",
            "content": [],
            "model": model,
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {"input_tokens": usage["input_tokens"], "output_tokens": 0},
        },
    })

    for index, block in enumerate(content_blocks):
        if block["type"] == "text":
            yield sse("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "text", "text": ""},
            })
            yield sse("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "text_delta", "text": block["text"]},
            })
        else:  # tool_use
            yield sse("content_block_start", {
                "type": "content_block_start", "index": index,
                "content_block": {"type": "tool_use", "id": block["id"], "name": block["name"], "input": {}},
            })
            yield sse("content_block_delta", {
                "type": "content_block_delta", "index": index,
                "delta": {"type": "input_json_delta", "partial_json": json.dumps(block["input"])},
            })
        yield sse("content_block_stop", {"type": "content_block_stop", "index": index})

    yield sse("message_delta", {
        "type": "message_delta",
        "delta": {"stop_reason": turn.finish_reason or "end_turn", "stop_sequence": None},
        "usage": {"output_tokens": usage["output_tokens"]},
    })
    yield sse("message_stop", {"type": "message_stop"})


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@router.post("/claude-gateway/v1/messages")
async def create_message(
    body: MessagesRequest,
    db: AsyncSession = Depends(get_db),
    _identity: ResolvedIdentity = Depends(get_identity_from_gateway_token),
):
    await _require_enabled(db)
    await check_token_rate_limit(_identity.employee_id, "gateway_messages", max_requests=30, window_seconds=60)

    try:
        system_text = _normalize_system(body.system)
        neutral_messages = anthropic_messages_to_neutral([m.model_dump() for m in body.messages])
        neutral_tools = anthropic_tools_to_neutral([t.model_dump() for t in body.tools]) if body.tools else []
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AnthropicError(400, "invalid_request_error", "Malformed message content blocks") from exc

    overrides = await _generation_overrides(db)
    temperature = overrides.get("temperature", body.temperature if body.temperature is not None else 0.2)
    top_p = overrides.get("top_p", body.top_p)
    max_tokens = overrides.get("max_tokens", body.max_tokens)

    try:
        registry = ProviderRegistry(db)
        llm = await registry.get_gateway_llm()
    except ValueError as exc:
        logger.warning("Claude Code Gateway provider not configured: {}", exc)
        raise AnthropicError(
            500, "api_error",
            "No LLM provider configured for the Claude Code Gateway — ask an administrator to configure one in Settings",
        ) from exc

    from app.config import settings

    try:
        turn = await asyncio.wait_for(
            llm.generate_with_tools(
                messages=neutral_messages,
                tools=neutral_tools,
                system=system_text,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            ),
            timeout=settings.chat_generation_timeout,
        )
    except asyncio.TimeoutError as exc:
        logger.warning("Claude Code Gateway generation timed out after {}s", settings.chat_generation_timeout)
        raise AnthropicError(
            504, "api_error",
            f"Generation timed out after {settings.chat_generation_timeout}s — the configured provider is slow or unreachable",
        ) from exc
    except Exception as exc:
        logger.exception("Claude Code Gateway generation failed")
        raise AnthropicError(502, "api_error", "Generation failed — the configured provider returned an error") from exc

    input_tokens_estimate = _estimate_input_tokens(system_text, neutral_messages)

    if body.stream:
        return StreamingResponse(
            _stream_message_response(body.model, turn, input_tokens_estimate),
            media_type="text/event-stream",
        )
    return _build_message_response(body.model, turn, input_tokens_estimate)


@router.post("/claude-gateway/v1/messages/count_tokens")
async def count_tokens(
    body: CountTokensRequest,
    db: AsyncSession = Depends(get_db),
    _identity: ResolvedIdentity = Depends(get_identity_from_gateway_token),
):
    await _require_enabled(db)
    await check_token_rate_limit(_identity.employee_id, "gateway_count_tokens", max_requests=120, window_seconds=60)

    try:
        system_text = _normalize_system(body.system)
        neutral_messages = anthropic_messages_to_neutral([m.model_dump() for m in body.messages])
        tools_chars = sum(len(json.dumps(t.model_dump())) for t in (body.tools or []))
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        raise AnthropicError(400, "invalid_request_error", "Malformed message content blocks") from exc
    return {"input_tokens": _estimate_input_tokens(system_text, neutral_messages) + max(0, tools_chars // 4)}
