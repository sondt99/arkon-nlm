"""Omniroute is an OpenAI-compatible provider seeded from OMNIROUTE_* env vars."""

import inspect

import pytest

from app.ai.providers.base import OPENAI_COMPATIBLE, ProviderType
from app.ai.providers.openai_provider import OpenAILLM
from app.ai.registry import _get_embedding_class, _get_llm_class, _get_vision_class
from app.database.models import AppConfig
from app.services.config_service import ALL_CONFIG_KEYS, ConfigService


def _omniroute_env(monkeypatch, *, key="sk-test", url="https://ai.example.com/v1", model="nosiaht"):
    from app.config import settings

    monkeypatch.setattr(settings, "omniroute_api_key", key)
    monkeypatch.setattr(settings, "omniroute_base_url", url)
    monkeypatch.setattr(settings, "omniroute_model", model)


def test_omniroute_is_openai_compatible():
    assert ProviderType.OMNIROUTE in OPENAI_COMPATIBLE
    assert _get_llm_class(ProviderType.OMNIROUTE) is OpenAILLM
    assert _get_embedding_class(ProviderType.OMNIROUTE).__name__ == "OpenAIEmbedding"
    assert _get_vision_class(ProviderType.OMNIROUTE).__name__ == "OpenAIVision"


def test_openai_compatible_chat_calls_disable_streaming():
    """Omniroute streams SSE unless stream=false is sent on the wire."""
    src = inspect.getsource(OpenAILLM.generate_detailed)
    assert '"stream": False' in src
    assert "stream" in inspect.getsource(OpenAILLM.generate_with_tools)
    assert "max_tokens=256" in inspect.getsource(OpenAILLM.test_connection)


def test_openai_compatible_generate_detailed_maps_truncation():
    src = inspect.getsource(OpenAILLM.generate_detailed)
    assert "LLMGeneration" in src
    assert '"length": "max_tokens"' in src
    assert "generate_detailed" in inspect.getsource(OpenAILLM.generate)


def test_omniroute_config_keys_are_managed():
    for key in (
        "llm_api_key__omniroute",
        "chatbot_api_key__omniroute",
        "vision_api_key__omniroute",
        "gateway_api_key__omniroute",
        "embedding_api_key__omniroute",
    ):
        assert key in ALL_CONFIG_KEYS


@pytest.mark.asyncio
async def test_omniroute_env_fills_llm_when_db_empty(fake_db, monkeypatch):
    _omniroute_env(monkeypatch)
    svc = ConfigService(fake_db.factory())

    assert await svc.get("llm_provider") == "omniroute"
    assert await svc.get("llm_model_id") == "nosiaht"
    assert await svc.get("llm_base_url") == "https://ai.example.com/v1"
    assert await svc.get("llm_api_key__omniroute") == "sk-test"
    # Chatbot/gateway stay unset so they inherit the LLM slot.
    assert await svc.get("chatbot_provider") is None
    assert await svc.get("gateway_provider") is None
    assert await svc.get("vision_provider") is None


@pytest.mark.asyncio
async def test_saved_provider_wins_over_omniroute_env(fake_db, monkeypatch):
    _omniroute_env(monkeypatch)
    fake_db.insert(AppConfig(key="llm_provider", value="google"))
    fake_db.insert(AppConfig(key="llm_model_id", value="gemini-2.5-flash"))
    svc = ConfigService(fake_db.factory())

    assert await svc.get("llm_provider") == "google"
    assert await svc.get("llm_model_id") == "gemini-2.5-flash"
    # Google must not inherit the Omniroute URL.
    assert await svc.get("llm_base_url") is None


@pytest.mark.asyncio
async def test_omniroute_env_url_fills_when_provider_is_omniroute(fake_db, monkeypatch):
    _omniroute_env(monkeypatch, url="https://ai.nosiaht.com/v1")
    fake_db.insert(AppConfig(key="llm_provider", value="omniroute"))
    fake_db.insert(AppConfig(key="llm_model_id", value="glm/glm-5.3"))
    svc = ConfigService(fake_db.factory())

    assert await svc.get("llm_provider") == "omniroute"
    assert await svc.get("llm_model_id") == "glm/glm-5.3"
    assert await svc.get("llm_base_url") == "https://ai.nosiaht.com/v1"
    assert await svc.get("llm_api_key__omniroute") == "sk-test"


@pytest.mark.asyncio
async def test_incomplete_omniroute_env_does_not_select_the_provider(fake_db, monkeypatch):
    _omniroute_env(monkeypatch, model="")
    svc = ConfigService(fake_db.factory())

    assert await svc.get("llm_provider") is None
    assert await svc.get("llm_model_id") is None
    # The key is still available for a later UI save.
    assert await svc.get("llm_api_key__omniroute") == "sk-test"
