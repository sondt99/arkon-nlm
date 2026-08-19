"""app/routers/roles.py, app/routers/audit.py and app/routers/notes.py.

Three of the routers issue #60 lists with zero tests. The escalation half of roles.py
lives in test_rbac_routes.py next to the rest of the RBAC security surface; what is left
here is the shape-and-validation half, plus the audit log — which is the only record of
who did what, so a filter bug in it is an accountability bug.

Coverage note: these are per-route behaviour tests, not an exhaustive contract. notes.py
in particular has no scope column at all, so nothing here can assert row-level
visibility; the only authorization it has is the permission on the route.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database.models import AuditLog, Employee, Note, Role
from app.routers import audit as audit_router
from app.routers import notes as notes_router
from app.routers import roles as roles_router
from app.services.permissions import (
    ALL_PERMISSIONS,
    PERMISSION_GROUPS,
)

NOW = datetime(2026, 8, 18, 9, 30, tzinfo=timezone.utc)


def _user(*perms: str, role: str = "employee"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Actor",
        role=role,
        department_id=uuid.uuid4(),
        custom_role=SimpleNamespace(permissions=list(perms)) if perms else None,
    )


class _FakeSession:
    """Counts for count(*) queries, a fixed row list for everything else."""

    def __init__(self, *rows, results=(), total=0, rowcount=1):
        self._rows = {(type(r).__name__, r.id): r for r in rows}
        self.results = list(results)
        self.total = total
        self.rowcount = rowcount
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.statements: list[object] = []

    async def get(self, model, ident):
        return self._rows.get((model.__name__, ident))

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(statement)
        if "count(" in sql.lower():
            return SimpleNamespace(scalar_one=lambda: self.total, scalar=lambda: self.total)
        rows = self.results
        return SimpleNamespace(
            all=lambda: rows,
            scalars=lambda: SimpleNamespace(all=lambda: rows),
            rowcount=self.rowcount,
        )

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if getattr(obj, "created_at", None) is None:
            obj.created_at = NOW
            obj.updated_at = NOW
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)

    async def flush(self):
        pass

    async def refresh(self, _obj):
        pass

    async def commit(self):
        pass

    def added_of(self, model) -> list:
        return [o for o in self.added if isinstance(o, model)]


def _role_row(*perms: str, is_system: bool = False, name: str = "Reviewer") -> Role:
    role = Role(id=uuid.uuid4(), name=name, description="desc", permissions=list(perms))
    role.is_system = is_system
    return role


# --------------------------------------------------------------------------- #
# GET /api/roles/permissions
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_the_permission_catalog_exposes_every_declared_permission():
    """The handler indexes PERMISSION_LABELS and the group map with a bare [k], so a
    permission added to ALL_PERMISSIONS without a label or a group 500s this route — and
    the admin UI's whole role editor with it."""
    result = await roles_router.list_permissions(_user("org:roles:read"))

    assert {p.key for p in result} == set(ALL_PERMISSIONS)
    assert all(p.label for p in result), "a permission is missing its label"
    assert all(p.group in PERMISSION_GROUPS for p in result)


@pytest.mark.asyncio
async def test_the_permission_catalog_does_not_invent_permissions():
    """A key here that require_permission cannot satisfy is an unassignable checkbox."""
    result = await roles_router.list_permissions(_user("org:roles:read"))
    assert len(result) == len(ALL_PERMISSIONS)


# --------------------------------------------------------------------------- #
# POST /api/roles
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_create_role_rejects_unknown_permission_strings():
    """A typo'd permission is stored but can never match, so the role silently grants
    less than the admin who created it believes."""
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await roles_router.create_role(
            roles_router.RoleCreate(name="Typo", permissions=["wiki:red:all"]),
            _user(role="admin"),
            db=db,
        )
    assert exc.value.status_code == 400
    assert "wiki:red:all" in exc.value.detail
    assert db.added_of(Role) == []


