import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import export_api
from app.services import chat_service
from app.services.mcp_auth_service import (
    MCPAuthService,
    ResolvedIdentity,
    get_identity_from_export_token,
)


class _FakeSession:
    def __init__(self, conversation=None, employee=None):
        self._conversation = conversation
        self._employee = employee
        self.added = []

    async def execute(self, _statement):
        return SimpleNamespace(scalar_one_or_none=lambda: self._conversation)

    async def get(self, _model, _id):
        return self._employee

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        self.added.append(obj)

    async def commit(self):
        pass

    async def refresh(self, _obj):
        pass

    async def flush(self):
        pass


def _identity(employee_id=None, allowed_kt=None) -> ResolvedIdentity:
    return ResolvedIdentity(
        employee_id=employee_id or uuid.uuid4(),
        employee_name="Test Employee",
        department_id=uuid.uuid4(),
        department_name="Engineering",
        allowed_knowledge_types=allowed_kt,
    )


# ---------------------------------------------------------------------------
# get_identity_from_export_token
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_token_dependency_rejects_missing_credentials():
    with pytest.raises(HTTPException) as exc:
        await get_identity_from_export_token(credentials=None, db=_FakeSession())
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_export_token_dependency_rejects_invalid_token(monkeypatch):
    async def fake_verify(self, token):
        return None

    monkeypatch.setattr(MCPAuthService, "verify_token", fake_verify)
    creds = SimpleNamespace(credentials="ark_bogus")
    with pytest.raises(HTTPException) as exc:
        await get_identity_from_export_token(credentials=creds, db=_FakeSession())
    assert exc.value.status_code == 401


# ---------------------------------------------------------------------------
# export_chat
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_rejects_invalid_persona():
    body = export_api.ExportChatRequest(persona="bob", question="Hi")
    with pytest.raises(HTTPException) as exc:
        await export_api.export_chat(body, db=_FakeSession(), identity=_identity())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_chat_rejects_empty_question():
    body = export_api.ExportChatRequest(persona="victor", question="   ")
    with pytest.raises(HTTPException) as exc:
        await export_api.export_chat(body, db=_FakeSession(), identity=_identity())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_chat_unknown_conversation_id_is_404():
    body = export_api.ExportChatRequest(
        persona="victor", question="Hi", conversation_id=uuid.uuid4()
    )
    with pytest.raises(HTTPException) as exc:
        await export_api.export_chat(body, db=_FakeSession(conversation=None), identity=_identity())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_chat_happy_path_creates_conversation_and_returns_answer(monkeypatch):
    async def fake_generate_reply(**kwargs):
        return "The answer.", [{"slug": "kb-page", "title": "KB Page"}]

    monkeypatch.setattr(chat_service, "generate_reply", fake_generate_reply)

    body = export_api.ExportChatRequest(persona="ashley", question="What is Arkon?")
    identity = _identity()
    db = _FakeSession()

    result = await export_api.export_chat(body, db=db, identity=identity)

    assert result.answer == "The answer."
    assert result.sources == [export_api.ExportChatSource(slug="kb-page", title="KB Page")]
    assert result.conversation_id is not None
    # A ChatConversation and two ChatMessage rows (user + assistant) were added.
    assert len(db.added) == 3


@pytest.mark.asyncio
async def test_chat_generation_failure_returns_502_without_saving_assistant_message(monkeypatch):
    async def fake_generate_reply(**kwargs):
        raise TimeoutError("provider timed out")

    monkeypatch.setattr(chat_service, "generate_reply", fake_generate_reply)

    body = export_api.ExportChatRequest(persona="victor", question="Hi")
    db = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        await export_api.export_chat(body, db=db, identity=_identity())

    assert exc.value.status_code == 502
    # Only the conversation + user message were added, no assistant message.
    assert len(db.added) == 2


@pytest.mark.asyncio
async def test_chat_workspace_access_denied_is_403(monkeypatch):
    async def deny(*_args, **_kwargs):
        return False

    monkeypatch.setattr(export_api, "can_access_workspace", deny)

    body = export_api.ExportChatRequest(
        persona="victor", question="Hi", workspace_id=uuid.uuid4()
    )
    db = _FakeSession(employee=SimpleNamespace(id=uuid.uuid4(), role="employee"))

    with pytest.raises(HTTPException) as exc:
        await export_api.export_chat(body, db=db, identity=_identity())
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# export_search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_rejects_empty_query():
    with pytest.raises(HTTPException) as exc:
        await export_api.export_search(q="  ", db=_FakeSession(), identity=_identity())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_search_returns_mapped_results(monkeypatch):
    class _FakeEmbeddingProvider:
        async def embed(self, text):
            return [0.1, 0.2, 0.3]

    class _FakeRegistry:
        def __init__(self, _db):
            pass

        async def get_embedding(self, task="document"):
            return _FakeEmbeddingProvider()

    monkeypatch.setattr(export_api, "ProviderRegistry", _FakeRegistry)

    page = SimpleNamespace(
        slug="incident-response",
        title="Incident Response",
        summary="How we handle incidents.",
        page_type="concept",
        knowledge_type_slugs=["sop"],
    )

    from app.services import wiki_service

    async def fake_search(*_args, **kwargs):
        assert kwargs["top_k"] == 5
        return [(page, 0.87654)]

    monkeypatch.setattr(wiki_service, "search_pages_semantic", fake_search)

    result = await export_api.export_search(
        q="incident", top_k=5, db=_FakeSession(), identity=_identity()
    )

    assert result.query == "incident"
    assert len(result.results) == 1
    r = result.results[0]
    assert r.slug == "incident-response"
    assert r.score == 0.8765


@pytest.mark.asyncio
async def test_search_returns_502_when_embedding_unavailable(monkeypatch):
    class _FakeRegistry:
        def __init__(self, _db):
            pass

        async def get_embedding(self, task="document"):
            raise ValueError("No active embedding model. Pick one in Settings -> Embedding.")

    monkeypatch.setattr(export_api, "ProviderRegistry", _FakeRegistry)

    with pytest.raises(HTTPException) as exc:
        await export_api.export_search(q="incident", db=_FakeSession(), identity=_identity())
    assert exc.value.status_code == 502
