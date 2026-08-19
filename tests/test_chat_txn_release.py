"""The chat path must not hold a DB transaction across the provider call."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import chat_service


@pytest.mark.asyncio
async def test_generate_reply_commits_before_calling_the_provider(monkeypatch):
    """Ordering test: commit() must happen before llm.generate().

    Generation can take up to chat_generation_timeout (240s default). Holding the pooled
    connection across that window left it `idle in transaction`, so ~30 concurrent chats
    exhausted pool_size=20 + max_overflow=10 and every other endpoint began failing on
    connection checkout.

    Asserting on the *order* rather than merely "commit was called" is the point — a commit
    that happens after generation would satisfy a call-count assertion while fixing
    nothing.
    """
    events: list[str] = []

    session = AsyncMock()

    async def _commit():
        events.append("commit")

    session.commit = _commit
    session.execute.return_value = SimpleNamespace(
        scalars=lambda: SimpleNamespace(all=lambda: [])
    )

    class _LLM:
        # generate_reply logs llm.config.model_id after generating.
        config = SimpleNamespace(model_id="test-model")

        async def generate(self, *_a, **_kw):
            events.append("generate")
            return "an answer long enough to avoid the expansion path " * 5

    class _Registry:
        async def get_chatbot_llm(self):
            return _LLM()

    # Disable RAG so the test does not need embeddings.
    class _Config:
        def __init__(self, *_a, **_kw):
            pass

        async def get(self, _key):
            return "false"

    # ConfigService is imported inside generate_reply, so patch it at its source module.
    import app.services.config_service as config_module
    monkeypatch.setattr(config_module, "ConfigService", _Config)
    monkeypatch.setattr(chat_service, "_should_expand_answer", lambda *_a, **_kw: False)

    conversation = SimpleNamespace(
        id="c1", scope_type="global", scope_id=None
    )

    answer, sources = await chat_service.generate_reply(
        session=session,
        registry=_Registry(),  # type: ignore[arg-type]
        conversation=conversation,  # type: ignore[arg-type]
        question="what is the budget?",
    )

    assert "commit" in events, "generate_reply never released the transaction"
    assert "generate" in events
    assert events.index("commit") < events.index("generate"), (
        f"commit must precede the provider call, got {events}"
    )
    assert answer
    assert sources == []
