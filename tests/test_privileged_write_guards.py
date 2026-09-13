"""Guards that exist but are not called are the recurring defect in this codebase.

#88 was closed on two helpers that were written correctly and never invoked. The MCP
scope filter had the same shape. These pin two more of the same class, both found by
mutation: deleting each left the full suite green.

  * `_get_user_permissions` fell back to EMPLOYEE_DEFAULT_PERMISSIONS when an employee had
    no role — a fallback GRANT in the function that decides what someone may do.
  * `create_employee` never called `ensure_can_set_password`, though `update_employee`
    does, so `org:employees:manage` could mint an account in any department with a chosen
    password and log in as it.
"""

import ast
import inspect
import pathlib
from types import SimpleNamespace

import app.routers.rbac as rbac_router
from app.services.permission_engine import _get_user_permissions

_RBAC_TREE = ast.parse(
    pathlib.Path(inspect.getfile(rbac_router)).read_text(encoding="utf-8")
)


def _calls_within(func_name: str) -> set[str]:
    """Names invoked inside a named top-level function."""
    for fn in ast.walk(_RBAC_TREE):
        if isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef)) and fn.name == func_name:
            return {
                n.func.id
                for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
    raise AssertionError(f"{func_name} not found in app/routers/rbac.py")


# ---------------------------------------------------------------------------
# No role means no permissions
# ---------------------------------------------------------------------------

def test_an_employee_with_no_role_gets_no_permissions():
    """Was EMPLOYEE_DEFAULT_PERMISSIONS — a grant where a deny belongs.

    REST hid it: `get_current_user` re-attaches the system Employee role before this
    runs. The MCP and export paths do not, so stripping a departing contractor's role
    left their existing token holding doc:read:own_dept and wiki:WRITE:own_dept.
    """
    roleless = SimpleNamespace(role="employee", custom_role=None)
    assert _get_user_permissions(roleless) == set(), (
        "an account with no role resolved to the default permission set — removing "
        "someone's role must remove their access, not restore the standard one"
    )


def test_a_roleless_employee_is_denied_the_default_grants_specifically():
    roleless = SimpleNamespace(role="employee", custom_role=None)
    perms = _get_user_permissions(roleless)
    for p in ("doc:read:own_dept", "wiki:read:own_dept", "wiki:write:own_dept"):
        assert p not in perms


def test_admin_is_unaffected():
    """The deny must not have been applied one branch too early."""
    admin = SimpleNamespace(role="admin", custom_role=None)
    assert "doc:read:all" in _get_user_permissions(admin)


# ---------------------------------------------------------------------------
# Creating an account is at least as privileged as updating one
# ---------------------------------------------------------------------------

def test_create_employee_requires_the_password_guard():
    assert "ensure_can_set_password" in _calls_within("create_employee"), (
        "create_employee sets a caller-supplied password without ensure_can_set_password "
        "— org:employees:manage can then mint an account in any department and log in "
        "as it, walking around the self-reassignment block in "
        "ensure_can_assign_department"
    )


def test_update_employee_still_requires_it():
    """The rule this file asserts create must match. If update loses it, both are wrong."""
    assert "ensure_can_set_password" in _calls_within("update_employee")


def test_delete_department_refuses_while_content_is_still_classified():
    """Deleting a department used to publish its documents.

    `Department.source_departments` is cascade="all, delete-orphan", and
    `can_access_document` reads a source with no departments as global — so deleting an
    emptied department silently made its files readable organization-wide, with no audit
    entry because no access-control row was explicitly changed.
    """
    calls = _calls_within("delete_department")
    assert "select" in calls, "delete_department no longer counts anything before deleting"
    src = inspect.getsource(rbac_router.delete_department)
    assert "SourceDepartment" in src and "SkillDepartment" in src, (
        "delete_department must refuse while documents or skills are still classified "
        "under the department"
    )
