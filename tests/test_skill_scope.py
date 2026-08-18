"""Skill contribution scope is taken from the skill, not the submitter (#9)."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.services.skill_scope import (
    ensure_reviewer_can_approve,
    ensure_scope_change_allowed,
    normalize_contribution_scope,
    skill_department_ids,
)


def test_existing_department_skill_ignores_claimed_global_scope():
    dept = uuid4()
    skill = SimpleNamespace(departments=[SimpleNamespace(department_id=dept)])
    scope, ids = normalize_contribution_scope("global", None, skill=skill)
    assert scope == "department"
    assert ids == [dept]


def test_invalid_scope_type_rejected_for_new_skill():
    with pytest.raises(HTTPException) as exc:
        normalize_contribution_scope("project", None)
    assert exc.value.status_code == 400


def test_department_scope_requires_ids():
    with pytest.raises(HTTPException) as exc:
        normalize_contribution_scope("department", [])
    assert exc.value.status_code == 400


def test_reviewer_outside_skill_department_is_denied():
    dept_a, dept_b = uuid4(), uuid4()
    skill = SimpleNamespace(departments=[SimpleNamespace(department_id=dept_a)])
    reviewer = SimpleNamespace(role="employee", department_id=dept_b)
    contrib = SimpleNamespace(scope_type="global", scope_ids=None)
    with pytest.raises(HTTPException) as exc:
        ensure_reviewer_can_approve(reviewer, skill, contrib)
    assert exc.value.status_code == 403


def test_reviewer_in_skill_department_is_allowed_even_if_contrib_says_global():
    dept = uuid4()
    skill = SimpleNamespace(departments=[SimpleNamespace(department_id=dept)])
    reviewer = SimpleNamespace(role="employee", department_id=dept)
    contrib = SimpleNamespace(scope_type="global", scope_ids=None)
    ensure_reviewer_can_approve(reviewer, skill, contrib)


def test_non_admin_cannot_pass_final_scope_type():
    with pytest.raises(HTTPException) as exc:
        ensure_scope_change_allowed(SimpleNamespace(role="employee"), "global")
    assert exc.value.status_code == 403


def test_admin_can_widen_scope():
    ensure_scope_change_allowed(SimpleNamespace(role="admin"), "global")


def test_skill_department_ids_normalizes_strings():
    dept = uuid4()
    skill = SimpleNamespace(departments=[SimpleNamespace(department_id=str(dept))])
    assert skill_department_ids(skill) == [dept]
