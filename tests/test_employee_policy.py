"""Denial paths for employee-manage privilege (#6). No database required."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.employee_policy import (
    ensure_can_assign_role,
    ensure_can_set_password,
    ensure_can_toggle,
    ensure_not_last_admin,
    ensure_password_strength,
)


def _actor(role="employee"):
    return SimpleNamespace(id=uuid4(), role=role)


def _target(role="employee", is_active=True):
    return SimpleNamespace(id=uuid4(), role=role, is_active=is_active)


def test_hr_cannot_promote_to_admin():
    with pytest.raises(HTTPException) as exc:
        ensure_can_assign_role(_actor("employee"), "admin")
    assert exc.value.status_code == 403


def test_hr_cannot_change_existing_role():
    target = _target("employee")
    with pytest.raises(HTTPException) as exc:
        ensure_can_assign_role(_actor("employee"), "admin", target=target)
    assert exc.value.status_code == 403


def test_admin_cannot_change_own_role():
    admin = _actor("admin")
    with pytest.raises(HTTPException) as exc:
        ensure_can_assign_role(admin, "employee", target=admin)
    assert exc.value.status_code == 403


def test_admin_can_promote_someone_else():
    ensure_can_assign_role(_actor("admin"), "admin", target=_target("employee"))


def test_hr_cannot_reset_password():
    with pytest.raises(HTTPException) as exc:
        ensure_can_set_password(_actor("employee"))
    assert exc.value.status_code == 403


def test_short_password_rejected():
    with pytest.raises(HTTPException) as exc:
        ensure_password_strength("short")
    assert exc.value.status_code == 400


def test_cannot_toggle_self():
    user = _actor("employee")
    with pytest.raises(HTTPException) as exc:
        ensure_can_toggle(user, user, active_admin_count=2)
    assert exc.value.status_code == 403


def test_hr_cannot_toggle_admin():
    with pytest.raises(HTTPException) as exc:
        ensure_can_toggle(_actor("employee"), _target("admin"), active_admin_count=2)
    assert exc.value.status_code == 403


def test_cannot_deactivate_last_admin():
    with pytest.raises(HTTPException) as exc:
        ensure_can_toggle(_actor("admin"), _target("admin", True), active_admin_count=1)
    assert exc.value.status_code == 409


def test_cannot_demote_last_admin():
    with pytest.raises(HTTPException) as exc:
        ensure_not_last_admin(_target("admin"), "employee", 1)
    assert exc.value.status_code == 409
