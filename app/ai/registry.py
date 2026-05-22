"""
Provider Registry — central factory that resolves the correct AI provider
based on runtime configuration stored in the database.

Usage:
    registry = ProviderRegistry(db_session)

    # Embedding (for document ingestion & search)
    emb = await registry.get_embedding()
    vectors = await emb.embed_batch(["hello", "world"])

    # Embedding for queries (with search_query task for Google)
    emb_query = await registry.get_embedding(task="search_query")
    query_vec = await emb_query.embed("what is the refund policy?")

    # LLM (for summarization, webhook gateway)
    llm = await registry.get_llm()
    summary = await llm.generate("Summarize this document...")

    # Vision (for image analysis during ingestion)
    vision = await registry.get_vision()
    if vision:
        caption = await vision.analyze_image(image_bytes)
"""

from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    ProviderConfig,
    ProviderType,
    VisionProvider,
)

# ---------------------------------------------------------------------------
# Provider class mappings — add new providers here
# ---------------------------------------------------------------------------

def _get_embedding_class(provider: ProviderType) -> type[EmbeddingProvider]:
    if provider == ProviderType.GOOGLE:
        from app.ai.providers.google import GoogleEmbedding
        return GoogleEmbedding
    elif provider in (ProviderType.OPENAI, ProviderType.OLLAMA, ProviderType.NINEROUTER):
        from app.ai.providers.openai_provider import OpenAIEmbedding
        return OpenAIEmbedding
    raise ValueError(f"Unsupported embedding provider: {provider}")


def _get_llm_class(provider: ProviderType) -> type[LLMProvider]:
    if provider == ProviderType.GOOGLE:
        from app.ai.providers.google import GoogleLLM
        return GoogleLLM
    elif provider in (ProviderType.OPENAI, ProviderType.OLLAMA, ProviderType.NINEROUTER):
        from app.ai.providers.openai_provider import OpenAILLM
        return OpenAILLM
    elif provider == ProviderType.ANTHROPIC:
        from app.ai.providers.anthropic_provider import AnthropicLLM
        return AnthropicLLM
    raise ValueError(f"Unsupported LLM provider: {provider}")


def _get_vision_class(provider: ProviderType) -> type[VisionProvider]:
    if provider == ProviderType.GOOGLE:
        from app.ai.providers.google import GoogleVision
        return GoogleVision
    elif provider in (ProviderType.OPENAI, ProviderType.OLLAMA, ProviderType.NINEROUTER):
        from app.ai.providers.openai_provider import OpenAIVision
        return OpenAIVision
    elif provider == ProviderType.ANTHROPIC:
        from app.ai.providers.anthropic_provider import AnthropicVision
        return AnthropicVision
    raise ValueError(f"Unsupported vision provider: {provider}")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