@pytest.mark.asyncio
async def test_create_role_normalises_legacy_permission_names():
    """Legacy dotted names still arrive from old clients and seed data. Storing them raw
    would leave a role whose permissions never match the colon-format checks."""
    db = _FakeSession()
    result = await roles_router.create_role(
        roles_router.RoleCreate(name="HR", permissions=["employees.read"]),
        _user(role="admin"),
        db=db,
    )
    assert result.permissions == ["org:employees:read"]
    assert db.added_of(Role)[0].permissions == ["org:employees:read"]


@pytest.mark.asyncio
async def test_create_role_is_never_a_system_role():
    """is_system is what protects the auto-attached Employee role from edits (#18); a
    caller must not be able to mint a role that inherits that protection."""
    db = _FakeSession()
    result = await roles_router.create_role(
        roles_router.RoleCreate(name="Employee", permissions=[]),
        _user(role="admin"),
        db=db,
    )
    assert result.is_system is False


@pytest.mark.asyncio
async def test_create_role_trims_the_name_and_audits_the_creation():
    db = _FakeSession()
    actor = _user(role="admin")
    result = await roles_router.create_role(
        roles_router.RoleCreate(name="  Reviewer  ", permissions=["org:audit:read"]),
        actor,
        db=db,
    )

    assert result.name == "Reviewer"
    entries = db.added_of(AuditLog)
    assert len(entries) == 1
    assert (entries[0].action, entries[0].resource_type) == ("create", "role")
    assert entries[0].principal_id == actor.id
    assert entries[0].resource_id == str(result.id)


