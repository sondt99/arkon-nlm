import uuid
from types import SimpleNamespace

import pytest
from fastapi import Response

from app.routers import wiki as wiki_router


def _admin() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role="admin")


def _page(slug="foo") -> SimpleNamespace:
    return SimpleNamespace(
        slug=slug,
        title="Foo",
        page_type="concept",
        summary="",
        knowledge_type_slugs=[],
        source_ids=[],
        scope_type="global",
        scope_id=None,
        version=1,
        updated_at=None,
    )


class _FakeCountResult:
    def __init__(self, count):
        self._count = count

    def scalar_one(self):
        return self._count


class _FakeRowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    """Routes count() queries to a fake total, everything else to fake rows."""

    def __init__(self, total, rows):
        self.total = total
        self.rows = rows
        self.statements: list[str] = []

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(sql)
        if "count(" in sql.lower():
            return _FakeCountResult(self.total)
        return _FakeRowsResult(self.rows)


@pytest.mark.asyncio
async def test_list_wiki_pages_sets_x_total_count_header():
    session = _FakeSession(total=42, rows=[_page()])
    response = Response()

    result = await wiki_router.list_wiki_pages(
        response=response,
        page_type=None,
        knowledge_type_slug=None,
        limit=24,
        offset=0,
        db=session,
        user=_admin(),
    )

    assert response.headers["X-Total-Count"] == "42"
    assert len(result) == 1
    assert result[0].slug == "foo"


@pytest.mark.asyncio
async def test_list_wiki_pages_count_query_applies_same_page_type_filter():
    session = _FakeSession(total=7, rows=[_page()])
    response = Response()

    await wiki_router.list_wiki_pages(
        response=response,
        page_type="entity",
        knowledge_type_slug=None,
        limit=24,
        offset=48,
        db=session,
        user=_admin(),
    )

    count_sql, rows_sql = session.statements
    assert "page_type" in count_sql and "page_type" in rows_sql
    # The count query must not carry limit/offset — it counts the whole filtered set.
    assert "LIMIT" not in count_sql.upper()
    assert "OFFSET" not in count_sql.upper()
    assert "LIMIT" in rows_sql.upper() and "OFFSET" in rows_sql.upper()
