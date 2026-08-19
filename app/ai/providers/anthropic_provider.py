"""
Anthropic provider — LLM and Vision (Claude 3+ models support image input).

Supports: Claude Sonnet, Claude Haiku, Claude Opus, etc.
"""

import base64
from typing import Optional

from app.ai.agent_protocol import (
    AssistantTurn,
    ToolCall,
    neutral_to_anthropic_messages,
    openai_tools_to_anthropic,
)
from app.ai.providers.base import (
    LLMGeneration,
    LLMOutputTruncated,
    LLMProvider,
    ProviderConfig,
    VisionProvider,
)

# Models that reject temperature / top_p / top_k (400 invalid_request_error).
_NO_SAMPLING_MARKERS = (
    "opus-4-8",
    "opus-4-7",
    "sonnet-5",
    "fable-5",
    "claude-4.7",
    "claude-4.8",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-sonnet-5",
)


def model_accepts_sampling(model_id: str) -> bool:
    """Return False for current-generation Claude IDs that reject sampling params."""
    mid = (model_id or "").lower()
    return not any(marker in mid for marker in _NO_SAMPLING_MARKERS)


def _apply_sampling(kwargs: dict, model_id: str, temperature: float, top_p: Optional[float]) -> None:
    if not model_accepts_sampling(model_id):
        return
    kwargs["temperature"] = temperature
    if top_p is not None:
        kwargs["top_p"] = top_p


# The SDK's own defaults are 2 retries and a 10-minute request timeout. Every caller wraps
# these calls in a much shorter asyncio.wait_for, which cancels the coroutine mid-backoff —
# so under a 429 with retry-after: 60 the SDK slept, the outer deadline fired, and
# _extract_with_sem recorded the chunk as permanently failed. Configuring the client
# explicitly keeps the SDK's backoff inside the caller's budget.
_CLIENT_TIMEOUT_SECONDS = 90.0
_CLIENT_MAX_RETRIES = 3


def _collect_text(response) -> str:
    """Join every text block in a response.

    `response.content[0].text` assumed the first block is text. It is a heterogeneous list
    of TextBlock / ThinkingBlock / ToolUseBlock: on models where adaptive thinking is on,
    content[0] is a ThinkingBlock and .text raises AttributeError — swallowed by callers'
    broad excepts and reported as a parse failure. Even without thinking, a response split
    across multiple text blocks (citations, refusal fallbacks) silently lost everything
    after the first.
    """
    if not response.content:
        return ""
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


def _stop_reason(response) -> Optional[str]:
    return getattr(response, "stop_reason", None)


class AnthropicLLM(LLMProvider):
    """Anthropic Claude LLM provider."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=_CLIENT_TIMEOUT_SECONDS,
                max_retries=_CLIENT_MAX_RETRIES,
            )
        return self._client

    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        top_p: Optional[float] = None,
    ) -> str:
        kwargs = {
            "model": self.config.model_id,
            "max_tokens": max_tokens or 16384,
            "messages": [{"role": "user", "content": prompt}],
        }
        _apply_sampling(kwargs, self.config.model_id, temperature, top_p)
        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)
        return _collect_text(response)

    async def generate_detailed(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        top_p: Optional[float] = None,
    ) -> LLMGeneration:
        """generate() plus the stop reason, so callers can detect truncation."""
        resolved_max = max_tokens or 16384
        kwargs = {
            "model": self.config.model_id,
            "max_tokens": resolved_max,
            "messages": [{"role": "user", "content": prompt}],
        }
        _apply_sampling(kwargs, self.config.model_id, temperature, top_p)
        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)

        # A refusal is a documented HTTP-200 outcome on current models; content is empty or
        # partial, so reading it as an answer would store a blank body.
        reason = _stop_reason(response)
        if reason == "refusal":
            raise RuntimeError(
                "The model declined this request (stop_reason=refusal). "
                "Content was not generated."
            )

        usage = None
        if getattr(response, "usage", None):
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }
        return LLMGeneration(
            text=_collect_text(response), stop_reason=reason, usage=usage
        )

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        top_p: Optional[float] = None,
    ) -> AssistantTurn:
        anthropic_messages = neutral_to_anthropic_messages(messages)
        anthropic_tools = openai_tools_to_anthropic(tools)

        kwargs: dict = {
            "model": self.config.model_id,
            "max_tokens": max_tokens or 16384,
            "messages": anthropic_messages,
            "tools": anthropic_tools,
        }
        _apply_sampling(kwargs, self.config.model_id, temperature, top_p)
        if system:
            kwargs["system"] = system

        response = await self.client.messages.create(**kwargs)

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                args = block.input if isinstance(block.input, dict) else {}
                tool_calls.append(ToolCall(id=block.id, name=block.name, arguments=args))

        reason_map = {"end_turn": "end_turn", "tool_use": "tool_use", "max_tokens": "max_tokens"}
        finish_reason = reason_map.get(response.stop_reason or "end_turn", "end_turn")

        usage = None
        if response.usage:
            usage = {
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            }

        return AssistantTurn(
            text="\n".join(text_parts) or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def test_connection(self) -> tuple[bool, str]:
        try:
            result = await self.generate("Say 'OK'", max_tokens=10, temperature=0)
            return True, f"OK — model={self.config.model_id}, response='{result[:50]}'"
        except Exception as e:
            return False, f"Anthropic error: {e}"


class AnthropicVision(VisionProvider):
    """Anthropic Claude vision provider (Claude 3+ supports image input)."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.AsyncAnthropic(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=_CLIENT_TIMEOUT_SECONDS,
                max_retries=_CLIENT_MAX_RETRIES,
            )
        return self._client

    async def analyze_image(
        self,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        prompt: Optional[str] = None,
    ) -> str:
        if not prompt:
            prompt = (
                "Describe this image in detail. "
                "If it's a diagram, flowchart, or table, explain the meaning and steps. "
                "If it's a regular image, provide a concise description."
            )
        b64 = base64.standard_b64encode(image_data).decode()
        # 1024 tokens could not satisfy the prompt above, which explicitly asks the model to
        # "explain the meaning and steps" of diagrams, flowcharts, and tables. A multi-step
        # flowchart was described up to the cap and cut off mid-step, and the partial
        # caption was then stored as the image's searchable text and embedded — this is the
        # only path by which a diagram's content ever reaches the wiki.
        response = await self.client.messages.create(
            model=self.config.model_id,
            max_tokens=4096,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        caption = _collect_text(response)
        if _stop_reason(response) == "max_tokens":
            # Surface it rather than storing a caption cut off mid-sentence as if complete.
            raise LLMOutputTruncated(caption, max_tokens=4096)
        return caption

    async def test_connection(self) -> tuple[bool, str]:
        try:
            tiny_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
                b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
                b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            await self.analyze_image(tiny_png, "image/png", "What is this?")
            return True, f"OK — model={self.config.model_id}"
        except Exception as e:
            return False, f"Anthropic Vision error: {e}"
