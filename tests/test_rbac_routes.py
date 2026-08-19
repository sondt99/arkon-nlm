"""Privilege-escalation paths through app/routers/rbac.py and app/routers/roles.py.

This is the escalation surface issue #60 asks for first, and the routes behind issues #6
and #18. tests/test_employee_policy.py already covers the policy *functions* in
app/services/employee_policy.py; nothing covered the routers that are supposed to call
them, so deleting a call site was an invisible change. These tests drive the handlers.

The handlers take their permission guard as a default argument, so calling one directly
bypasses the guard. The wiring tests at the bottom therefore pull the guard object off
the registered route (see the ``route_guards`` fixture in conftest.py) and await it, so
both halves — "the route is gated" and "the gate denies" — are exercised for real.

No database: a stub session in the idiom of test_export_api.py.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database.models import AuditLog, Department, Employee, Role
from app.routers import rbac as rbac_router
from app.routers import roles as roles_router
from app.services.auth_service import verify_password

DEPT_ID = uuid.uuid4()


class _FakeSession:
    """Stub AsyncSession: primary-key lookups from a dict, counts from a fixed number."""

    def __init__(self, *rows, admin_count: int = 2):
        self._rows = {(type(r).__name__, r.id): r for r in rows}
        self.admin_count = admin_count
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.statements: list[object] = []
        self.flushes = 0

    async def get(self, model, ident):
        return self._rows.get((model.__name__, ident))

    async def execute(self, statement):
        self.statements.append(statement)
        count = self.admin_count
        return SimpleNamespace(
            scalar_one=lambda: count,
            scalar=lambda: count,
            scalar_one_or_none=lambda: None,
            scalars=lambda: SimpleNamespace(all=lambda: []),
            rowcount=1,
        )

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        self.flushes += 1

    async def refresh(self, _obj):
        pass

    async def commit(self):
        pass

    def audit_entries(self) -> list[AuditLog]:
        return [o for o in self.added if isinstance(o, AuditLog)]


def _actor(*perms: str, role: str = "employee", actor_id=None):
    return SimpleNamespace(
        id=actor_id or uuid.uuid4(),
        name="Actor",
        role=role,
        department_id=DEPT_ID,
        custom_role=SimpleNamespace(permissions=list(perms)) if perms else None,
    )


def _employee_row(role: str = "employee", is_active: bool = True, emp_id=None) -> Employee:
    emp = Employee(
        name="Target",
        email="target@example.com",
        role=role,
        department_id=DEPT_ID,
        password_hash="$2b$04$oldhasholdhasholdhasholdhasholdhasholdhasholdhasholdhash",
    )
    emp.id = emp_id or uuid.uuid4()
    emp.is_active = is_active
    emp.mcp_token_hash = None
    return emp


def _department() -> Department:
    dept = Department(name="Engineering", description=None)
    dept.id = DEPT_ID
    return dept


def _role_row(*perms: str, is_system: bool = False, name: str = "Reviewer") -> Role:
    role = Role(id=uuid.uuid4(), name=name, description=None, permissions=list(perms))
    role.is_system = is_system
    return role


# --------------------------------------------------------------------------- #
# PUT /api/employees/{id} — system role writes (#6)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_employees_manage_cannot_promote_another_user_to_admin():
    """org:employees:manage is an HR-level grant, not an admin-minting grant.

    Without the ensure_can_assign_role call in the handler, this permission is a
    one-request path to full admin.
    """
    target = _employee_row()
    db = _FakeSession(target)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(target.id),
            rbac_router.EmployeeUpdate(role="admin"),
            db=db,
            _user=_actor("org:employees:manage"),
        )
    assert exc.value.status_code == 403
    assert target.role == "employee"


@pytest.mark.asyncio
async def test_employees_manage_cannot_promote_itself_to_admin():
    """Self-escalation is the shortest version of the same attack."""
    actor_id = uuid.uuid4()
    target = _employee_row(emp_id=actor_id)
    db = _FakeSession(target)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(actor_id),
            rbac_router.EmployeeUpdate(role="admin"),
            db=db,
            _user=_actor("org:employees:manage", actor_id=actor_id),
        )
    assert exc.value.status_code == 403
    assert target.role == "employee"


@pytest.mark.asyncio
async def test_employees_manage_cannot_demote_an_admin():
    """Demoting the admins is a denial-of-service on the whole control plane."""
    target = _employee_row(role="admin")
    db = _FakeSession(target, admin_count=5)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(target.id),
            rbac_router.EmployeeUpdate(role="employee"),
            db=db,
            _user=_actor("org:employees:manage"),
        )
    assert exc.value.status_code == 403
    assert target.role == "admin"


@pytest.mark.asyncio
async def test_an_admin_cannot_change_their_own_system_role():
    actor_id = uuid.uuid4()
    target = _employee_row(role="admin", emp_id=actor_id)
    db = _FakeSession(target, admin_count=5)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(actor_id),
            rbac_router.EmployeeUpdate(role="employee"),
            db=db,
            _user=_actor(role="admin", actor_id=actor_id),
        )
    assert exc.value.status_code == 403
    assert target.role == "admin"


@pytest.mark.asyncio
async def test_the_last_active_admin_cannot_be_demoted():
    """Locking every human out of the admin portal is unrecoverable without shell access."""
    target = _employee_row(role="admin")
    db = _FakeSession(target, admin_count=1)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(target.id),
            rbac_router.EmployeeUpdate(role="employee"),
            db=db,
            _user=_actor(role="admin"),
        )
    assert exc.value.status_code == 409
    assert target.role == "admin"


@pytest.mark.asyncio
async def test_an_admin_can_still_promote_someone_else():
    """The allow path, so the denials above cannot be satisfied by refusing everything."""
    target = _employee_row()
    db = _FakeSession(target, admin_count=2)
    result = await rbac_router.update_employee(
        str(target.id),
        rbac_router.EmployeeUpdate(role="admin"),
        db=db,
        _user=_actor(role="admin"),
    )
    assert target.role == "admin"
    assert result["id"] == str(target.id)


@pytest.mark.asyncio
async def test_employees_manage_cannot_reset_another_users_password():
    """Resetting an admin's password is account takeover without touching any role."""
    target = _employee_row(role="admin")
    original = target.password_hash
    db = _FakeSession(target)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(target.id),
            rbac_router.EmployeeUpdate(password="a-long-enough-secret"),
            db=db,
            _user=_actor("org:employees:manage"),
        )
    assert exc.value.status_code == 403
    assert target.password_hash == original


