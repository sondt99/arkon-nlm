"""get_current_user must not write to the database (issue #89).

`get_current_user` defaults an employee with no custom role to the "Employee" system role.
It used to do that with a plain `employee.custom_role = sys_role` on a session-attached row,
and `get_db()` commits unconditionally when a request finishes — so the defaulting persisted:
an admin who deliberately cleared someone's role had it written back on that employee's next
request, and every authenticated GET became a write transaction.

The assertions below go through SQLAlchemy's own unit-of-work state rather than the source
text, because that state is exactly what the flush consults to decide whether to emit an
UPDATE. A commented-out assignment leaves no history; so does the fix. Only the bug does.
"""

import uuid

import pytest
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import inspect
from sqlalchemy.orm.attributes import set_committed_value

from app.database.models import Employee, Role
from app.services.auth_service import create_access_token, get_current_user


def _seeded(model, **fields):
    """Build an ORM instance that looks freshly loaded rather than newly constructed.

    `Model(**fields)` records every constructor argument as a pending change, which would
    leave `InstanceState.modified` True before the code under test runs at all. Seeding the
    values as committed is what makes "did this coroutine dirty the row?" an answerable
    question.
    """
    obj = model()
    for key, value in fields.items():
        set_committed_value(obj, key, value)
    return obj


def _employee(**overrides) -> Employee:
    fields = dict(
        id=uuid.uuid4(),
        email="ana@example.com",
        name="Ana",
        role="employee",
        is_active=True,
    )
    fields.update(overrides)
    return _seeded(Employee, **fields)


def _system_role() -> Role:
    return _seeded(
        Role, id=uuid.uuid4(), name="Employee", is_system=True, permissions=["wiki:read"],
    )


def _credentials(employee: Employee) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials=create_access_token(str(employee.id), employee.role, employee.name),
    )


async def _resolve(fake_db, employee: Employee, role):
    """Run the dependency against the in-memory session harness."""
    fake_db.select_rows["employees"] = [employee]
    fake_db.select_rows["roles"] = [role] if role is not None else []
    session = fake_db.factory()
    return await get_current_user(credentials=_credentials(employee), db=session)


@pytest.mark.asyncio
async def test_auto_attached_role_is_not_a_pending_write(fake_db):
    employee = _employee()
    role = _system_role()

    resolved = await _resolve(fake_db, employee, role)

    # The defaulting itself still has to work — this is not a test for "stop doing it".
    assert resolved.custom_role is role

    state = inspect(resolved)
    assert state.attrs.custom_role.history.has_changes() is False, (
        "custom_role is dirty, so get_db()'s commit will persist the auto-attached role and "
        "silently undo an admin who cleared it"
    )
    assert state.modified is False, (
        "the employee row is flagged modified, so every authenticated request flushes an "
        "UPDATE for a value that was only meant to be a default"
    )


@pytest.mark.asyncio
async def test_no_write_is_attempted_against_the_session(fake_db):
    employee = _employee()

    await _resolve(fake_db, employee, _system_role())

    assert fake_db.commits == 0
    assert fake_db.flushes == 0
    assert fake_db.inserted == []


@pytest.mark.asyncio
async def test_an_explicitly_assigned_role_is_left_alone(fake_db):
    """The lookup must not run at all when the employee already has a role."""
    assigned = _seeded(Role, id=uuid.uuid4(), name="Editor", is_system=False, permissions=[])
    employee = _employee()
    set_committed_value(employee, "custom_role", assigned)

    # No "Employee" system role is offered; if the branch ran it would find nothing and the
    # assignment below would be the only thing keeping the assertion true, so also check the
    # statement count.
    resolved = await _resolve(fake_db, employee, None)

    assert resolved.custom_role is assigned
    role_selects = [
        s for s in fake_db.statements
        if getattr(s.get_final_froms()[0], "name", None) == "roles"
    ]
    assert role_selects == []


@pytest.mark.asyncio
async def test_admins_are_not_given_the_employee_role(fake_db):
    admin = _employee(role="admin", email="root@example.com")

    resolved = await _resolve(fake_db, admin, _system_role())

    assert resolved.custom_role is None
    assert inspect(resolved).modified is False