# --------------------------------------------------------------------------- #
# PUT / DELETE /api/roles/{id}
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_update_role_404s_for_an_unknown_id():
    with pytest.raises(HTTPException) as exc:
        await roles_router.update_role(
            uuid.uuid4(),
            roles_router.RoleUpdate(name="x"),
            _user(role="admin"),
            db=_FakeSession(),
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_update_role_rejects_unknown_permission_strings():
    role = _role_row("org:audit:read")
    db = _FakeSession(role)
    with pytest.raises(HTTPException) as exc:
        await roles_router.update_role(
            role.id,
            roles_router.RoleUpdate(permissions=["org:audit:reed"]),
            _user(role="admin"),
            db=db,
        )
    assert exc.value.status_code == 400
    assert role.permissions == ["org:audit:read"]


@pytest.mark.asyncio
async def test_delete_role_refuses_system_roles():
    """Deleting the auto-attached Employee role would drop every employee to the
    hardcoded fallback permission set without anyone changing a role assignment."""
    role = _role_row("wiki:read:own_dept", is_system=True, name="Employee")
    db = _FakeSession(role)
    with pytest.raises(HTTPException) as exc:
        await roles_router.delete_role(role.id, _user(role="admin"), db=db)
    assert exc.value.status_code == 400
    assert db.deleted == []


@pytest.mark.asyncio
async def test_delete_role_404s_for_an_unknown_id():
    with pytest.raises(HTTPException) as exc:
        await roles_router.delete_role(uuid.uuid4(), _user(role="admin"), db=_FakeSession())
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_role_removes_a_custom_role_and_audits_it():
    role = _role_row("org:audit:read")
    db = _FakeSession(role)
    actor = _user("org:roles:manage")

    await roles_router.delete_role(role.id, actor, db=db)

    assert db.deleted == [role]
    entries = db.added_of(AuditLog)
    assert (entries[0].action, entries[0].resource_type) == ("delete", "role")
    assert entries[0].resource_id == str(role.id)


@pytest.mark.asyncio
async def test_listing_roles_migrates_stored_legacy_names_on_the_way_out():
    """Existing rows still hold dotted names. The API must present one vocabulary, or the
    role editor round-trips a permission the checks do not recognise."""
    db = _FakeSession(results=[_role_row("roles.read", "org:audit:read")])
    result = await roles_router.list_roles(_user("org:roles:read"), db=db)
    assert result[0].permissions == ["org:audit:read", "org:roles:read"]


# --------------------------------------------------------------------------- #
# GET /api/audit/log
# --------------------------------------------------------------------------- #

def _audit_row(action: str = "update", decision: str = "ALLOW") -> AuditLog:
    entry = AuditLog(
        principal_id=uuid.uuid4(),
        principal_type="human",
        action=action,
        resource_type="employee",
        resource_id=str(uuid.uuid4()),
        decision=decision,
        reason="because",
    )
    entry.id = uuid.uuid4()
    entry.timestamp = NOW
    return entry


def _employee_row(name: str = "Mai") -> Employee:
    emp = Employee(name=name, email="mai@example.com", role="employee")
    emp.id = uuid.uuid4()
    return emp


@pytest.mark.asyncio
async def test_audit_log_pairs_each_entry_with_its_principal():
    entry, employee = _audit_row(), _employee_row()
    db = _FakeSession(results=[(entry, employee)], total=1)

    result = await audit_router.get_audit_log(
        page=1, page_size=50, db=db, _user=_user("org:audit:read")
    )

    assert result.total == 1
    assert result.page == 1 and result.page_size == 50
    item = result.items[0]
    assert item.id == str(entry.id)
    assert item.principal_name == "Mai"
    assert item.principal_email == "mai@example.com"
    assert item.action == "update"
    assert item.decision == "ALLOW"


@pytest.mark.asyncio
async def test_audit_log_survives_a_deleted_principal():
    """principal_id has no FK guarantee once an employee row is gone. A None employee
    must render as an unattributed entry, not 500 the only accountability view there is."""
    db = _FakeSession(results=[(_audit_row(), None)], total=1)

    result = await audit_router.get_audit_log(
        page=1, page_size=50, db=db, _user=_user("org:audit:read")
    )

    assert result.items[0].principal_name is None
    assert result.items[0].principal_email is None


@pytest.mark.asyncio
async def test_audit_log_filters_reach_the_count_query_too():
    """If a filter is applied only to the page query, `total` describes a different set
    than `items` — the classic pagination bug, here producing a filtered view that claims
    thousands of results and pages into emptiness.

    Asserted on the count statement's bind parameters rather than its SQL text: the
    subquery projects every AuditLog column, so the words "action" and "decision" appear
    in the compiled string whether or not anything is filtered on them.
    """
    db = _FakeSession(results=[], total=0)

    await audit_router.get_audit_log(
        page=1,
        page_size=50,
        action="delete",
        decision="DENY",
        resource_type="employee",
        db=db,
        _user=_user("org:audit:read"),
    )

    count_stmt = next(s for s in db.statements if "count(" in str(s).lower())
    bound = set(count_stmt.compile().params.values())
    assert {"delete", "DENY", "employee"} <= bound


@pytest.mark.asyncio
async def test_audit_log_applies_the_requested_page_offset():
    db = _FakeSession(results=[], total=500)

    result = await audit_router.get_audit_log(
        page=3, page_size=25, db=db, _user=_user("org:audit:read")
    )

    page_stmt = next(s for s in db.statements if "count(" not in str(s).lower())
    bound = set(page_stmt.compile().params.values())
    assert {25, 50} <= bound, "page 3 of 25 must skip the first 50 rows"
    assert result.page == 3 and result.page_size == 25


@pytest.mark.asyncio
async def test_audit_log_rejects_a_malformed_principal_filter():
    """An unparseable UUID must not reach the driver as a 500."""
    db = _FakeSession(results=[], total=0)
    with pytest.raises(ValueError):
        await audit_router.get_audit_log(
            page=1, page_size=50, principal_id="not-a-uuid", db=db,
            _user=_user("org:audit:read"),
        )


# --------------------------------------------------------------------------- #
# /api/notes
# --------------------------------------------------------------------------- #

def _note_row(title: str = "Standup") -> Note:
    note = Note(title=title, content="body", note_type="human")
    note.id = uuid.uuid4()
    note.created_at = NOW
    note.updated_at = NOW
    return note


@pytest.mark.asyncio
async def test_list_notes_orders_newest_first():
    """The notes pane shows the most recent entry; losing the ordering buries it."""
    db = _FakeSession(results=[_note_row()])

    result = await notes_router.list_notes(db=db, _user=_user())

    assert len(result) == 1
    assert result[0].title == "Standup"
    sql = str(db.statements[0]).upper()
    assert "ORDER BY NOTES.CREATED_AT DESC" in sql


@pytest.mark.asyncio
async def test_create_note_returns_the_persisted_row():
    db = _FakeSession()

    result = await notes_router.create_note(
        notes_router.NoteCreate(title="Retro", content="what went wrong"),
        db=db,
        _user=_user("wiki:write:own_dept"),
    )

    stored = db.added_of(Note)
    assert len(stored) == 1
    assert stored[0].content == "what went wrong"
    assert result.id == stored[0].id
    assert result.note_type == "human", "the default note_type changed"
    assert result.created_at == NOW.isoformat()


@pytest.mark.asyncio
async def test_delete_note_404s_when_no_row_matched():
    """delete_by_id reports rowcount; treating 0 as success makes the UI drop a note from
    the list that is still in the database."""
    db = _FakeSession(rowcount=0)
    with pytest.raises(HTTPException) as exc:
        await notes_router.delete_note(
            uuid.uuid4(), db=db, _user=_user("wiki:delete:own_dept")
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_note_reports_success_when_a_row_was_removed():
    db = _FakeSession(rowcount=1)
    result = await notes_router.delete_note(
        uuid.uuid4(), db=db, _user=_user("wiki:delete:own_dept")
    )
    assert result == {"deleted": True}


# --------------------------------------------------------------------------- #
# Route wiring
# --------------------------------------------------------------------------- #

ROUTE_PERMISSIONS = [
    ("GET", "/api/roles", "org:roles:read"),
    ("GET", "/api/roles/permissions", "org:roles:read"),
    ("GET", "/api/audit/log", "org:audit:read"),
    ("POST", "/api/notes", "wiki:write"),
    ("DELETE", "/api/notes/{note_id}", "wiki:delete"),
]


@pytest.mark.parametrize(
    "method,path,permission",
    ROUTE_PERMISSIONS,
    ids=[f"{m} {p}" for m, p, _ in ROUTE_PERMISSIONS],
)
def test_route_carries_its_documented_permission(route_guards, method, path, permission):
    assert [p for p, _ in route_guards(method, path)] == [permission]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,permission",
    ROUTE_PERMISSIONS,
    ids=[f"{m} {p}" for m, p, _ in ROUTE_PERMISSIONS],
)
async def test_route_guard_denies_a_user_without_the_permission(
    route_guards, method, path, permission
):
    (_, guard), = route_guards(method, path)
    with pytest.raises(HTTPException) as exc:
        await guard(_user("doc:read:own_dept"))
    assert exc.value.status_code == 403
    assert await guard(_user(role="admin")) is not None


@pytest.mark.asyncio
async def test_the_audit_log_is_not_readable_with_a_mere_settings_grant(route_guards):
    """org:audit:read is the only key to the audit log. Nothing adjacent — settings,
    employees, roles — may substitute for it, or the log is readable by the same people
    whose actions it records."""
    (_, guard), = route_guards("GET", "/api/audit/log")
    for adjacent in ("org:settings:manage", "org:employees:manage", "org:roles:manage"):
        with pytest.raises(HTTPException) as exc:
            await guard(_user(adjacent))
        assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_note_writes_accept_either_scope_of_the_wiki_permission(route_guards):
    """require_permission("wiki:write") is the two-part form: it matches own_dept or all,
    and must not be satisfied by wiki:read."""
    (_, guard), = route_guards("POST", "/api/notes")
    assert await guard(_user("wiki:write:own_dept")) is not None
    assert await guard(_user("wiki:write:all")) is not None
    with pytest.raises(HTTPException):
        await guard(_user("wiki:read:all"))