@pytest.mark.asyncio
async def test_password_reset_by_an_admin_stores_only_a_bcrypt_hash():
    target = _employee_row()
    db = _FakeSession(target)
    await rbac_router.update_employee(
        str(target.id),
        rbac_router.EmployeeUpdate(password="a-long-enough-secret"),
        db=db,
        _user=_actor(role="admin"),
    )
    assert "a-long-enough-secret" not in target.password_hash
    assert verify_password("a-long-enough-secret", target.password_hash) is True


@pytest.mark.asyncio
async def test_password_reset_still_enforces_the_length_floor():
    target = _employee_row()
    db = _FakeSession(target)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(target.id),
            rbac_router.EmployeeUpdate(password="short"),
            db=db,
            _user=_actor(role="admin"),
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_update_employee_leaves_omitted_fields_alone():
    """EmployeeUpdate is a partial update. Treating an omitted field as "set to None"
    would silently blank emails and unassign roles on every rename."""
    role_id = uuid.uuid4()
    target = _employee_row()
    target.custom_role_id = role_id
    db = _FakeSession(target)

    await rbac_router.update_employee(
        str(target.id),
        rbac_router.EmployeeUpdate(name="Renamed"),
        db=db,
        _user=_actor(role="admin"),
    )

    assert target.name == "Renamed"
    assert target.email == "target@example.com"
    assert target.role == "employee"
    assert target.custom_role_id == role_id


@pytest.mark.asyncio
async def test_clearing_the_custom_role_requires_an_explicit_null():
    """An explicit `custom_role_id: null` must unassign; the field being absent must not.
    The handler distinguishes the two via model_fields_set."""
    target = _employee_row()
    target.custom_role_id = uuid.uuid4()
    db = _FakeSession(target)

    await rbac_router.update_employee(
        str(target.id),
        rbac_router.EmployeeUpdate(custom_role_id=None),
        db=db,
        _user=_actor(role="admin"),
    )

    assert target.custom_role_id is None


