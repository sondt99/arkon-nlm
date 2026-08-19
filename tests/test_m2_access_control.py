"""Denial-path tests for the second access-control wave.

Every case here is a *denial*. The audit's recurring theme was that allow paths were
covered by manual testing while denial paths were covered by nothing, which is how a
read-only role kept turning out to have write access.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.database.models import ScopeType, WorkspaceRole
from app.routers.sources import _require_source_access, _validate_source_scope

DEPT = uuid.uuid4()


def _user(*perms: str, role: str = "employee"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        role=role,
        department_id=DEPT,
        custom_role=SimpleNamespace(permissions=list(perms)),
    )


def _db_with_source(source):
    db = AsyncMock()
    db.get.return_value = source
    db.execute.return_value = SimpleNamespace(all=lambda: [])
    return db


# --------------------------------------------------------------------------- #
# #17 — the four plan / knowledge-impact endpoints
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_require_source_access_denies_workspace_source_to_non_member(monkeypatch):
    source = SimpleNamespace(
        id=uuid.uuid4(), scope_type="project", scope_id=uuid.uuid4()
    )

    async def _not_a_member(_db, _user, _wid):
        return False

    monkeypatch.setattr(
        "app.services.permission_engine.can_access_workspace", _not_a_member
    )
    with pytest.raises(HTTPException) as exc:
        await _require_source_access(
            _db_with_source(source), _user("doc:read:all"), source.id, "read"
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_require_source_access_404s_for_missing_source():
    db = AsyncMock()
    db.get.return_value = None
    with pytest.raises(HTTPException) as exc:
        await _require_source_access(db, _user("doc:read:all"), uuid.uuid4(), "read")
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# #33 — client-supplied source scope
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_unknown_scope_type_is_rejected_not_defaulted():
    """A free-string scope_type must 422, not silently become 'global'.

    Treating an unrecognised value as global is the weakness that made skill-contribution
    scope forgeable — the guard read `== "department"` and anything else skipped it.
    """
    with pytest.raises(HTTPException) as exc:
        await _validate_source_scope(AsyncMock(), _user("doc:create:all"), "GLOBAL!", None)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_project_scope_requires_scope_id():
    with pytest.raises(HTTPException) as exc:
        await _validate_source_scope(
            AsyncMock(), _user("doc:create:all"), ScopeType.PROJECT.value, None
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_malformed_scope_id_is_422_not_500():
    with pytest.raises(HTTPException) as exc:
        await _validate_source_scope(
            AsyncMock(), _user("doc:create:all"), ScopeType.PROJECT.value, "not-a-uuid"
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_project_scope_denied_to_non_editor(monkeypatch):
    async def _viewer(_db, _user, _wid):
        return WorkspaceRole.VIEWER.value

    monkeypatch.setattr("app.services.permission_engine.get_workspace_role", _viewer)
    with pytest.raises(HTTPException) as exc:
        await _validate_source_scope(
            AsyncMock(),
            _user("doc:create:own_dept"),
            ScopeType.PROJECT.value,
            uuid.uuid4(),
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_project_scope_allowed_for_editor(monkeypatch):
    wid = uuid.uuid4()

    async def _editor(_db, _user, _wid):
        return WorkspaceRole.EDITOR.value

    monkeypatch.setattr("app.services.permission_engine.get_workspace_role", _editor)
    scope_type, scope_id = await _validate_source_scope(
        AsyncMock(), _user("doc:create:own_dept"), ScopeType.PROJECT.value, wid
    )
    assert (scope_type, scope_id) == (ScopeType.PROJECT.value, wid)


@pytest.mark.asyncio
async def test_global_scope_drops_a_stray_scope_id():
    """A global source must not retain a workspace id that would mislead later readers."""
    scope_type, scope_id = await _validate_source_scope(
        AsyncMock(), _user("doc:create:all"), ScopeType.GLOBAL.value, uuid.uuid4()
    )
    assert scope_type == ScopeType.GLOBAL.value
    assert scope_id is None


# --------------------------------------------------------------------------- #
# #35 — MCP wiki tools must honour wiki:read
# --------------------------------------------------------------------------- #

def test_mcp_wiki_guard_denies_identity_without_wiki_read():
    from app.mcp.tools import _require_wiki_read

    denied = SimpleNamespace(is_admin=False, wiki_readable=False)
    err = _require_wiki_read(denied)
    assert err is not None and "wiki:read" in err


def test_mcp_wiki_guard_allows_reader_and_admin():
    from app.mcp.tools import _require_wiki_read

    assert _require_wiki_read(SimpleNamespace(is_admin=False, wiki_readable=True)) is None
    assert _require_wiki_read(SimpleNamespace(is_admin=True, wiki_readable=False)) is None


# --------------------------------------------------------------------------- #
# #44 — MCP must not leak internal error text
# --------------------------------------------------------------------------- #

def test_mcp_server_masks_error_details():
    from app.mcp.server import create_mcp_server

    mcp = create_mcp_server()
    # fastmcp 3.x stores the resolved flag as _mask_error_details. Asserting on the stored
    # value rather than the constructor call site means a refactor that drops the kwarg —
    # or a fastmcp upgrade that renames it — fails here instead of silently re-exposing
    # raw exception text to MCP clients.
    assert getattr(mcp, "_mask_error_details", False) is True, (
        "MCP error masking is off — raw exceptions reach MCP clients. If fastmcp renamed "
        "this attribute, update the assertion rather than deleting it."
    )


# --------------------------------------------------------------------------- #
# #22 — download filenames must survive non-Latin-1 titles
# --------------------------------------------------------------------------- #

def test_content_disposition_survives_vietnamese_titles():
    from app.routers.notebooklm import _content_disposition

    header = _content_disposition("Báo cáo tài chính Q3", "pdf")
    # Starlette encodes headers as latin-1; this is the exact failure that 500'd.
    header.encode("latin-1")
    assert "filename*=UTF-8''" in header


def test_content_disposition_strips_quote_breakout():
    from app.routers.notebooklm import _content_disposition

    header = _content_disposition('evil"; x=1', "pdf")
    # Only the two structural quotes around the ASCII fallback may remain.
    assert header.count('"') == 2