class ProviderRegistry:
    """
    Resolves provider configs from DB and returns the correct implementation.

    Config keys in DB follow the pattern: {capability}_{field}
      - embedding_provider, embedding_model_id, embedding_api_key, ...
      - llm_provider, llm_model_id, llm_api_key, ...
      - vision_provider, vision_model_id, vision_api_key, ...
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_embedding(
        self,
        task: str = "document",
        spec_id: Optional[str] = None,
    ) -> EmbeddingProvider:
        """
        Get an embedding provider for a specific catalog spec, or the active
        one configured in app_config.

        Args:
            task: Embedding task type (Google uses this for query vs document).
            spec_id: Override — load this specific catalog entry instead of
                     the system's active spec. Used by re-embed jobs that need
                     to embed against a NEW model while the active spec is
                     still pointing at the OLD one (atomic flip on completion).
        """
        config = await self._load_embedding_config(spec_id=spec_id)
        config.extra["task"] = task
        cls = _get_embedding_class(config.provider)
        return cls(config)

    async def get_active_embedding_spec_id(self) -> Optional[str]:
        """Return the spec_id currently active for search, or None if unset."""
        from app.ai.embedding_catalog import EMBEDDING_CATALOG
        from app.services.config_service import (
            ACTIVE_EMBEDDING_MODEL_KEY,
            ConfigService,
        )

        svc = ConfigService(self.db)
        spec_id = await svc.get(ACTIVE_EMBEDDING_MODEL_KEY)
        if spec_id and spec_id in EMBEDDING_CATALOG:
            return spec_id
        return None

    async def get_llm(self) -> LLMProvider:
        """Get the configured LLM provider."""
        config = await self._load_config("llm")
        cls = _get_llm_class(config.provider)
        return cls(config)

    async def get_vision(self) -> Optional[VisionProvider]:
        """Get the configured vision provider. Returns None if not configured."""
        try:
            config = await self._load_config("vision")
            cls = _get_vision_class(config.provider)
            return cls(config)
        except ValueError:
            logger.debug("No vision provider configured, image analysis disabled")
            return None

    async def test_all(self) -> dict[str, tuple[bool, str]]:
        """
        Test all configured providers.
        Returns: {"embedding": (True, "OK"), "llm": (False, "error"), ...}
        """
        results = {}

        for capability in ("embedding", "llm", "vision"):
            try:
                config = await self._load_config(capability)
            except ValueError as e:
                results[capability] = (False, f"Not configured: {e}")
                continue

            try:
                if capability == "embedding":
                    provider = _get_embedding_class(config.provider)(config)
                elif capability == "llm":
                    provider = _get_llm_class(config.provider)(config)
                else:
                    provider = _get_vision_class(config.provider)(config)
                results[capability] = await provider.test_connection()
            except Exception as e:
                results[capability] = (False, str(e))

        return results

    # --- Internal ---

    async def _load_embedding_config(
        self, spec_id: Optional[str] = None
    ) -> ProviderConfig:
        """
        Build a ProviderConfig for an embedding model from the catalog.

        Resolution order:
          1. Explicit spec_id argument (used by migration jobs).
          2. active_embedding_model_spec_id from app_config.

        API key is loaded from the per-provider key
        (`embedding_api_key__<provider>`); falls back to the legacy single-key
        `embedding_api_key` for in-place upgrades.
        """
        from app.ai.embedding_catalog import get_spec
        from app.services.config_service import (
            ACTIVE_EMBEDDING_MODEL_KEY,
            ConfigService,
            embedding_api_key_for,
        )

        svc = ConfigService(self.db)

        if spec_id is None:
            spec_id = await svc.get(ACTIVE_EMBEDDING_MODEL_KEY)
        if not spec_id:
            raise ValueError(
                "No active embedding model. Pick one in Settings → Embedding."
            )

        try:
            spec = get_spec(spec_id)
        except Exception:
            # Custom model — reconstruct from stored config
            custom_id = await svc.get("embedding_custom_spec_id")
            if custom_id != spec_id:
                raise
            raw_model_id = await svc.get("embedding_custom_model_id") or spec_id.split("/", 1)[-1]
            dim_str = await svc.get("embedding_custom_dimension")
            provider_str = await svc.get("embedding_custom_provider") or spec_id.split("/")[0]
            if not dim_str:
                raise ValueError(
                    f"Custom embedding model '{spec_id}' has no dimension configured."
                )
            from app.ai.embedding_catalog import EmbeddingModelSpec
            spec = EmbeddingModelSpec(
                id=spec_id,
                provider=provider_str,
                model_id=raw_model_id,
                dimension=int(dim_str),
                max_input_tokens=8191,
                label=f"Custom — {raw_model_id} ({dim_str}d)",
                cost_per_1m_tokens=None,
            )
        api_key = (
            await svc.get(embedding_api_key_for(spec.provider))
            or await svc.get("embedding_api_key")  # legacy fallback
            or ""
        )
        base_url = await svc.get("embedding_base_url")

        return ProviderConfig(
            provider=ProviderType(spec.provider),
            api_key=api_key,
            model_id=spec.model_id,
            base_url=base_url,
            dimensions=spec.dimension,
            extra={"spec_id": spec.id},
        )

    async def _load_config(self, capability: str) -> ProviderConfig:
        """Load provider config from DB for LLM / vision capabilities."""
        if capability == "embedding":
            return await self._load_embedding_config()

        from app.services.config_service import ConfigService
        svc = ConfigService(self.db)

        provider_str = await svc.get(f"{capability}_provider")
        model_id = await svc.get(f"{capability}_model_id")
        # Per-provider key takes precedence over the legacy single key
        api_key = (
            await svc.get(f"{capability}_api_key__{provider_str}")
            or await svc.get(f"{capability}_api_key")
            or ""
        )
        base_url = await svc.get(f"{capability}_base_url")
        dimensions_str = await svc.get(f"{capability}_dimensions")

        if not provider_str or not model_id:
            raise ValueError(
                f"No {capability} provider configured. "
                f"Set {capability}_provider and {capability}_model_id in settings."
            )

        return ProviderConfig(
            provider=ProviderType(provider_str),
            api_key=api_key or "",
            model_id=model_id,
            # 9router needs its own base URL; Ollama gets its from llm_base_url
            base_url=base_url,
            dimensions=int(dimensions_str) if dimensions_str else None,
            extra={},
        )


# ---------------------------------------------------------------------------
# Convenience: supported providers list (for admin UI dropdowns)
# ---------------------------------------------------------------------------

SUPPORTED_PROVIDERS = {
    "embedding": [
        {"id": "google", "name": "Google Gemini", "models": [
            "gemini-embedding-2", "text-embedding-004",
        ]},
        {"id": "openai", "name": "OpenAI", "models": [
            "text-embedding-3-small", "text-embedding-3-large", "text-embedding-ada-002",
        ]},
    ],
    "llm": [
        {"id": "google", "name": "Google Gemini", "models": [
            "gemini-3.1-pro", "gemini-3.1-flash", "gemini-3.0-flash",
            "gemini-2.5-flash", "gemini-2.5-pro",
        ]},
        {"id": "openai", "name": "OpenAI", "models": [
            "gpt-5.5-instant", "gpt-5.4", "gpt-5.2",
            "gpt-4.1-mini", "gpt-4.1-nano", "gpt-4o", "gpt-4o-mini",
        ]},
        {"id": "anthropic", "name": "Anthropic", "models": [
            "claude-4.7-opus", "claude-4.6-sonnet",
            "claude-sonnet-4-20250514", "claude-haiku-4-20250514",
        ]},
        {"id": "ollama", "name": "Ollama", "models": [
            "qwen2.5:14b", "qwen2.5:7b", "llama3.1:8b", "mistral:7b",
        ]},
        {"id": "ninerouter", "name": "9Router", "models": [
            "google/gemini-2.5-pro", "google/gemini-2.5-flash",
            "anthropic/claude-sonnet-4-6", "anthropic/claude-haiku-4-5",
            "openai/gpt-4o", "openai/gpt-4o-mini",
            "meta-llama/llama-3.3-70b-instruct",
        ]},
    ],
    "vision": [
        {"id": "google", "name": "Google Gemini", "models": [
            "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro",
        ]},
        {"id": "openai", "name": "OpenAI", "models": [
            "gpt-4o", "gpt-4o-mini",
        ]},
        {"id": "anthropic", "name": "Anthropic", "models": [
            "claude-sonnet-4-20250514", "claude-haiku-4-20250514",
        ]},
        {"id": "ollama", "name": "Ollama", "models": [
            "llava:7b", "llava:13b", "llava-llama3", "moondream",
        ]},
        {"id": "ninerouter", "name": "9Router", "models": [
            "google/gemini-2.5-flash", "openai/gpt-4o", "anthropic/claude-sonnet-4-6",
        ]},
    ],
}
