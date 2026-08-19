"""Provider response handling: heterogeneous content blocks and truncation."""

from types import SimpleNamespace

import pytest

from app.ai.providers.anthropic_provider import (
    AnthropicLLM,
    _collect_text,
    _stop_reason,
    model_accepts_sampling,
)
from app.ai.providers.base import LLMGeneration, ProviderConfig, ProviderType


def _block(kind, **kw):
    return SimpleNamespace(type=kind, **kw)


def test_thinking_block_first_does_not_break_text_extraction():
    """content[0].text raised AttributeError when the first block was a ThinkingBlock.

    On current models adaptive thinking can be on, so content[0] is a ThinkingBlock. The old
    code raised, callers' broad excepts swallowed it, and the failure was reported as a
    parse error.
    """
    response = SimpleNamespace(
        content=[
            _block("thinking", thinking="deliberating..."),
            _block("text", text="the real answer"),
        ]
    )
    assert _collect_text(response) == "the real answer"


def test_multiple_text_blocks_are_all_kept():
    """Reading only content[0] silently dropped everything after the first text block.

    Citations and refusal fallbacks split output across blocks, so a wiki page could be
    committed with its tail missing.
    """
    response = SimpleNamespace(
        content=[_block("text", text="part one "), _block("text", text="part two")]
    )
    assert _collect_text(response) == "part one part two"


def test_tool_use_blocks_are_not_treated_as_text():
    response = SimpleNamespace(
        content=[
            _block("text", text="answer"),
            _block("tool_use", id="t1", name="x", input={}),
        ]
    )
    assert _collect_text(response) == "answer"


def test_empty_content_is_empty_string():
    assert _collect_text(SimpleNamespace(content=[])) == ""


def test_stop_reason_is_readable():
    assert _stop_reason(SimpleNamespace(stop_reason="max_tokens")) == "max_tokens"
    assert _stop_reason(SimpleNamespace()) is None


# --------------------------------------------------------------------------- #
# LLMGeneration / truncation contract
# --------------------------------------------------------------------------- #

def test_generation_reports_truncation():
    assert LLMGeneration(text="x", stop_reason="max_tokens").truncated is True
    assert LLMGeneration(text="x", stop_reason="end_turn").truncated is False


def test_unknown_stop_reason_is_not_reported_as_untruncated():
    """stop_reason=None means "unknown", and must not be read as "definitely fine".

    The base-class default returns None for providers that cannot report it. `truncated`
    being False there is correct for the property's contract, but callers that commit output
    to the KB should treat None as "cannot rule it out" — which is why the base class says
    so in its docstring.
    """
    gen = LLMGeneration(text="x", stop_reason=None)
    assert gen.stop_reason is None
    assert gen.truncated is False


@pytest.mark.asyncio
async def test_generate_detailed_surfaces_stop_reason(monkeypatch):
    cfg = ProviderConfig(provider="anthropic", model_id="claude-opus-4-8", api_key="k")
    llm = AnthropicLLM(cfg)

    class _Messages:
        async def create(self, **kwargs):
            # Sampling params must be absent for this model id.
            assert "temperature" not in kwargs
            assert "top_p" not in kwargs
            return SimpleNamespace(
                content=[_block("text", text="truncated body")],
                stop_reason="max_tokens",
                usage=SimpleNamespace(input_tokens=10, output_tokens=20),
            )

    monkeypatch.setattr(
        AnthropicLLM, "client", property(lambda self: SimpleNamespace(messages=_Messages()))
    )
    result = await llm.generate_detailed("hello")
    assert result.truncated is True
    assert result.text == "truncated body"
    assert result.usage == {"input_tokens": 10, "output_tokens": 20}


@pytest.mark.asyncio
async def test_refusal_is_raised_not_returned_as_empty_text(monkeypatch):
    """A refusal is a documented HTTP-200 outcome; reading content would store a blank body."""
    cfg = ProviderConfig(provider="anthropic", model_id="claude-opus-4-8", api_key="k")
    llm = AnthropicLLM(cfg)

    class _Messages:
        async def create(self, **kwargs):
            return SimpleNamespace(content=[], stop_reason="refusal", usage=None)

    monkeypatch.setattr(
        AnthropicLLM, "client", property(lambda self: SimpleNamespace(messages=_Messages()))
    )
    with pytest.raises(RuntimeError, match="declined"):
        await llm.generate_detailed("hello")


def test_sampling_gate_covers_current_model_ids():
    """Sampling params are rejected with 400 on the current generation."""
    for mid in ("claude-opus-4-8", "claude-opus-4-7", "claude-sonnet-5", "claude-fable-5"):
        assert model_accepts_sampling(mid) is False, mid
    # Still accepted on these.
    for mid in ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6"):
        assert model_accepts_sampling(mid) is True, mid


# --------------------------------------------------------------------------- #
# Vision captions (#90 item 5)
# --------------------------------------------------------------------------- #