@pytest.mark.asyncio
async def test_update_employee_404s_for_an_unknown_id():
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            str(uuid.uuid4()),
            rbac_router.EmployeeUpdate(name="x"),
            db=db,
            _user=_actor(role="admin"),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_employee_writes_an_audit_entry_naming_the_actor():
    """Role and password changes are the events an incident review needs to attribute."""
    target = _employee_row()
    db = _FakeSession(target)
    actor = _actor(role="admin")

    await rbac_router.update_employee(
        str(target.id),
        rbac_router.EmployeeUpdate(name="Renamed"),
        db=db,
        _user=actor,
    )

    entries = db.audit_entries()
    assert len(entries) == 1
    assert entries[0].principal_id == actor.id
    assert (entries[0].action, entries[0].resource_type) == ("update", "employee")


# --------------------------------------------------------------------------- #
# POST /api/employees
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_create_employee_refuses_to_mint_an_admin_for_a_non_admin_caller():
    db = _FakeSession(_department())
    body = rbac_router.EmployeeCreate(
        name="New",
        email="new@example.com",
        password="a-long-enough-secret",
        role="admin",
        department_id=str(DEPT_ID),
    )
    with pytest.raises(HTTPException) as exc:
        await rbac_router.create_employee(
            body, db=db, _user=_actor("org:employees:manage")
        )
    assert exc.value.status_code == 403
    assert db.added == []


@pytest.mark.asyncio
async def test_create_employee_rejects_an_unknown_system_role():
    """Only 'admin' and 'employee' exist. A free string reaching the column is how an
    unrecognised role ends up treated as the permissive branch by later comparisons."""
    db = _FakeSession(_department())
    body = rbac_router.EmployeeCreate(
        name="New",
        email="new@example.com",
        password="a-long-enough-secret",
        role="superuser",
        department_id=str(DEPT_ID),
    )
    with pytest.raises(HTTPException) as exc:
        await rbac_router.create_employee(body, db=db, _user=_actor(role="admin"))
    assert exc.value.status_code == 400
    assert db.added == []


