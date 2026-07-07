import uuid
from types import SimpleNamespace

import pytest

from app.routers import chat as chat_router


class _FakeDeleteResult:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount
        self.statements: list[str] = []
        self.committed = False

    async def execute(self, statement):
        self.statements.append(str(statement))
        return _FakeDeleteResult(self.rowcount)

    async def commit(self):
        self.committed = True


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), role="employee")


@pytest.mark.asyncio
async def test_delete_all_conversations_scopes_to_current_user():
    session = _FakeSession(rowcount=5)

    result = await chat_router.delete_all_conversations(
        scope_type=None, scope_id=None, db=session, current_user=_user()
    )

    assert result.deleted == 5
    assert session.committed
    sql = session.statements[0]
    assert "chat_conversations" in sql
    assert "employee_id" in sql
    # No scope filters requested — must not narrow beyond ownership.
    assert "scope_type" not in sql
    assert "scope_id" not in sql


@pytest.mark.asyncio
async def test_delete_all_conversations_applies_scope_filters_when_given():
    session = _FakeSession(rowcount=2)

    await chat_router.delete_all_conversations(
        scope_type="project", scope_id=uuid.uuid4(), db=session, current_user=_user()
    )

    sql = session.statements[0]
    assert "scope_type" in sql
    assert "scope_id" in sql


@pytest.mark.asyncio
async def test_delete_all_conversations_reports_zero_when_nothing_deleted():
    session = _FakeSession(rowcount=0)

    result = await chat_router.delete_all_conversations(
        scope_type=None, scope_id=None, db=session, current_user=_user()
    )

    assert result.deleted == 0
