"""`merger.py`'s truncation guard was inert on Gemini.

`LLMGeneration.truncated` is `stop_reason == "max_tokens"`, and `LLMProvider`'s base
`generate_detailed` reports `stop_reason=None` — deliberately, with a docstring telling
callers to read None as "cannot rule out truncation". `GoogleLLM` never overrode it, so on
a Gemini deployment `result.truncated` was permanently False and `merger.py:107` — whose
comment describes a real incident where a merge cut off at max_output_tokens was committed
mid-document — could not fire.

The base fallback is correct and stays. What must not happen again is a concrete provider
inheriting it by omission, so the check is structural: every provider the registry can hand
out reports a real stop reason.
"""

from types import SimpleNamespace

import pytest

from app.ai.providers.anthropic_provider import AnthropicLLM
from app.ai.providers.base import LLMGeneration, LLMProvider
from app.ai.providers.google import GoogleLLM
from app.ai.providers.openai_provider import OpenAILLM

_CONCRETE_LLM_PROVIDERS = [AnthropicLLM, GoogleLLM, OpenAILLM]


@pytest.mark.parametrize(
    "provider", _CONCRETE_LLM_PROVIDERS, ids=lambda p: p.__name__
)
def test_every_provider_reports_its_own_stop_reason(provider):
    assert "generate_detailed" in provider.__dict__, (
        f"{provider.__name__} inherits LLMProvider.generate_detailed, which reports "
        "stop_reason=None. LLMGeneration.truncated is then permanently False and "
        "merger.py commits bodies cut off at max_tokens"
    )


def test_the_base_fallback_is_still_conservative():
    """It must keep reporting None — the bug was inheriting it, not the fallback itself."""
    assert "generate_detailed" in LLMProvider.__dict__
    assert LLMGeneration(text="x", stop_reason=None).truncated is False
    assert LLMGeneration(text="x", stop_reason="max_tokens").truncated is True


# ---------------------------------------------------------------------------
# Gemini response mapping
# ---------------------------------------------------------------------------

def _response(text, finish, usage=None):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=SimpleNamespace(name=finish))],
        usage_metadata=usage,
    )


@pytest.mark.parametrize(
    "finish, expected",
    [
        ("STOP", "end_turn"),
        ("MAX_TOKENS", "max_tokens"),
        ("SAFETY", "refusal"),
        ("RECITATION", "refusal"),
        ("PROHIBITED_CONTENT", "refusal"),
        ("SOMETHING_NEW", None),
    ],
)
def test_gemini_finish_reasons_map_to_provider_neutral_stop_reasons(finish, expected):
    gen = GoogleLLM._generation_from(_response("body", finish))
    assert gen.stop_reason == expected


def test_a_truncated_gemini_merge_is_detectable():
    """The exact condition merger.py checks."""
    assert GoogleLLM._generation_from(_response("half a page", "MAX_TOKENS")).truncated is True
    assert GoogleLLM._generation_from(_response("a full page", "STOP")).truncated is False


def test_a_blocked_response_is_not_a_silent_empty_success():
    """`response.text` is None when a candidate is blocked.

    `return response.text or ""` turned that into an empty SUCCESS, and callers read an
    empty string as a real answer — reducer.py records "no entities merged", verifier.py
    records "no conflict", and the KB commits as though verification passed.
    """
    gen = GoogleLLM._generation_from(_response(None, "SAFETY"))
    assert gen.text == ""
    assert gen.stop_reason == "refusal", (
        "a safety block is indistinguishable from a genuinely empty answer"
    )


def test_usage_is_carried_when_the_response_has_it():
    usage = SimpleNamespace(prompt_token_count=120, candidates_token_count=45)
    gen = GoogleLLM._generation_from(_response("body", "STOP", usage))
    assert gen.usage == {"input_tokens": 120, "output_tokens": 45}


def test_a_response_with_no_candidates_does_not_raise():
    """Defensive: the SDK returns an empty candidate list in some blocked cases."""
    gen = GoogleLLM._generation_from(
        SimpleNamespace(text=None, candidates=[], usage_metadata=None)
    )
    assert gen.text == ""
    assert gen.stop_reason is None