@pytest.mark.asyncio
async def test_create_employee_rejects_a_weak_password():
    db = _FakeSession(_department())
    body = rbac_router.EmployeeCreate(
        name="New", email="new@example.com", password="short", department_id=str(DEPT_ID)
    )
    with pytest.raises(HTTPException) as exc:
        await rbac_router.create_employee(body, db=db, _user=_actor(role="admin"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_employee_requires_a_password():
    """An account with no password_hash cannot log in, but it can be handed an MCP token —
    a credential with no interactive owner."""
    db = _FakeSession(_department())
    body = rbac_router.EmployeeCreate(
        name="New", email="new@example.com", department_id=str(DEPT_ID)
    )
    with pytest.raises(HTTPException) as exc:
        await rbac_router.create_employee(body, db=db, _user=_actor(role="admin"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_employee_rejects_an_unknown_department():
    db = _FakeSession()
    body = rbac_router.EmployeeCreate(
        name="New",
        email="new@example.com",
        password="a-long-enough-secret",
        department_id=str(uuid.uuid4()),
    )
    with pytest.raises(HTTPException) as exc:
        await rbac_router.create_employee(body, db=db, _user=_actor(role="admin"))
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_create_employee_stores_only_a_hash_of_the_password():
    db = _FakeSession(_department())
    body = rbac_router.EmployeeCreate(
        name="New",
        email="new@example.com",
        password="a-long-enough-secret",
        department_id=str(DEPT_ID),
    )

    result = await rbac_router.create_employee(body, db=db, _user=_actor(role="admin"))

    created = [o for o in db.added if isinstance(o, Employee)]
    assert len(created) == 1
    assert created[0].password_hash != "a-long-enough-secret"
    assert verify_password("a-long-enough-secret", created[0].password_hash) is True
    assert result["email"] == "new@example.com"
    assert "password" not in result


# --------------------------------------------------------------------------- #
# PATCH /api/employees/{id}/toggle and DELETE
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_nobody_can_deactivate_their_own_account():
    actor_id = uuid.uuid4()
    target = _employee_row(role="admin", emp_id=actor_id)
    db = _FakeSession(target, admin_count=5)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.toggle_employee(
            str(actor_id), db=db, _user=_actor(role="admin", actor_id=actor_id)
        )
    assert exc.value.status_code == 403
    assert target.is_active is True


@pytest.mark.asyncio
async def test_employees_manage_cannot_disable_an_admin_account():
    target = _employee_row(role="admin")
    db = _FakeSession(target, admin_count=5)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.toggle_employee(
            str(target.id), db=db, _user=_actor("org:employees:manage")
        )
    assert exc.value.status_code == 403
    assert target.is_active is True


@pytest.mark.asyncio
async def test_the_last_active_admin_cannot_be_deactivated():
    target = _employee_row(role="admin")
    db = _FakeSession(target, admin_count=1)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.toggle_employee(str(target.id), db=db, _user=_actor(role="admin"))
    assert exc.value.status_code == 409
    assert target.is_active is True


@pytest.mark.asyncio
async def test_toggle_flips_an_ordinary_employee_and_reports_the_new_state():
    target = _employee_row(is_active=True)
    db = _FakeSession(target, admin_count=2)
    result = await rbac_router.toggle_employee(
        str(target.id), db=db, _user=_actor("org:employees:manage")
    )
    assert target.is_active is False
    assert result == {"id": str(target.id), "is_active": False}


@pytest.mark.asyncio
async def test_delete_employee_refuses_admin_accounts():
    target = _employee_row(role="admin")
    db = _FakeSession(target)
    with pytest.raises(HTTPException) as exc:
        await rbac_router.delete_employee(
            str(target.id), db=db, _user=_actor("org:employees:manage")
        )
    assert exc.value.status_code == 400
    assert db.deleted == []


@pytest.mark.asyncio
async def test_delete_employee_404s_for_an_unknown_id():
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await rbac_router.delete_employee(
            str(uuid.uuid4()), db=db, _user=_actor("org:employees:manage")
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_department_refuses_while_employees_are_attached():
    """Deleting a populated department would orphan every employee's scope, which the
    document filters read as "no department" — a silent widening."""
    dept = _department()
    db = _FakeSession(dept, admin_count=3)  # the count query answers 3 employees
    with pytest.raises(HTTPException) as exc:
        await rbac_router.delete_department(
            str(dept.id), db=db, _user=_actor("org:departments:manage")
        )
    assert exc.value.status_code == 409
    assert db.deleted == []


@pytest.mark.asyncio
async def test_delete_department_succeeds_when_empty():
    dept = _department()
    db = _FakeSession(dept, admin_count=0)
    result = await rbac_router.delete_department(
        str(dept.id), db=db, _user=_actor("org:departments:manage")
    )
    assert result == {"deleted": True}
    assert db.deleted == [dept]


# --------------------------------------------------------------------------- #
# Self-service MCP token — /api/my/mcp-token
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_self_service_token_can_only_target_the_callers_own_row():
    """This route takes no employee id at all; if it ever grows one, minting another
    person's MCP token becomes a one-request impersonation of their whole KB scope."""
    caller = _actor("wiki:read:own_dept")
    db = _FakeSession()

    await rbac_router.get_my_mcp_token(db=db, current_user=caller)

    bound = [v for s in db.statements for v in s.compile().params.values()]
    assert caller.id in bound


@pytest.mark.asyncio
async def test_self_service_token_rotates_on_every_call():
    """The portal's "Regenerate Token" button calls this. Returning the existing token
    would mean a user who believed they had rotated a leaked credential had not."""
    caller = _actor("wiki:read:own_dept")
    db = _FakeSession()

    first = await rbac_router.get_my_mcp_token(db=db, current_user=caller)
    second = await rbac_router.get_my_mcp_token(db=db, current_user=caller)

    assert first.token != second.token
    assert first.token.startswith("ark_")


@pytest.mark.asyncio
async def test_self_service_token_is_never_stored_in_clear_text():
    caller = _actor("wiki:read:own_dept")
    db = _FakeSession()

    issued = await rbac_router.get_my_mcp_token(db=db, current_user=caller)

    stored = [
        v
        for s in db.statements
        for v in s.compile().params.values()
        if isinstance(v, str)
    ]
    assert issued.token not in stored
    assert stored, "no token digest was written at all"


@pytest.mark.asyncio
async def test_token_status_reports_presence_without_the_digest():
    with_token = _actor()
    with_token.mcp_token_hash = "sha256:deadbeef"
    without = _actor()
    without.mcp_token_hash = None

    assert await rbac_router.get_my_mcp_token_status(current_user=with_token) == {
        "has_token": True
    }
    assert await rbac_router.get_my_mcp_token_status(current_user=without) == {
        "has_token": False
    }


@pytest.mark.asyncio
async def test_admin_token_minting_404s_for_an_unknown_employee():
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await rbac_router.generate_mcp_token(
            str(uuid.uuid4()), db=db, _user=_actor("org:employees:manage")
        )
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# PUT /api/roles/{id} — permission escalation (#18)
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_roles_manage_cannot_grant_a_permission_the_caller_lacks():
    """Otherwise org:roles:manage is a path to full admin: grant yourself
    org:employees:manage on any role you already hold, then use it."""
    role = _role_row("wiki:read:own_dept")
    db = _FakeSession(role)
    with pytest.raises(HTTPException) as exc:
        await roles_router.update_role(
            role.id,
            roles_router.RoleUpdate(
                permissions=["wiki:read:own_dept", "org:employees:manage"]
            ),
            _user=_actor("org:roles:manage", "wiki:read:own_dept"),
            db=db,
        )
    assert exc.value.status_code == 403
    assert "org:employees:manage" in exc.value.detail
    assert role.permissions == ["wiki:read:own_dept"]


@pytest.mark.asyncio
async def test_a_legacy_permission_name_cannot_smuggle_an_escalation():
    """"employees.edit" migrates to org:employees:manage. If the escalation check ran
    before migration, the legacy spelling would walk straight past it."""
    role = _role_row("wiki:read:own_dept")
    db = _FakeSession(role)
    with pytest.raises(HTTPException) as exc:
        await roles_router.update_role(
            role.id,
            roles_router.RoleUpdate(permissions=["employees.edit"]),
            _user=_actor("org:roles:manage", "wiki:read:own_dept"),
            db=db,
        )
    assert exc.value.status_code == 403
    assert "org:employees:manage" in exc.value.detail


@pytest.mark.asyncio
async def test_roles_manage_can_grant_a_permission_it_holds_itself():
    """The allow path — the guard is "no escalation", not "no delegation"."""
    role = _role_row("wiki:read:own_dept")
    db = _FakeSession(role)

    result = await roles_router.update_role(
        role.id,
        roles_router.RoleUpdate(permissions=["wiki:read:own_dept", "org:audit:read"]),
        _user=_actor("org:roles:manage", "wiki:read:own_dept", "org:audit:read"),
        db=db,
    )

    assert set(result.permissions) == {"wiki:read:own_dept", "org:audit:read"}


@pytest.mark.asyncio
async def test_narrowing_a_role_is_not_treated_as_escalation():
    """Removing a permission the caller does not hold must stay allowed, or an HR-scoped
    role manager can never revoke anything they cannot grant."""
    role = _role_row("wiki:read:own_dept", "org:employees:manage")
    db = _FakeSession(role)

    result = await roles_router.update_role(
        role.id,
        roles_router.RoleUpdate(permissions=["wiki:read:own_dept"]),
        _user=_actor("org:roles:manage", "wiki:read:own_dept"),
        db=db,
    )

    assert result.permissions == ["wiki:read:own_dept"]


@pytest.mark.asyncio
async def test_permissions_on_a_system_role_are_locked_even_for_an_admin():
    """get_current_user auto-attaches the "Employee" system role to everyone without a
    custom role, so one PUT here grants a permission to the entire company. Locking only
    the *name* left that open (#18)."""
    role = _role_row("wiki:read:own_dept", is_system=True, name="Employee")
    db = _FakeSession(role)
    with pytest.raises(HTTPException) as exc:
        await roles_router.update_role(
            role.id,
            roles_router.RoleUpdate(
                permissions=["wiki:read:own_dept", "org:employees:manage"]
            ),
            _user=_actor(role="admin"),
            db=db,
        )
    assert exc.value.status_code == 400
    assert role.permissions == ["wiki:read:own_dept"]


@pytest.mark.asyncio
async def test_a_system_role_can_still_be_re_described():
    """Only the name and the permission set are frozen; the description is editable."""
    role = _role_row("wiki:read:own_dept", is_system=True, name="Employee")
    db = _FakeSession(role)

    result = await roles_router.update_role(
        role.id,
        roles_router.RoleUpdate(name="Renamed", description="Default for everyone"),
        _user=_actor(role="admin"),
        db=db,
    )

    assert result.name == "Employee", "a system role was renamed"
    assert result.description == "Default for everyone"


@pytest.mark.asyncio
async def test_an_admin_may_grant_any_permission_on_a_custom_role():
    role = _role_row()
    db = _FakeSession(role)

    result = await roles_router.update_role(
        role.id,
        roles_router.RoleUpdate(permissions=["org:employees:manage"]),
        _user=_actor(role="admin"),
        db=db,
    )

    assert result.permissions == ["org:employees:manage"]


# --------------------------------------------------------------------------- #
# Route wiring — the guard the handler tests deliberately bypass
# --------------------------------------------------------------------------- #

RBAC_ROUTE_PERMISSIONS = [
    ("GET", "/api/departments", "org:departments:read"),
    ("POST", "/api/departments", "org:departments:manage"),
    ("PUT", "/api/departments/{dept_id}", "org:departments:manage"),
    ("DELETE", "/api/departments/{dept_id}", "org:departments:manage"),
    ("GET", "/api/employees", "org:employees:read"),
    ("POST", "/api/employees", "org:employees:manage"),
    ("PUT", "/api/employees/{emp_id}", "org:employees:manage"),
    ("DELETE", "/api/employees/{emp_id}", "org:employees:manage"),
    ("PATCH", "/api/employees/{emp_id}/toggle", "org:employees:manage"),
    ("POST", "/api/employees/{emp_id}/token", "org:employees:manage"),
    ("DELETE", "/api/employees/{emp_id}/token", "org:employees:manage"),
    ("PUT", "/api/roles/{role_id}", "org:roles:manage"),
    ("POST", "/api/roles", "org:roles:manage"),
    ("DELETE", "/api/roles/{role_id}", "org:roles:manage"),
]


@pytest.mark.parametrize(
    "method,path,permission",
    RBAC_ROUTE_PERMISSIONS,
    ids=[f"{m} {p}" for m, p, _ in RBAC_ROUTE_PERMISSIONS],
)
def test_rbac_route_carries_its_documented_permission(
    route_guards, method, path, permission
):
    """A read permission on a write route is the mistake this catches.

    The guard is read out of the registered route's dependency tree, so removing
    require_permission(...) from the handler empties this list.
    """
    assert [p for p, _ in route_guards(method, path)] == [permission]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,permission",
    RBAC_ROUTE_PERMISSIONS,
    ids=[f"{m} {p}" for m, p, _ in RBAC_ROUTE_PERMISSIONS],
)
async def test_rbac_route_guard_denies_a_user_without_the_permission(
    route_guards, method, path, permission
):
    """Awaits the real guard object off the route, so this covers require_permission
    itself as well as the wiring."""
    (_, guard), = route_guards(method, path)
    with pytest.raises(HTTPException) as exc:
        await guard(_actor("wiki:read:own_dept"))
    assert exc.value.status_code == 403
    assert await guard(_actor(permission)) is not None
    assert await guard(_actor(role="admin")) is not None


# --------------------------------------------------------------------------- #
# The two escalation holes found while writing this file.
#
# `Employee.role` was guarded by ensure_can_assign_role, but the CUSTOM role confers the
# same authority through the permission engine and was written straight from the request
# body. And the anti-escalation rule existed on update_role but not create_role, so it
# could be walked around by creating a role rather than editing one.
#
# Chained, `org:roles:manage` + `org:employees:manage` reached every permission in
# ALL_PERMISSIONS: create an over-broad role, then assign it to yourself.
# --------------------------------------------------------------------------- #

ORG_WIDE = ["org:employees:manage", "org:settings:manage", "doc:read:all"]


def _manager(perms, *, emp_id=None):
    """A non-admin holding exactly `perms` via a custom role."""
    return SimpleNamespace(
        id=emp_id or uuid.uuid4(),
        role="employee",
        custom_role=SimpleNamespace(id=uuid.uuid4(), permissions=list(perms)),
        custom_role_id=None,
    )


@pytest.mark.asyncio
async def test_create_role_cannot_grant_permissions_the_caller_lacks():
    """create_role had no escalation guard at all — only update_role did."""
    actor = _manager(["org:roles:manage", "wiki:read:own_dept"])
    body = SimpleNamespace(
        name="Sneaky", description=None, permissions=list(ORG_WIDE),
    )

    with pytest.raises(HTTPException) as exc:
        await roles_router.create_role(body=body, _user=actor, db=_FakeSession())

    assert exc.value.status_code == 403
    assert "do not hold yourself" in exc.value.detail
    for p in ORG_WIDE:
        assert p in exc.value.detail


@pytest.mark.asyncio
async def test_create_role_still_allows_granting_what_the_caller_holds():
    """The guard must not make role creation useless."""
    actor = _manager(["org:roles:manage", "wiki:read:all"])
    body = SimpleNamespace(name="Reader", description=None, permissions=["wiki:read:all"])

    out = await roles_router.create_role(body=body, _user=actor, db=_FakeSession())

    assert out.permissions == ["wiki:read:all"]


@pytest.mark.asyncio
async def test_an_admin_may_still_create_any_role():
    admin = SimpleNamespace(id=uuid.uuid4(), role="admin", custom_role=None, custom_role_id=None)
    body = SimpleNamespace(name="Anything", description=None, permissions=list(ORG_WIDE))

    out = await roles_router.create_role(body=body, _user=admin, db=_FakeSession())

    assert set(out.permissions) == set(ORG_WIDE)


@pytest.mark.asyncio
async def test_cannot_assign_a_custom_role_richer_than_your_own():
    """The second half of the chain: assigning the over-broad role to somebody."""
    actor = _manager(["org:employees:manage"])
    rich = Role(id=uuid.uuid4(), name="Rich", permissions=list(ORG_WIDE), is_system=False)
    target = Employee(
        id=uuid.uuid4(), name="T", email="t@x", password_hash="h",
        role="employee", department_id=DEPT_ID,
    )
    body = SimpleNamespace(
        name=None, email=None, department_id=None, role=None, password=None,
        custom_role_id=str(rich.id),
        model_fields_set={"custom_role_id"},
    )

    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            emp_id=str(target.id), body=body,
            db=_FakeSession(target, rich), _user=actor,
        )

    assert exc.value.status_code == 403
    assert target.custom_role_id is None, "the write must not land"


@pytest.mark.asyncio
async def test_cannot_grant_yourself_a_custom_role_you_already_qualify_for():
    """Self-assignment is refused even when the permissions would not widen.

    `ensure_can_assign_role` already refuses self-promotion of the system role for the same
    reason: an employee-manager editing their own authority is the shape of the attack,
    regardless of whether this particular grant happens to be a no-op.
    """
    me_id = uuid.uuid4()
    actor = _manager(["org:employees:manage", "wiki:read:all"], emp_id=me_id)
    role = Role(id=uuid.uuid4(), name="Same", permissions=["wiki:read:all"], is_system=False)
    me = Employee(
        id=me_id, name="Me", email="me@x", password_hash="h",
        role="employee", department_id=DEPT_ID,
    )
    body = SimpleNamespace(
        name=None, email=None, department_id=None, role=None, password=None,
        custom_role_id=str(role.id), model_fields_set={"custom_role_id"},
    )

    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            emp_id=str(me.id), body=body, db=_FakeSession(me, role), _user=actor,
        )

    assert exc.value.status_code == 403
    assert "your own custom role" in exc.value.detail


@pytest.mark.asyncio
async def test_create_employee_cannot_mint_someone_richer_than_the_creator():
    """The same hole existed on create, where there is no self-assignment angle at all."""
    actor = _manager(["org:employees:manage"])
    rich = Role(id=uuid.uuid4(), name="Rich", permissions=list(ORG_WIDE), is_system=False)
    body = SimpleNamespace(
        name="New", email="n@x", password="hunter22hunter", role="employee",
        department_id=str(DEPT_ID), custom_role_id=str(rich.id),
    )

    dept = Department(id=DEPT_ID, name="Eng")
    with pytest.raises(HTTPException) as exc:
        await rbac_router.create_employee(
            body=body, db=_FakeSession(rich, dept), _user=actor,
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_unknown_custom_role_is_a_404_not_a_silent_write():
    actor = SimpleNamespace(id=uuid.uuid4(), role="admin", custom_role=None, custom_role_id=None)
    target = Employee(
        id=uuid.uuid4(), name="T", email="t@x", password_hash="h",
        role="employee", department_id=DEPT_ID,
    )
    body = SimpleNamespace(
        name=None, email=None, department_id=None, role=None, password=None,
        custom_role_id=str(uuid.uuid4()), model_fields_set={"custom_role_id"},
    )

    with pytest.raises(HTTPException) as exc:
        await rbac_router.update_employee(
            emp_id=str(target.id), body=body, db=_FakeSession(target), _user=actor,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_clearing_a_custom_role_is_always_allowed():
    """Removing authority is not escalation, and must stay possible."""
    actor = _manager(["org:employees:manage"])
    target = Employee(
        id=uuid.uuid4(), name="T", email="t@x", password_hash="h",
        role="employee", department_id=DEPT_ID,
    )
    target.custom_role_id = uuid.uuid4()
    body = SimpleNamespace(
        name=None, email=None, department_id=None, role=None, password=None,
        custom_role_id=None, model_fields_set={"custom_role_id"},
    )

    await rbac_router.update_employee(
        emp_id=str(target.id), body=body, db=_FakeSession(target), _user=actor,
    )

    assert target.custom_role_id is None


# --------------------------------------------------------------------------- #
# Audit rows must name the object they describe.
#
# `default=uuid.uuid4` on the id column is applied at INSERT, so reading `obj.id` between
# `db.add()` and `db.flush()` yields None — and `str(None)` is the string "None". Both
# create endpoints wrote that as the audit row's resource_id while returning the real UUID
# in the response, so the trail recorded that *an* employee was created but never which.
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_create_department_audits_the_real_id():
    admin = SimpleNamespace(id=uuid.uuid4(), role="admin", custom_role=None, custom_role_id=None)
    db = _FakeSession()
    body = SimpleNamespace(name="Platform", description=None)

    out = await rbac_router.create_department(body=body, _user=admin, db=db)

    entries = [r for r in db.added if isinstance(r, AuditLog)]
    assert len(entries) == 1
    assert entries[0].resource_id not in (None, "None", "")
    assert entries[0].resource_id == str(out["id"])


@pytest.mark.asyncio
async def test_create_employee_audits_the_real_id():
    admin = SimpleNamespace(id=uuid.uuid4(), role="admin", custom_role=None, custom_role_id=None)
    dept = Department(id=DEPT_ID, name="Eng")
    db = _FakeSession(dept)
    body = SimpleNamespace(
        name="New", email="n@x", password="hunter22hunter", role="employee",
        department_id=str(DEPT_ID), custom_role_id=None,
    )

    out = await rbac_router.create_employee(body=body, _user=admin, db=db)

    entries = [r for r in db.added if isinstance(r, AuditLog)]
    assert len(entries) == 1
    # Both halves are needed. Comparing only audit-id to response-id is vacuous: under the
    # bug BOTH are the string "None", so they match and the test passes.
    assert entries[0].resource_id not in (None, "None", "")
    assert entries[0].resource_id == str(out["id"])
