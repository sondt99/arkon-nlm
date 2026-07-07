"""
Dynamic Config Service — DB > .env > default.
Reads settings from app_config table first, falls back to env/defaults.
Sensitive values (API keys, tokens) are encrypted at rest with Fernet.
"""

import base64
import hashlib
from typing import Any, Optional

from cryptography.fernet import Fernet
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AppConfig

# ---------------------------------------------------------------------------
# Encryption helpers
# ---------------------------------------------------------------------------

def _derive_fernet_key(secret: str) -> bytes:
    """Derive a Fernet-compatible key from an arbitrary secret string."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _is_sensitive(key: str) -> bool:
    """A key is sensitive if it stores raw credentials/API keys."""
    return (
        key in {"embedding_api_key", "llm_api_key", "vision_api_key", "chatbot_api_key"}
        or key.startswith("embedding_api_key__")
        or key.startswith("llm_api_key__")
        or key.startswith("vision_api_key__")
        or key.startswith("chatbot_api_key__")
    )


# Backward-compat alias: code that compared `key in SENSITIVE_KEYS` should now
# call `_is_sensitive(key)`. Kept for any external callers.
SENSITIVE_KEYS = frozenset({
    "embedding_api_key",
    "llm_api_key",
    "vision_api_key",
    "chatbot_api_key",
})

# Active embedding model selection (canonical spec_id from EMBEDDING_CATALOG)
ACTIVE_EMBEDDING_MODEL_KEY = "active_embedding_model_spec_id"

# Per-provider embedding API keys: `embedding_api_key__<provider>`. We store
# one key per provider so admins can switch provider without losing the
# previously configured key. Encrypted at rest.
def embedding_api_key_for(provider: str) -> str:
    return f"embedding_api_key__{provider}"


# All config keys that can be managed via UI
ALL_CONFIG_KEYS = [
    # --- Embedding (whitelist-driven) ---
    ACTIVE_EMBEDDING_MODEL_KEY,  # canonical spec_id, e.g. "openai/text-embedding-3-small"
    "embedding_api_key__google",
    "embedding_api_key__openai",
    "embedding_api_key__anthropic",
    "embedding_api_key__ollama",
    "embedding_api_key__ninerouter",
    "embedding_base_url",        # optional, custom endpoint (Ollama, Azure, proxy)

    # --- LLM provider (for summarization, webhook gateway) ---
    "llm_provider",              # "google" | "openai" | "anthropic" | "ollama" | "ninerouter"
    "llm_model_id",              # e.g. "gpt-4o-mini", "claude-sonnet-4-20250514"
    "llm_api_key",               # Legacy single key (kept for backwards compat)
    "llm_api_key__google",
    "llm_api_key__openai",
    "llm_api_key__anthropic",
    "llm_api_key__ollama",
    "llm_api_key__ninerouter",
    "llm_base_url",              # Custom endpoint

    # --- Vision provider (for image analysis during ingestion) ---
    "vision_provider",           # "google" | "openai" | "ninerouter"
    "vision_model_id",           # e.g. "gemini-2.0-flash", "gpt-4o"
    "vision_api_key",            # Legacy single key (kept for backwards compat)
    "vision_api_key__google",
    "vision_api_key__openai",
    "vision_api_key__anthropic",
    "vision_api_key__ollama",
    "vision_api_key__ninerouter",
    "vision_base_url",           # Custom endpoint

    # --- Custom embedding spec (set when user switches to a non-catalog model) ---
    "embedding_custom_spec_id",
    "embedding_custom_model_id",
    "embedding_custom_dimension",
    "embedding_custom_provider",

    # --- Chatbot provider (optional — falls back to LLM provider if unset) ---
    "chatbot_provider",           # "google" | "openai" | "anthropic" | "ollama" | "ninerouter"
    "chatbot_model_id",
    "chatbot_api_key",
    "chatbot_api_key__google",
    "chatbot_api_key__openai",
    "chatbot_api_key__anthropic",
    "chatbot_api_key__ollama",
    "chatbot_api_key__ninerouter",
    "chatbot_base_url",

    # --- Chat ---
    "chat_rag_enabled",           # "true" | "false" — toggle RAG/KB search

    # --- Export API ---
    "export_api_enabled",         # "true" | "false" — toggle /api/export/v1/* endpoints
    "export_api_temperature",     # float 0.0-1.0, empty = provider default
    "export_api_max_tokens",      # int, empty = provider default
    "export_api_top_p",           # float 0.0-1.0, empty = provider default

    # --- System ---
    "session_timeout_minutes",

    # --- Deprecated embedding keys (kept readable for one release; do not write) ---
    "embedding_provider",
    "embedding_model_id",
    "embedding_api_key",
    "embedding_dimensions",
]


class ConfigService:
    """
    Config priority: DB > .env > default

    Usage:
        config_svc = ConfigService(db_session)
        api_key = await config_svc.get("google_api_key")
        await config_svc.set("google_api_key", "AIza...")
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self._fernet: Optional[Fernet] = None

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            from app.config import settings
            key = _derive_fernet_key(settings.secret_key)
            self._fernet = Fernet(key)
        return self._fernet

    def _encrypt(self, value: str) -> str:
        """Encrypt a value for storage."""
        return self.fernet.encrypt(value.encode()).decode()

    def _decrypt(self, value: str) -> str:
        """Decrypt a stored value."""
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except Exception:
            # If decryption fails (e.g. key changed), return raw value
            logger.warning("Failed to decrypt config value, returning raw")
            return value

    async def get(self, key: str) -> Optional[str]:
        """
        Get a config value. Priority: DB > .env > default.
        """
        # 1. Try DB first
        stmt = select(AppConfig).where(AppConfig.key == key)
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()

        if row and row.value:
            value = row.value
            if _is_sensitive(key):
                value = self._decrypt(value)
            return value

        # 2. Fallback to env/settings
        from app.config import settings
        env_value = getattr(settings, key, None)
        if env_value is not None:
            return str(env_value)

        return None

    async def set(self, key: str, value: str) -> None:
        """Set a config value in DB."""
        store_value = value
        if _is_sensitive(key) and value:
            store_value = self._encrypt(value)

        stmt = select(AppConfig).where(AppConfig.key == key)
        result = await self.db.execute(stmt)
        row = result.scalar_one_or_none()

        if row:
            row.value = store_value
        else:
            self.db.add(AppConfig(key=key, value=store_value))

        await self.db.flush()

    async def get_all(self) -> dict[str, Optional[str]]:
        """Get all config values (decrypted)."""
        result = {}
        for key in ALL_CONFIG_KEYS:
            result[key] = await self.get(key)
        return result

    async def get_all_for_ui(self) -> dict[str, Any]:
        """Get all config values, masking sensitive ones for UI display."""
        all_config = await self.get_all()
        ui_config = {}

        for key, value in all_config.items():
            if _is_sensitive(key) and value:
                # Mask: show only last 4 chars
                if len(value) > 8:
                    ui_config[key] = "•" * 8 + value[-4:]
                else:
                    ui_config[key] = "•" * len(value)
                ui_config[f"{key}_configured"] = True
            else:
                ui_config[key] = value
                if _is_sensitive(key):
                    ui_config[f"{key}_configured"] = bool(value)

        return ui_config

    async def set_batch(self, updates: dict[str, str]) -> dict[str, bool]:
        """Set multiple config values at once. Returns {key: success}."""
        results = {}
        for key, value in updates.items():
            if key not in ALL_CONFIG_KEYS:
                results[key] = False
                continue
            # Skip masked values (user didn't change them)
            if value and value.startswith("••••"):
                results[key] = True
                continue
            await self.set(key, value)
            results[key] = True
        return results


async def get_effective_config(db: AsyncSession) -> dict[str, Optional[str]]:
    """
    Convenience: get all effective config values (DB > env > default).
    Used by services that need the latest config.
    """
    svc = ConfigService(db)
    return await svc.get_all()
