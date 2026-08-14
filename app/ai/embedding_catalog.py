"""
Embedding model catalog — code-level whitelist of supported embedding models.

This is the single source of truth for which embedding models the system
supports. Admins choose from this catalog (via the settings UI); they cannot
type free-form model IDs or dimensions, which previously caused two classes of
production bugs:

  1. Misspelled model_id → API call fails or silently uses a different model.
  2. Dimension mismatch → vectors stored with wrong shape, search broken.

Adding a new model means adding an entry here AND adding a per-dimension
SQLAlchemy table + Alembic migration if its `dimension` is not already
supported. See plan: app/ai/embedding_catalog.py for context.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class EmbeddingModelSpec:
    id: str                    # canonical: "<provider>/<model_id>", e.g. "openai/text-embedding-3-small"
    provider: str              # matches ProviderType: "openai" | "google" | ...
    model_id: str              # ID sent to the provider API: "text-embedding-3-small"
    dimension: int             # output vector dim — must match a wiki_page_embeddings_<dim> table
    max_input_tokens: int      # provider's per-request token cap (used for chunking budgets)
    label: str                 # short label shown in UI
    cost_per_1m_tokens: float | None  # USD per 1M input tokens; None = unknown / free
    notes: str | None = None   # short hint for admins


# All entries here MUST have a `dimension` value that has a matching
# `wiki_page_embeddings_<dim>` table in the database. Currently supported
# dimensions: 768, 1024, 1536, 3072.
EMBEDDING_CATALOG: dict[str, EmbeddingModelSpec] = {
    # --- Google Gemini ---
    # Both Gemini embedding models support flexible output dim (128–3072).
    # We pin to 3072 (highest recommended) so the schema column type matches.
    # If you want the cheaper 1536/768 tiers, add a second spec entry — the
    # provider call already passes `output_dimensionality` from spec.dimension.
    "google/gemini-embedding-001": EmbeddingModelSpec(
        id="google/gemini-embedding-001",
        provider="google",
        model_id="gemini-embedding-001",
        dimension=3072,
        max_input_tokens=2048,
        label="Gemini Embedding 001 (3072d)",
        cost_per_1m_tokens=0.15,
        notes="Text-only. Stable since June 2025. Strong multilingual incl. Vietnamese.",
    ),
    "google/gemini-embedding-2": EmbeddingModelSpec(
        id="google/gemini-embedding-2",
        provider="google",
        model_id="gemini-embedding-2",
        dimension=3072,
        max_input_tokens=8192,
        label="Gemini Embedding 2 (3072d, multimodal)",
        cost_per_1m_tokens=0.15,
        notes="Multimodal (text, image, video, audio, PDF). 8K input window. Stable Apr 2026.",
    ),
    # --- OpenAI ---
    "openai/text-embedding-3-small": EmbeddingModelSpec(
        id="openai/text-embedding-3-small",
        provider="openai",
        model_id="text-embedding-3-small",
        dimension=1536,
        max_input_tokens=8191,
        label="OpenAI text-embedding-3-small (1536d)",
        cost_per_1m_tokens=0.02,
        notes="Best price/performance on OpenAI side.",
    ),
    "openai/text-embedding-3-large": EmbeddingModelSpec(
        id="openai/text-embedding-3-large",
        provider="openai",
        model_id="text-embedding-3-large",
        dimension=3072,
        max_input_tokens=8191,
        label="OpenAI text-embedding-3-large (3072d)",
        cost_per_1m_tokens=0.13,
        notes="Highest quality OpenAI embedding. ~6.5x cost of 3-small.",
    ),
    # --- Ollama (local) ---
    "ollama/nomic-embed-text": EmbeddingModelSpec(
        id="ollama/nomic-embed-text",
        provider="ollama",
        model_id="nomic-embed-text",
        dimension=768,
        max_input_tokens=8192,
        label="Ollama — nomic-embed-text (768d, local)",
        cost_per_1m_tokens=0.0,
        notes="Local model via Ollama. Set Base URL = http://host.docker.internal:11434/v1",
    ),
    # --- 9Router (OpenAI-compatible proxy) ---
    "ninerouter/openai/text-embedding-3-small": EmbeddingModelSpec(
        id="ninerouter/openai/text-embedding-3-small",
        provider="ninerouter",
        model_id="openai/text-embedding-3-small",
        dimension=1536,
        max_input_tokens=8191,
        label="9Router → text-embedding-3-small (1536d)",
        cost_per_1m_tokens=None,
        notes="Via 9Router proxy. Set Base URL to your 9Router endpoint.",
    ),
    "ninerouter/openai/text-embedding-3-large": EmbeddingModelSpec(
        id="ninerouter/openai/text-embedding-3-large",
        provider="ninerouter",
        model_id="openai/text-embedding-3-large",
        dimension=3072,
        max_input_tokens=8191,
        label="9Router → text-embedding-3-large (3072d)",
        cost_per_1m_tokens=None,
        notes="Via 9Router proxy. Set Base URL to your 9Router endpoint.",
    ),
}


SUPPORTED_DIMENSIONS: tuple[int, ...] = tuple(
    sorted({s.dimension for s in EMBEDDING_CATALOG.values()})
)


class UnknownEmbeddingModel(KeyError):
    """Raised when a spec_id is not in the catalog."""


# Spec IDs that existed before the "<provider>/<model_id>" convention was
# enforced (the 9Router entries used a shortened tail). Installs may have
# persisted the old IDs in app_config, embedding_jobs, or
# wiki_page_embeddings_<dim> rows; a data migration rewrites those, and this
# alias keeps any stragglers resolving.
LEGACY_SPEC_ID_ALIASES: dict[str, str] = {
    "ninerouter/text-embedding-3-small": "ninerouter/openai/text-embedding-3-small",
    "ninerouter/text-embedding-3-large": "ninerouter/openai/text-embedding-3-large",
}


def normalize_spec_id(spec_id: str) -> str:
    """Map a possibly-legacy spec_id to its canonical catalog ID."""
    return LEGACY_SPEC_ID_ALIASES.get(spec_id, spec_id)


def get_spec(spec_id: str) -> EmbeddingModelSpec:
    spec_id = normalize_spec_id(spec_id)
    try:
        return EMBEDDING_CATALOG[spec_id]
    except KeyError as e:
        raise UnknownEmbeddingModel(
            f"Unknown embedding model spec_id={spec_id!r}. "
            f"Valid IDs: {sorted(EMBEDDING_CATALOG.keys())}"
        ) from e


def list_specs() -> list[EmbeddingModelSpec]:
    return list(EMBEDDING_CATALOG.values())


def list_specs_by_provider(provider: str) -> list[EmbeddingModelSpec]:
    return [s for s in EMBEDDING_CATALOG.values() if s.provider == provider]


def specs_for_dimension(dimension: int) -> Iterable[EmbeddingModelSpec]:
    return (s for s in EMBEDDING_CATALOG.values() if s.dimension == dimension)
