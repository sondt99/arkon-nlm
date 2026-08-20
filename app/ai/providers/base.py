"""
Abstract base classes for AI providers.

Every provider (Google, OpenAI, Anthropic, Ollama…) implements these
interfaces so the rest of the codebase never imports a specific SDK.
"""

import json
import re
import secrets
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:
    from app.ai.agent_protocol import AssistantTurn


# ---------------------------------------------------------------------------
# Untrusted-data envelope
# ---------------------------------------------------------------------------
#
# Every prompt that carries uploaded text has to state where that text begins and ends, and
# the delimiter has to be unguessable: with a fixed tag name a document can simply write the
# closing form and have everything after it read as operator instructions. Shared here so the
# ingestion prompts and the chat prompt fence untrusted text the same way.

UNTRUSTED_DOCUMENT_TAG = "untrusted_document"
UNTRUSTED_KB_CONTEXT_TAG = "untrusted_kb_context"
UNTRUSTED_HINTS_TAG = "untrusted_category_hints"
UNTRUSTED_CONVERSATION_TAG = "untrusted_conversation"

# The compiler, writer and planner prompts each carry several envelopes at once (document
# text, existing pages, category hints). Passing this tuple instead of one tag strips every
# marker the codebase uses from every block, so a poisoned page body cannot emit the closing
# form of a *neighbouring* envelope and leave the model guessing which block just ended.
UNTRUSTED_TAGS: tuple[str, ...] = (
    UNTRUSTED_DOCUMENT_TAG,
    UNTRUSTED_KB_CONTEXT_TAG,
    UNTRUSTED_HINTS_TAG,
    UNTRUSTED_CONVERSATION_TAG,
)


def new_envelope_nonce() -> str:
    """Return a per-call random suffix for an untrusted-data delimiter."""
    return secrets.token_hex(4)


def strip_envelope_markers(
    text: str, tag: Union[str, Iterable[str]], nonce: str
) -> str:
    """Remove anything in `text` that could open or close an envelope early.

    `tag` accepts one tag name or several (see UNTRUSTED_TAGS).
    """
    tags = (tag,) if isinstance(tag, str) else tuple(tag)
    for name in tags:
        # Both the nonced form and a generic guess at it.
        for marker in (
            f"</{name}_{nonce}>",
            f"<{name}_{nonce}>",
            f"</{name}",
            f"<{name}",
        ):
            text = text.replace(marker, "[removed]")
    return text


def flatten_untrusted_metadata(
    value: str, tag: Union[str, Iterable[str]], nonce: str, limit: int = 200
) -> str:
    """Collapse a document-derived label to one harmless line.

    Values like a section heading are attacker-controlled but have to be rendered outside
    the envelope to be useful as metadata. Flattening the whitespace keeps a heading such as
    `# Disregard the schema` from forging a new markdown section of its own, and the cap
    stops a 5,000-character "heading" from dominating the prompt.
    """
    flattened = " ".join(strip_envelope_markers(value or "", tag, nonce).split())
    if len(flattened) > limit:
        flattened = flattened[:limit] + "…"
    return flattened or "(unknown)"


# ---------------------------------------------------------------------------
# LLM JSON output
# ---------------------------------------------------------------------------
#
# Shared here for the same reason as the envelope helpers above: MAP, REDUCE and VERIFY all
# ask a model for JSON and all have to survive the same provider habits — a ``` fence, a
# leading "Sure, here it is:", a trailing remark, or a reply cut off mid-object.
#
# Two call sites used `raw.strip("```json").strip("```")` for this. str.strip takes a *set of
# characters*, not a substring, so that expression removes any leading or trailing backtick,
# `j`, `s`, `o` or `n` in any order; it only appears to work on the common fenced form
# because the newline after the fence stops the scan before it reaches the payload. Neither
# site had any fallback, so a reply with prose around the JSON went to `except Exception` and
# was reported as "no conflict" / "nothing to merge" — the failure this helper exists to end.

