"""Pin the single-item authorization checks against the list filters.

The audit found that `can_access_skill` returned True where `build_skill_filter`
returned an empty result for the same user — read-one allowed what list-many hid. Two
code paths answered the same authorization question differently, and nothing held them
together.

These tests sweep a user x object matrix and assert the two paths agree for every cell.
That is the property worth pinning: whichever way the matrix grows, a change that widens
one path without the other fails here.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services.permission_engine import (
    build_document_filter,
    build_skill_filter,
    can_access_document,
    can_access_skill,
)

DEPT_A = uuid4()
DEPT_B = uuid4()


def _user(*perms: str, role: str = "employee", dept=DEPT_A):
    return SimpleNamespace(
        id=uuid4(),
        role=role,
        department_id=dept,
        custom_role=SimpleNamespace(permissions=list(perms)),
    )


def _filter_allows(filter_result, obj_dept_ids: set) -> bool:
    """Interpret a build_*_filter result as a per-object visibility predicate.

    The tuple has three meaningful shapes:
        (False, [])        -> no filtering, every object visible
        (True, [dept, ...]) -> global objects, or objects in one of those departments
        (True, None)       -> no permission at all, empty result set

    That last shape is the footgun the audit called out: a caller that checks only the
    first element reads "needs filtering" and then filters by nothing, turning "deny
    everything" into "allow everything". Modelling it explicitly here means a caller
    that regresses to that reading shows up as a pairing failure.
    """
    needs_filter, clauses = filter_result
    if not needs_filter:
        return True
    if clauses is None:
        return False
    allowed = {c for c in clauses if c is not None}
    if not obj_dept_ids:
        return True  # global object
    return bool(obj_dept_ids & allowed)


def _db_returning_departments(dept_ids: set):
    """AsyncMock session whose SourceDepartment query yields dept_ids."""
    db = AsyncMock()
    db.execute.return_value = SimpleNamespace(all=lambda: [(d,) for d in dept_ids])
    return db


# (label, user, object department ids)
_MATRIX = [
    ("admin / global", _user(role="admin"), set()),
    ("admin / other dept", _user(role="admin"), {DEPT_B}),
    ("read:all / other dept", _user("doc:read:all", "skill:read:all"), {DEPT_B}),
    ("read:own_dept / global", _user("doc:read:own_dept", "skill:read:own_dept"), set()),
    ("read:own_dept / own dept", _user("doc:read:own_dept", "skill:read:own_dept"), {DEPT_A}),
    ("read:own_dept / other dept", _user("doc:read:own_dept", "skill:read:own_dept"), {DEPT_B}),
    ("no permission / global", _user(), set()),
    ("no permission / own dept", _user(), {DEPT_A}),
    ("no permission / other dept", _user(), {DEPT_B}),
    ("wrong-action perm / global", _user("doc:edit:all", "skill:edit:all"), set()),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("label,user,obj_depts", _MATRIX, ids=[m[0] for m in _MATRIX])
async def test_document_single_check_agrees_with_list_filter(label, user, obj_depts):
    source = SimpleNamespace(id=uuid4(), scope_type="global", scope_id=None)
    single = await can_access_document(
        _db_returning_departments(obj_depts), user, source, "read"
    )
    listed = _filter_allows(build_document_filter(user, "read"), obj_depts)
    assert single == listed, (
        f"{label}: can_access_document returned {single} but build_document_filter "
        f"implies {listed} — read-one and list-many disagree"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("label,user,obj_depts", _MATRIX, ids=[m[0] for m in _MATRIX])
async def test_skill_single_check_agrees_with_list_filter(label, user, obj_depts):
    skill = SimpleNamespace(
        departments=[SimpleNamespace(department_id=d) for d in obj_depts]
    )
    single = await can_access_skill(AsyncMock(), user, skill, "read")
    listed = _filter_allows(build_skill_filter(user, "read"), obj_depts)
    assert single == listed, (
        f"{label}: can_access_skill returned {single} but build_skill_filter "
        f"implies {listed} — read-one and list-many disagree"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["edit", "delete"])
async def test_destructive_actions_require_the_matching_permission(action):
    """A read grant must never satisfy a write check, on either path."""
    reader = _user("doc:read:all", "skill:read:all")

    skill = SimpleNamespace(departments=[])
    assert await can_access_skill(AsyncMock(), reader, skill, action) is False
    assert _filter_allows(build_skill_filter(reader, action), set()) is False

    source = SimpleNamespace(id=uuid4(), scope_type="global", scope_id=None)
    assert (
        await can_access_document(_db_returning_departments(set()), reader, source, action)
        is False
    )
    assert _filter_allows(build_document_filter(reader, action), set()) is False


def test_no_permission_filter_is_not_mistaken_for_unfiltered():
    """Regression guard for the (True, None) shape itself.

    A caller reading only `needs_filter` would treat "deny everything" as "filter by
    nothing". Assert the shape stays distinguishable from the show-all case.
    """
    nobody = _user()
    assert build_document_filter(nobody, "read") == (True, None)
    assert build_skill_filter(nobody, "read") == (True, None)

    admin = _user(role="admin")
    assert build_document_filter(admin, "read") == (False, [])
    assert build_skill_filter(admin, "read") == (False, [])

    # The two must never be confusable by the first element alone.
    assert build_document_filter(nobody, "read")[0] != build_document_filter(admin, "read")[0]
