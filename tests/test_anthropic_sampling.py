"""Anthropic sampling-parameter gate (issues #1 / #2)."""

from app.ai.providers.anthropic_provider import _apply_sampling, model_accepts_sampling


def test_current_generation_rejects_sampling():
    for model in ("claude-opus-4-8", "claude-sonnet-5", "claude-opus-4-7"):
        assert model_accepts_sampling(model) is False


def test_haiku_and_sonnet_4_6_still_accept_sampling():
    assert model_accepts_sampling("claude-haiku-4-5") is True
    assert model_accepts_sampling("claude-sonnet-4-6") is True


def test_apply_sampling_omits_keys_on_blocked_models():
    kwargs: dict = {}
    _apply_sampling(kwargs, "claude-opus-4-8", 0.2, 0.9)
    assert "temperature" not in kwargs
    assert "top_p" not in kwargs


def test_apply_sampling_includes_keys_on_legacy_models():
    kwargs: dict = {}
    _apply_sampling(kwargs, "claude-haiku-4-5", 0.2, 0.9)
    assert kwargs["temperature"] == 0.2
    assert kwargs["top_p"] == 0.9