_JSON_FENCE_OPEN = re.compile(r"\A```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n?")
_JSON_FENCE_CLOSE = re.compile(r"\r?\n?[ \t]*```[ \t]*\Z")


def strip_json_fence(raw: str) -> str:
    """Remove one leading and one trailing markdown code fence, anchored to the ends."""
    text = (raw or "").strip()
    text = _JSON_FENCE_OPEN.sub("", text)
    return _JSON_FENCE_CLOSE.sub("", text).strip()


def parse_json_response(raw: str) -> Any:
    """Parse a model's JSON reply. Raises ValueError when no JSON value is recoverable.

    json.JSONDecodeError is a ValueError, so a caller that only wants "did this parse"
    can catch ValueError for both outcomes.

    A reply truncated part-way through a nested value is *not* recoverable and raises. That
    is deliberate: the alternative is handing back a partial object that looks complete.
    """
    text = strip_json_fence(raw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to the outermost container, which recovers the failure that actually fires:
    # a model that wrapped valid JSON in "Sure, here it is:" or a closing remark.
    #
    # Only the container the reply *opens with* is considered. Trying `{...}` and then
    # `[...]` would, on a truncated object, return an inner array as though it were the
    # top-level value — and reducer's MAYBE resolution branches on exactly that
    # (`isinstance(parsed, list)`), so a wrong type is worse than no value.
    candidates = [(pos, pair) for pos, pair in (
        (text.find("{"), ("{", "}")),
        (text.find("["), ("[", "]")),
    ) if pos >= 0]
    if candidates:
        start, (_opener, closer) = min(candidates)
        end = text.rfind(closer)
        if end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass

    raise ValueError(f"No JSON value in model reply: {text[:200]!r}")


# ---------------------------------------------------------------------------
# Provider enum — add new providers here
# ---------------------------------------------------------------------------

class ProviderType(str, Enum):
    GOOGLE = "google"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OLLAMA = "ollama"
    VOYAGE = "voyage"
    COHERE = "cohere"
    NINEROUTER = "ninerouter"  # OpenAI-compatible AI routing proxy
    OMNIROUTE = "omniroute"  # OpenAI-compatible AI routing proxy (key + base URL + model)


# Chat/embeddings/vision that speak the OpenAI HTTP surface.
OPENAI_COMPATIBLE: frozenset[ProviderType] = frozenset({
    ProviderType.OPENAI,
    ProviderType.OLLAMA,
    ProviderType.NINEROUTER,
    ProviderType.OMNIROUTE,
})


# ---------------------------------------------------------------------------
# Runtime config loaded from DB
# ---------------------------------------------------------------------------

@dataclass
class ProviderConfig:
    """
    Configuration for a single provider instance.
    Loaded from DB by ProviderRegistry at runtime.
    """
    provider: ProviderType
    api_key: str = ""
    model_id: str = ""
    base_url: Optional[str] = None      # For Ollama, Azure, proxies
    dimensions: Optional[int] = None    # Embedding output dimensions
    extra: dict = field(default_factory=dict)  # Provider-specific params


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

class EmbeddingProvider(ABC):
    """Generate vector embeddings for text."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def embed(self, text: str) -> list[float]:
        """Embed a single text string."""
        ...

    @abstractmethod
    async def embed_batch(
        self, texts: list[str], concurrency: int = 5
    ) -> list[list[float]]:
        """Embed multiple texts with concurrency control."""
        ...

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        """
        Test if the provider is reachable and credentials are valid.
        Returns (success, human-readable message).
        """
        ...

    @property
    def dimensions(self) -> int:
        """Output vector dimensions."""
        return self.config.dimensions or 768


# ---------------------------------------------------------------------------
# LLM (text generation)
# ---------------------------------------------------------------------------

class LLMOutputTruncated(RuntimeError):
    """Raised when a provider stopped because it hit max_tokens.

    Callers that commit model output to the knowledge base must treat this as a hard
    failure rather than accepting a partial document — silently storing a body cut off
    mid-sentence is worse than failing the step and retrying with a larger budget.
    """

    def __init__(self, partial: str, max_tokens: Optional[int] = None):
        self.partial = partial
        self.max_tokens = max_tokens
        super().__init__(
            f"Model output was truncated at max_tokens={max_tokens}; "
            f"got {len(partial)} characters."
        )


@dataclass
class LLMGeneration:
    """A completion plus why generation stopped."""

    text: str
    # "end_turn" | "max_tokens" | "tool_use" | "refusal" | None (unknown)
    stop_reason: Optional[str] = None
    usage: Optional[dict] = None

    @property
    def truncated(self) -> bool:
        return self.stop_reason == "max_tokens"

class LLMProvider(ABC):
    """Generate text — used for summarization, webhook gateway, etc."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        top_p: Optional[float] = None,
    ) -> str:
        """Generate a text completion."""
        ...

    async def generate_detailed(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        top_p: Optional[float] = None,
    ) -> "LLMGeneration":
        """Like generate(), but also reports why the model stopped.

        stop_reason was previously computed and thrown away, so a response truncated at
        max_tokens was committed as final content — see merger.py, where a merge cut off
        mid-document still satisfied a char-length shrink guard.

        Non-abstract with a conservative default so the other providers keep working: the
        default reports stop_reason=None, meaning "unknown". Callers must read that as
        "cannot rule out truncation", not as "not truncated". Providers able to report it
        should override.
        """
        text = await self.generate(
            prompt, system=system, max_tokens=max_tokens,
            temperature=temperature, top_p=top_p,
        )
        return LLMGeneration(text=text, stop_reason=None)

    def cacheable_prefix_min_tokens(self) -> Optional[int]:
        """Smallest prompt prefix this provider will cache, or None if it caches nothing.

        A cache breakpoint on a shorter prefix is silently ignored — no error, no cache
        entry, and the caller has still paid the cache-write premium on the tokens that
        *were* eligible. Callers therefore gate on this number instead of marking every
        prompt and hoping. Returning None here is what keeps the non-Anthropic providers
        on exactly their old code path.
        """
        return None

    async def generate_cached(
        self,
        cacheable_prefix: str,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.7,
        top_p: Optional[float] = None,
    ) -> str:
        """generate() on `cacheable_prefix + prompt`, with the prefix offered for caching.

        `cacheable_prefix` must be the invariant head of the prompt — byte-identical across
        every call meant to share one cache entry — and the caller must have checked it
        against cacheable_prefix_min_tokens(). No separator is inserted: the caller owns the
        exact bytes on both sides of the boundary.

        The default is deliberately price-neutral and concatenates, so a provider without
        prompt caching receives precisely what generate() would have received.
        """
        return await self.generate(
            cacheable_prefix + prompt, system=system, max_tokens=max_tokens,
            temperature=temperature, top_p=top_p,
        )

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        system: Optional[str] = None,
        max_tokens: Optional[int] = None,
        temperature: float = 0.2,
        top_p: Optional[float] = None,
    ) -> "AssistantTurn":
        """
        Multi-turn tool-calling. Messages use neutral format from agent_protocol.
        Returns AssistantTurn with tool_calls (if any) and finish_reason.
        Override in providers that support tool calling.

        A neutral user message may carry a "cache_prefix" key; see
        agent_protocol.neutral_to_anthropic_messages. Converters that cannot cache
        concatenate it, so the flag is inert rather than lossy on those providers.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support tool calling. "
            "Configure a provider that supports function calling (Anthropic, OpenAI, Google)."
        )

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        ...


# ---------------------------------------------------------------------------
# Vision (image analysis)
# ---------------------------------------------------------------------------

class VisionProvider(ABC):
    """Analyze images — used during document ingestion for image captioning."""

    def __init__(self, config: ProviderConfig):
        self.config = config

    @abstractmethod
    async def analyze_image(
        self,
        image_data: bytes,
        mime_type: str = "image/jpeg",
        prompt: Optional[str] = None,
    ) -> str:
        """Analyze an image and return a text description."""
        ...

    @abstractmethod
    async def test_connection(self) -> tuple[bool, str]:
        ...
