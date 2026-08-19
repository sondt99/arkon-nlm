"""Behavioural tests for app/routers/auth.py — login, profile, password change, status.

Issue #60 named these routes explicitly: login is the one endpoint that is reachable
without a credential and issues one, so every mistake in it is a pre-auth mistake. The
meta-test in test_route_auth_coverage.py deliberately allowlists /api/auth/login and
/api/auth/status as public and therefore proves nothing about them; this file is that
gap.

No database here — the handlers are driven directly with a stub session, in the idiom of
test_export_api.py. bcrypt hashes are real (cost 4) so the password comparison under test
is the production one, not a stub.
"""

import uuid
from types import SimpleNamespace

import bcrypt
import pytest
from fastapi import HTTPException

from app.routers import auth as auth_router
from app.services.auth_service import decode_access_token, verify_password

PASSWORD = "correct horse battery"


class _FakeSession:
    """Answers the two queries the auth router issues.

    ``authenticate_employee``/``get_current_user`` select an Employee and call
    ``scalar_one_or_none``; ``_get_workspace_memberships`` joins project_members and
    calls ``all()``. Dispatching on the compiled SQL keeps one stub for both.
    """

    def __init__(self, employee=None, memberships=()):
        self.employee = employee
        self.memberships = list(memberships)
        self.statements: list[object] = []
        self.flushes = 0

    async def execute(self, statement):
        sql = str(statement)
        self.statements.append(statement)
        if "project_members" in sql:
            return SimpleNamespace(all=lambda: self.memberships)
        return SimpleNamespace(scalar_one_or_none=lambda: self.employee)

    async def flush(self):
        self.flushes += 1


def _hash(password: str) -> str:
    # Cost 4: this suite tests the comparison, not bcrypt's work factor.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(4)).decode("utf-8")


def _employee(
    *perms: str,
    password: str | None = PASSWORD,
    role: str = "employee",
    mcp_token_hash: str | None = None,
    is_active: bool = True,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Mai Nguyen",
        email="mai@example.com",
        role=role,
        department_id=uuid.uuid4(),
        department=SimpleNamespace(name="Engineering"),
        custom_role=SimpleNamespace(permissions=list(perms)) if perms else None,
        is_active=is_active,
        mcp_token_hash=mcp_token_hash,
        password_hash=_hash(password) if password else None,
    )


def _request(ip: str = "10.0.0.9"):
    return SimpleNamespace(client=SimpleNamespace(host=ip))


def _login(email: str = "mai@example.com", password: str = PASSWORD):
    return auth_router.LoginRequest(email=email, password=password)


