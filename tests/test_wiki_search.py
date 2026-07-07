import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database.models import WikiPage
from app.routers import wiki as wiki_router
from app.services import wiki_service

_SPEC_ID = "openai/text-embedding-3-small"
_DIM = 1536


class _FakeResult:
    def all(self):
        return []


class _FakeSession:
    def __init__(self):
        self.last_statement = None

    async def execute(self, statement):
        self.last_statement = statement
        return _FakeResult()


@pytest.mark.asyncio
async def test_search_defaults_to_scope_type_and_id_when_scope_clause_unset():
    session = _FakeSession()
    hits = await wiki_service.search_pages_semantic(
        session,
        query_embedding=[0.0] * _DIM,
        spec_id=_SPEC_ID,
        scope_type="global",
        scope_id=None,
    )
    assert hits == []
    sql = str(session.last_statement)
    assert "wiki_pages.scope_type =" in sql
    assert "wiki_pages.scope_id IS NULL" in sql


@pytest.mark.asyncio
async def test_explicit_scope_clause_overrides_scope_type_and_id():
    session = _FakeSession()
    custom_clause = WikiPage.title == "irrelevant-marker"

    await wiki_service.search_pages_semantic(
        session,
        query_embedding=[0.0] * _DIM,
        spec_id=_SPEC_ID,
        scope_type="global",  # would normally add a scope_id IS NULL clause
        scope_id=None,
        scope_clause=custom_clause,
    )
    sql = str(session.last_statement)
    assert "wiki_pages.title =" in sql
    assert "wiki_pages.scope_type =" not in sql
    assert "wiki_pages.scope_id IS" not in sql


@pytest.mark.asyncio
async def test_none_scope_clause_means_no_restriction():
    session = _FakeSession()
    await wiki_service.search_pages_semantic(
        session,
        query_embedding=[0.0] * _DIM,
        spec_id=_SPEC_ID,
        scope_clause=None,
    )
    sql = str(session.last_statement)
    assert "wiki_pages.scope_type =" not in sql
    assert "wiki_pages.scope_id IS" not in sql
    # The two baseline conditions (model spec + reserved-slug exclusion) still apply.
    assert "model_spec_id" in sql
    assert "wiki_pages.slug NOT IN" in sql


# ---------------------------------------------------------------------------
# GET /wiki/search router
# ---------------------------------------------------------------------------

def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role="admin")


@pytest.mark.asyncio
async def test_search_endpoint_rejects_empty_query():
    with pytest.raises(HTTPException) as exc:
        await wiki_router.search_wiki_pages(q="   ", db=_FakeSession(), user=_admin())
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_search_endpoint_maps_missing_embedding_config_to_503(monkeypatch):
    class _FakeEmbeddingProvider:
        async def embed(self, text):
            raise ValueError("No active embedding model. Pick one in Settings -> Embedding.")

    class _FakeRegistry:
        def __init__(self, _db):
            pass

        async def get_embedding(self, task="document"):
            return _FakeEmbeddingProvider()

    monkeypatch.setattr(wiki_router, "ProviderRegistry", _FakeRegistry)

    with pytest.raises(HTTPException) as exc:
        await wiki_router.search_wiki_pages(q="cve", db=_FakeSession(), user=_admin())
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_search_endpoint_returns_mapped_results(monkeypatch):
    class _FakeEmbeddingProvider:
        async def embed(self, text):
            return [0.1, 0.2]

    class _FakeRegistry:
        def __init__(self, _db):
            pass

        async def get_embedding(self, task="document"):
            return _FakeEmbeddingProvider()

    monkeypatch.setattr(wiki_router, "ProviderRegistry", _FakeRegistry)

    page = SimpleNamespace(
        slug="cve-2021-44228",
        title="CVE-2021-44228 (Log4Shell)",
        page_type="concept",
        summary="Remote code execution in Log4j.",
        scope_type="global",
        scope_id=None,
    )

    async def fake_search(*_args, **kwargs):
        assert kwargs["top_k"] == 20
        return [(page, 0.9123)]

    monkeypatch.setattr(wiki_service, "search_pages_semantic", fake_search)
    monkeypatch.setattr(wiki_router.wiki_service, "search_pages_semantic", fake_search)

    results = await wiki_router.search_wiki_pages(
        q="log4shell", top_k=20, db=_FakeSession(), user=_admin()
    )

    assert len(results) == 1
    assert results[0].slug == "cve-2021-44228"
    assert results[0].score == 0.9123
