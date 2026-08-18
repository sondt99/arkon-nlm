"""RBAC denial-path tests for the permission engine (no live database)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.permission_engine import can_access_document, can_access_skill
from app.services.permissions import ROLE_PRESETS

VIEWER_PERMISSIONS = ROLE_PRESETS["Viewer"]["permissions"]


def _user(*perms: str, role: str = "employee"):
    return SimpleNamespace(
        id=uuid4(),
        role=role,
        department_id=uuid4(),
        custom_role=SimpleNamespace(permissions=list(perms)),
    )


@pytest.mark.asyncio
async def test_viewer_cannot_delete_global_skill():
    user = _user(*VIEWER_PERMISSIONS)
    skill = SimpleNamespace(departments=[])
    assert await can_access_skill(AsyncMock(), user, skill, "delete") is False
    assert await can_access_skill(AsyncMock(), user, skill, "edit") is False


@pytest.mark.asyncio
async def test_viewer_can_read_global_skill():
    user = _user(*VIEWER_PERMISSIONS)
    skill = SimpleNamespace(departments=[])
    assert await can_access_skill(AsyncMock(), user, skill, "read") is True


@pytest.mark.asyncio
async def test_skill_own_dept_required_when_skill_has_departments():
    user = _user("skill:read:own_dept")
    other_dept = SimpleNamespace(department_id=uuid4())
    skill = SimpleNamespace(departments=[other_dept])
    assert await can_access_skill(AsyncMock(), user, skill, "read") is False


@pytest.mark.asyncio
async def test_workspace_source_denied_to_non_member(monkeypatch):
    user = _user("doc:read:all", "doc:read:own_dept")
    source = SimpleNamespace(id=uuid4(), scope_type="project", scope_id=uuid4())

    async def _no(_db, _user, _wid):
        return False

    monkeypatch.setattr(
        "app.services.permission_engine.can_access_workspace", _no
    )
    assert await can_access_document(AsyncMock(), user, source, "read") is False


@pytest.mark.asyncio
async def test_workspace_source_read_allowed_for_member(monkeypatch):
    user = _user("doc:read:own_dept")
    source = SimpleNamespace(id=uuid4(), scope_type="project", scope_id=uuid4())

    async def _yes(_db, _user, _wid):
        return True

    monkeypatch.setattr(
        "app.services.permission_engine.can_access_workspace", _yes
    )
    assert await can_access_document(AsyncMock(), user, source, "read") is True
