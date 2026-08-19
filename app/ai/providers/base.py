"""
Abstract base classes for AI providers.

Every provider (Google, OpenAI, Anthropic, Ollama…) implements these
interfaces so the rest of the codebase never imports a specific SDK.
"""

import secrets
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Optional, Union

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