# --------------------------------------------------------------------------- #
# login — denial paths
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_login_rejects_a_wrong_password():
    db = _FakeSession(employee=_employee())
    with pytest.raises(HTTPException) as exc:
        await auth_router.login(_login(password="hunter2"), _request(), db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_rejects_an_unknown_email():
    db = _FakeSession(employee=None)
    with pytest.raises(HTTPException) as exc:
        await auth_router.login(_login(email="nobody@example.com"), _request(), db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_does_not_reveal_which_half_of_the_credential_was_wrong():
    """An unknown account and a bad password must be indistinguishable to the client.

    Splitting these into "user not found" / "wrong password" turns the public login
    route into an account-enumeration oracle for the whole employee directory.
    """
    with pytest.raises(HTTPException) as unknown:
        await auth_router.login(_login(), _request(), db=_FakeSession(employee=None))
    with pytest.raises(HTTPException) as wrong_pw:
        await auth_router.login(
            _login(password="hunter2"), _request(), db=_FakeSession(employee=_employee())
        )

    assert unknown.value.status_code == wrong_pw.value.status_code == 401
    assert unknown.value.detail == wrong_pw.value.detail


@pytest.mark.asyncio
async def test_login_rejects_an_account_that_has_no_password_hash():
    """SSO-only / not-yet-provisioned rows must 401, not raise inside bcrypt."""
    db = _FakeSession(employee=_employee(password=None))
    with pytest.raises(HTTPException) as exc:
        await auth_router.login(_login(), _request(), db=db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_login_query_filters_deactivated_accounts_in_sql():
    """A deactivated employee must never reach the password comparison.

    The filter lives in the SELECT, so it is asserted on the statement's WHERE clause
    specifically — ``select(Employee)`` also *projects* is_active, so searching the whole
    compiled string would pass even with no filter at all. Dropping the filter would let
    a revoked account log in with its old password.
    """
    db = _FakeSession(employee=_employee())
    await auth_router.login(_login(), _request(), db=db)
    where = str(db.statements[0].whereclause)
    assert "employees.is_active" in where
    assert "employees.email" in where


@pytest.mark.asyncio
async def test_login_is_rate_limited_before_the_password_is_checked(monkeypatch):
    """The limiter has to run first or it cannot cap credential-stuffing attempts.

    Rate limiting after the bcrypt comparison still burns ~250 ms of CPU per attempt,
    which is the denial-of-service half of the same problem.
    """
    from app.services import rate_limiter

    async def _deny(*_args, **_kwargs):
        raise HTTPException(status_code=429, detail="Too many login attempts.")

    monkeypatch.setattr(rate_limiter, "check_rate_limit", _deny)

    db = _FakeSession(employee=_employee())
    with pytest.raises(HTTPException) as exc:
        await auth_router.login(_login(), _request(), db=db)

    assert exc.value.status_code == 429
    assert db.statements == [], "the employee row was queried despite the rate limit"


# --------------------------------------------------------------------------- #
# login — happy path and token shape
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_login_returns_a_bearer_token_that_decodes_to_the_caller():
    """The token is the credential for every other route; its claims must be the
    employee's own, and it must verify under the app's own decoder (issuer, audience
    and signature included)."""
    employee = _employee("wiki:read:own_dept")
    db = _FakeSession(employee=employee)

    result = await auth_router.login(_login(), _request(), db=db)

    assert result.token_type == "bearer"
    claims = decode_access_token(result.access_token)
    assert claims is not None, "the issued token does not survive decode_access_token"
    assert claims["sub"] == str(employee.id)
    assert claims["role"] == "employee"
    assert claims["name"] == employee.name
    assert "exp" in claims


@pytest.mark.asyncio
async def test_login_token_does_not_carry_the_callers_permissions():
    """Permissions must be resolved per-request, not frozen into a 24h token.

    A permission list inside the JWT keeps working for a day after an admin revokes the
    role, and nothing server-side can shorten that window.
    """
    db = _FakeSession(employee=_employee("org:employees:manage"))
    result = await auth_router.login(_login(), _request(), db=db)
    claims = decode_access_token(result.access_token)
    assert "permissions" not in claims


@pytest.mark.asyncio
async def test_login_user_payload_carries_permissions_and_workspace_memberships():
    employee = _employee("wiki:read:own_dept", "org:audit:read")
    workspace_id = uuid.uuid4()
    db = _FakeSession(
        employee=employee,
        memberships=[
            (
                SimpleNamespace(project_id=workspace_id, role="editor"),
                "Platform Team",
            )
        ],
    )

    result = await auth_router.login(_login(), _request(), db=db)

    assert set(result.user) == {
        "id",
        "name",
        "email",
        "role",
        "department_id",
        "department_name",
        "permissions",
        "workspace_memberships",
    }
    assert result.user["id"] == str(employee.id)
    assert result.user["department_name"] == "Engineering"
    assert result.user["permissions"] == ["org:audit:read", "wiki:read:own_dept"]
    assert result.user["workspace_memberships"] == [
        {
            "workspace_id": str(workspace_id),
            "workspace_name": "Platform Team",
            "role": "editor",
        }
    ]


@pytest.mark.asyncio
async def test_login_response_never_contains_the_password_hash():
    """Nothing in the login payload may echo the stored credential material."""
    employee = _employee("wiki:read:own_dept")
    db = _FakeSession(employee=employee)

    result = await auth_router.login(_login(), _request(), db=db)

    assert employee.password_hash not in repr(result.user)
    assert employee.password_hash not in result.access_token


@pytest.mark.asyncio
async def test_login_grants_admins_the_full_permission_set():
    """An admin has no custom_role row; the response still has to list what they can do,
    or the portal hides every admin screen from the admin."""
    from app.services.permissions import ALL_PERMISSIONS

    db = _FakeSession(employee=_employee(role="admin"))
    result = await auth_router.login(_login(), _request(), db=db)
    assert result.user["permissions"] == sorted(ALL_PERMISSIONS)


# --------------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_auth_status_is_a_bare_flag():
    """This route is on the public allowlist, so anything it returns is unauthenticated
    disclosure. It must stay exactly one boolean."""
    assert await auth_router.auth_status() == {"auth_required": True}


# --------------------------------------------------------------------------- #
# me
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_profile_reports_token_presence_without_disclosing_the_digest():
    employee = _employee("wiki:read:own_dept", mcp_token_hash="sha256:deadbeef")
    db = _FakeSession(employee=employee)

    result = await auth_router.get_profile(current_user=employee, db=db)

    assert result.has_mcp_token is True
    assert "deadbeef" not in result.model_dump_json()
    assert result.id == str(employee.id)
    assert result.permissions == ["wiki:read:own_dept"]


@pytest.mark.asyncio
async def test_profile_reports_no_token_when_none_is_stored():
    employee = _employee("wiki:read:own_dept", mcp_token_hash=None)
    result = await auth_router.get_profile(current_user=employee, db=_FakeSession())
    assert result.has_mcp_token is False


# --------------------------------------------------------------------------- #
# change-password
# --------------------------------------------------------------------------- #

def _change(current: str = PASSWORD, new: str = "a-longer-new-secret"):
    return auth_router.ChangePasswordRequest(current_password=current, new_password=new)


@pytest.mark.asyncio
async def test_change_password_requires_the_current_password():
    """Without this check a stolen JWT becomes permanent account takeover."""
    employee = _employee()
    with pytest.raises(HTTPException) as exc:
        await auth_router.change_password(
            _change(current="hunter2"), current_user=employee, db=_FakeSession()
        )
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_change_password_leaves_the_old_hash_intact_when_it_is_rejected():
    employee = _employee()
    original = employee.password_hash
    with pytest.raises(HTTPException):
        await auth_router.change_password(
            _change(current="hunter2"), current_user=employee, db=_FakeSession()
        )
    assert employee.password_hash == original


@pytest.mark.asyncio
async def test_change_password_enforces_a_minimum_length():
    employee = _employee()
    with pytest.raises(HTTPException) as exc:
        await auth_router.change_password(
            _change(new="short"), current_user=employee, db=_FakeSession()
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_change_password_refuses_an_account_with_no_password_set():
    employee = _employee(password=None)
    with pytest.raises(HTTPException) as exc:
        await auth_router.change_password(
            _change(), current_user=employee, db=_FakeSession()
        )
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_change_password_stores_a_hash_that_only_the_new_password_opens():
    employee = _employee()
    old_hash = employee.password_hash
    db = _FakeSession()

    result = await auth_router.change_password(
        _change(new="a-longer-new-secret"), current_user=employee, db=db
    )

    assert result == {"message": "Password changed successfully"}
    assert employee.password_hash != old_hash
    assert verify_password("a-longer-new-secret", employee.password_hash) is True
    assert verify_password(PASSWORD, employee.password_hash) is False
    assert db.flushes == 1, "the new hash was never flushed to the session"


@pytest.mark.asyncio
async def test_change_password_does_not_store_the_password_in_clear_text():
    employee = _employee()
    await auth_router.change_password(
        _change(new="a-longer-new-secret"), current_user=employee, db=_FakeSession()
    )
    assert "a-longer-new-secret" not in employee.password_hash
