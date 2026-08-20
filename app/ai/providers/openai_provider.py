"""
OpenAI provider — embedding, LLM, and vision.

Supports:
  - Embedding: text-embedding-3-small, text-embedding-3-large, text-embedding-ada-002
  - LLM: gpt-4o, gpt-4o-mini, gpt-3.5-turbo, etc.
  - Vision: gpt-4o (multimodal)

Also works with any OpenAI-compatible API (Azure, Together, Groq, etc.)
by setting a custom base_url.
"""

import asyncio
import base64
import json
from typing import Optional

from loguru import logger

from app.ai.agent_protocol import (
    AssistantTurn,
    ToolCall,
    neutral_to_openai_messages,
)
from app.ai.providers.base import (
    EmbeddingProvider,
    LLMGeneration,
    LLMProvider,
    ProviderConfig,
    VisionProvider,
)


class OpenAIEmbedding(EmbeddingProvider):
    """OpenAI embedding provider."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,  # None = default OpenAI
            )
        return self._client

    async def embed(self, text: str) -> list[float]:
        kwargs: dict = {
            "model": self.config.model_id,
            "input": text,
        }
        # text-embedding-3-* supports custom dimensions
        if self.config.dimensions:
            kwargs["dimensions"] = self.dimensions

        response = await self.client.embeddings.create(**kwargs)
        return response.data[0].embedding

    async def embed_batch(
        self, texts: list[str], concurrency: int = 5
    ) -> list[list[float]]:
        # OpenAI supports batch input natively (up to 2048 items)
        # Split into batches of 100 for safety
        batch_size = 100
        all_embeddings: list[list[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            kwargs = {
                "model": self.config.model_id,
                "input": batch,
            }
            if self.config.dimensions:
                kwargs["dimensions"] = self.dimensions

            response = await self.client.embeddings.create(**kwargs)
            # Sort by index to maintain order
            sorted_data = sorted(response.data, key=lambda x: x.index)
            all_embeddings.extend([d.embedding for d in sorted_data])

        logger.debug(f"OpenAI: embedded {len(texts)} texts in batches of {batch_size}")
        return all_embeddings

    async def test_connection(self) -> tuple[bool, str]:
        try:
            result = await self.embed("test connection")
            dim = len(result)
            return True, f"OK — model={self.config.model_id}, dimensions={dim}"
        except Exception as e:
            return False, f"OpenAI embedding error: {e}"


class OpenAILLM(LLMProvider):
    """OpenAI LLM provider."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
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
        result = await self.generate_detailed(
            prompt,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )
        return result.text

    async def generate_detailed(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        top_p: Optional[float] = None,
    ) -> LLMGeneration:
        """generate() plus stop reason, so MRP merge can reject a truncated body."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        kwargs: dict = {
            "model": self.config.model_id,
            "messages": messages,
            "temperature": temperature,
            # Omniroute (and some other proxies) stream SSE unless this is
            # explicit — the SDK then fails to parse a JSON completion.
            "stream": False,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p

        response = await self.client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        text = choice.message.content or ""
        reason_map = {"stop": "end_turn", "length": "max_tokens", "tool_calls": "tool_use"}
        stop_reason = reason_map.get(choice.finish_reason or "", None)
        usage = None
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }
        return LLMGeneration(text=text, stop_reason=stop_reason, usage=usage)

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        top_p: Optional[float] = None,
    ) -> AssistantTurn:
        openai_messages = []
        if system:
            openai_messages.append({"role": "system", "content": system})
        openai_messages.extend(neutral_to_openai_messages(messages))

        kwargs: dict = {
            "model": self.config.model_id,
            "messages": openai_messages,
            "tools": tools,
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if top_p is not None:
            kwargs["top_p"] = top_p

        response = await self.client.chat.completions.create(**kwargs)

        choice = response.choices[0]
        message = choice.message
        text = message.content
        tool_calls: list[ToolCall] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                args: dict = {}
                if tc.function.arguments:
                    try:
                        args = json.loads(tc.function.arguments)
                    except Exception:
                        logger.warning(
                            "OpenAI tool call {} ({}) had malformed JSON arguments: {!r}",
                            tc.id, tc.function.name, tc.function.arguments,
                        )
                tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=args))

        reason_map = {"stop": "end_turn", "tool_calls": "tool_use", "length": "max_tokens"}
        finish_reason = reason_map.get(choice.finish_reason or "stop", "end_turn")

        usage = None
        if response.usage:
            usage = {
                "input_tokens": response.usage.prompt_tokens,
                "output_tokens": response.usage.completion_tokens,
            }

        return AssistantTurn(
            text=text or None,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

    async def test_connection(self) -> tuple[bool, str]:
        try:
            # Reasoning models (GLM-5.3 via Omniroute) spend the first tens of
            # tokens on hidden thinking; 10 used to return an empty body and
            # still look like success.
            result = await self.generate("Say 'OK'", max_tokens=256, temperature=0)
            text = (result or "").strip()
            if not text:
                return False, (
                    f"Empty response from model={self.config.model_id}. "
                    "Try a larger max_tokens or a non-reasoning model."
                )
            return True, f"OK — model={self.config.model_id}, response='{text[:50]}'"
        except Exception as e:
            return False, f"OpenAI LLM error: {e}"


class OpenAIVision(VisionProvider):
    """OpenAI Vision provider (GPT-4o multimodal)."""

    def __init__(self, config: ProviderConfig):
        super().__init__(config)
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import openai
            self._client = openai.AsyncOpenAI(
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

        b64_image = base64.b64encode(image_data).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64_image}"

        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                response = await self.client.chat.completions.create(
                    model=self.config.model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {"url": data_url, "detail": "low"},
                                },
                            ],
                        }
                    ],
                    temperature=0.2,
                    stream=False,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                logger.warning(f"OpenAI Vision attempt {attempt + 1} failed: {e}")
                last_exc = e
                if attempt < 2:
                    await asyncio.sleep(2)
        # RAISE, do not `return ""`. An empty caption is indistinguishable from a successful
        # one to every consumer: `caption_images_task` counted it as captioned, so its
        # `if captioned == 0: raise` could never fire, and the resume filter
        # `SourceImage.caption.is_(None)` stopped matching because "" is not NULL — so the
        # retry found nothing to do and the images stayed permanently uncaptioned. A total
        # vision outage looked like a completed job with no way back.
        # AnthropicVision has always propagated; this brings the other two in line.
        raise RuntimeError(
            f"OpenAI Vision failed after 3 attempts: {last_exc}"
        ) from last_exc

    async def test_connection(self) -> tuple[bool, str]:
        try:
            # Quick test with a tiny 1x1 PNG
            tiny_png = (
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
                b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
                b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
                b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
            )
            await self.analyze_image(tiny_png, "image/png", "What is this?")
            return True, f"OK — model={self.config.model_id}"
        except Exception as e:
            return False, f"OpenAI Vision error: {e}"
