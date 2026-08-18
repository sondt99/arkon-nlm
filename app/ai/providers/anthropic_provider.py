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
from app.ai.providers.base import LLMProvider, ProviderConfig, VisionProvider


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
        return response.content[0].text if response.content else ""

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
        response = await self.client.messages.create(
            model=self.config.model_id,
            max_tokens=1024,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {"type": "base64", "media_type": mime_type, "data": b64}},
                    {"type": "text", "text": prompt},
                ],
            }],
        )
        return response.content[0].text if response.content else ""

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
