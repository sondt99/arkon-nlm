"""Every provider client must carry an explicit request timeout.

Two of the three providers built their SDK client with no timeout at all:

  * `genai.Client(api_key=...)` — no `http_options`, so NO client-side timeout. A stalled
    connection hung the coroutine indefinitely.
  * `openai.AsyncOpenAI(api_key=..., base_url=...)` — SDK default of 600 s.

LLM call sites are wrapped in `asyncio.wait_for`, so they were bounded anyway. EMBEDDING
call sites are not — `worker.py:890`, `pipeline.py:296`, `verifier.py:138` and
`chat.py:605` all await `embed()` directly — so one stuck request parked an arq job or an
HTTP handler for ten minutes, or forever on Gemini.

Asserted structurally, by AST, because the failure is an ABSENT keyword argument: a test
that constructs a client and inspects it would need network credentials, and a grep would
be satisfied by the word "timeout" appearing in a comment.
"""

import ast
import pathlib

import pytest

_PROVIDER_DIR = pathlib.Path("app/ai/providers")

#: (module, constructor, keyword that must be present on every call)
_CLIENT_CONSTRUCTORS = [
    ("google.py", "Client", "http_options"),
    ("openai_provider.py", "AsyncOpenAI", "timeout"),
    ("anthropic_provider.py", "AsyncAnthropic", "timeout"),
]


def _client_calls(module: str, ctor: str) -> list[ast.Call]:
    tree = ast.parse((_PROVIDER_DIR / module).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == ctor:
            found.append(node)
    return found


@pytest.mark.parametrize(
    "module, ctor, required_kw", _CLIENT_CONSTRUCTORS,
    ids=[m for m, _, _ in _CLIENT_CONSTRUCTORS],
)
def test_every_client_construction_sets_a_timeout(module, ctor, required_kw):
    calls = _client_calls(module, ctor)
    assert calls, f"no {ctor}(...) call found in {module} — did the SDK usage change?"

    missing = [
        c.lineno for c in calls
        if required_kw not in {kw.arg for kw in c.keywords if kw.arg}
    ]
    assert not missing, (
        f"{module}: {ctor}(...) built without `{required_kw}` at line(s) {missing}. "
        "Embedding calls are not wrapped in asyncio.wait_for, so a stalled request holds "
        "the arq job or HTTP handler open."
    )


def test_the_google_timeout_is_expressed_in_milliseconds():
    """`HttpOptions.timeout` is milliseconds, unlike every other timeout in this codebase.

    Setting it to 90 the way the other providers express seconds would give a 90 ms budget
    and fail every call.
    """
    from app.ai.providers.google import _CLIENT_TIMEOUT_MS

    assert _CLIENT_TIMEOUT_MS >= 1000, (
        "looks like seconds — HttpOptions.timeout is in milliseconds"
    )


def test_the_three_providers_agree_on_the_budget():
    """A 429 should get comparable patience whichever provider is configured."""
    from app.ai.providers.anthropic_provider import (
        _CLIENT_TIMEOUT_SECONDS as anthropic_t,
    )
    from app.ai.providers.google import _CLIENT_TIMEOUT_MS as google_ms
    from app.ai.providers.openai_provider import _CLIENT_TIMEOUT_SECONDS as openai_t

    assert google_ms / 1000 == anthropic_t == openai_t