def _vision(response):
    from app.ai.providers.anthropic_provider import AnthropicVision

    vision = AnthropicVision(ProviderConfig(
        provider=ProviderType.ANTHROPIC, model_id="claude-opus-4-8",
    ))

    class _Messages:
        def __init__(self):
            self.kwargs = {}

        async def create(self, **kwargs):
            self.kwargs.update(kwargs)
            return response

    messages = _Messages()
    vision._client = SimpleNamespace(messages=messages)
    return vision, messages


@pytest.mark.asyncio
async def test_a_caption_cut_off_at_the_cap_is_raised_not_stored():
    """analyze_image is the only path a diagram's content ever takes into the wiki.

    The caption becomes the image's searchable text and is embedded, so a description that
    stops mid-step is stored as if it were the whole diagram. The cap was 1024 tokens
    against a prompt that asks the model to "explain the meaning and steps".
    """
    from app.ai.providers.base import LLMOutputTruncated

    response = SimpleNamespace(
        content=[_block("text", text="Step 1 do X. Step 2 do")],
        stop_reason="max_tokens",
    )
    vision, messages = _vision(response)

    with pytest.raises(LLMOutputTruncated) as raised:
        await vision.analyze_image(b"\x89PNG", "image/png")

    assert raised.value.partial == "Step 1 do X. Step 2 do"
    assert messages.kwargs["max_tokens"] >= 4096


@pytest.mark.asyncio
async def test_a_complete_caption_is_returned():
    response = SimpleNamespace(
        content=[_block("text", text="A flowchart with three steps.")],
        stop_reason="end_turn",
    )
    vision, _ = _vision(response)

    assert await vision.analyze_image(b"\x89PNG", "image/png") == (
        "A flowchart with three steps."
    )


@pytest.mark.asyncio
async def test_a_caption_split_across_blocks_keeps_every_block():
    """content[0].text lost everything after the first block on the vision path too."""
    response = SimpleNamespace(
        content=[
            _block("thinking", thinking="reading the diagram"),
            _block("text", text="Step 1. "),
            _block("text", text="Step 2."),
        ],
        stop_reason="end_turn",
    )
    vision, _ = _vision(response)

    assert await vision.analyze_image(b"\x89PNG", "image/png") == "Step 1. Step 2."


# --------------------------------------------------------------------------- #
# Usage reporting
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_cache_token_counts_travel_with_the_rest_of_the_usage(monkeypatch):
    """cache_read_input_tokens is the only evidence a breakpoint took effect."""
    response = SimpleNamespace(
        content=[_block("text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(
            input_tokens=12, output_tokens=3,
            cache_read_input_tokens=8192, cache_creation_input_tokens=0,
        ),
    )

    class _Messages:
        async def create(self, **_kwargs):
            return response

    llm = AnthropicLLM(ProviderConfig(
        provider=ProviderType.ANTHROPIC, model_id="claude-opus-4-8",
    ))
    monkeypatch.setattr(
        AnthropicLLM, "client",
        property(lambda self: SimpleNamespace(messages=_Messages())),
    )

    result = await llm.generate_detailed("hello")

    assert result.usage == {
        "input_tokens": 12, "output_tokens": 3,
        "cache_read_input_tokens": 8192, "cache_creation_input_tokens": 0,
    }


@pytest.mark.asyncio
async def test_usage_without_cache_fields_omits_them(monkeypatch):
    """A provider proxy that does not report caching must not gain zeroed keys."""
    response = SimpleNamespace(
        content=[_block("text", text="ok")],
        stop_reason="end_turn",
        usage=SimpleNamespace(input_tokens=12, output_tokens=3),
    )

    class _Messages:
        async def create(self, **_kwargs):
            return response

    llm = AnthropicLLM(ProviderConfig(
        provider=ProviderType.ANTHROPIC, model_id="claude-opus-4-8",
    ))
    monkeypatch.setattr(
        AnthropicLLM, "client",
        property(lambda self: SimpleNamespace(messages=_Messages())),
    )

    result = await llm.generate_detailed("hello")

    assert result.usage == {"input_tokens": 12, "output_tokens": 3}


# --------------------------------------------------------------------------- #
# writer retry-after
# --------------------------------------------------------------------------- #

def test_retry_delay_reads_the_response_header():
    """The old regex scanned str(exc) for "retry_after", which this SDK never puts there."""
    from app.ai.mrp.writer import _writer_retry_delay

    exc = Exception("rate limited")
    exc.response = SimpleNamespace(headers={"retry-after": "90"})  # type: ignore[attr-defined]
    assert _writer_retry_delay(exc, 0) == 90


def test_retry_delay_falls_back_when_no_hint():
    from app.ai.mrp.writer import WRITER_RETRY_DELAYS, _writer_retry_delay

    assert _writer_retry_delay(Exception("boom"), 0) == WRITER_RETRY_DELAYS[0]


def test_retry_delay_is_bounded():
    from app.ai.mrp.writer import _writer_retry_delay

    exc = Exception("rate limited")
    exc.response = SimpleNamespace(headers={"retry-after": "99999"})  # type: ignore[attr-defined]
    assert _writer_retry_delay(exc, 0) == 120
