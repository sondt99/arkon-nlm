"""
Permission Engine — resolves access decisions for Global and Workspace realms.

Global Realm:
  - Permissions are scoped: resource:action:own_dept or resource:action:all
  - own_dept = user's department_id matches source's departments (via source_departments)
  - all = no scope restriction
  - No departments on resource = Global (visible to everyone with the action permission)

Workspace Realm:
  - Pure membership check. Global role does NOT grant access.
  - Admin (role='admin') can view all workspaces.
  - Workspace role (viewer/contributor/editor/admin) determines actions within workspace.
"""

import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    WORKSPACE_ROLE_HIERARCHY,
    Employee,
    ProjectMember,
    Skill,
    Source,
    SourceDepartment,
    WorkspaceRole,
)

# ---------------------------------------------------------------------------
# Permission string parsing
# ---------------------------------------------------------------------------

def parse_permission(perm: str) -> tuple[str, str, str]:
    """Parse 'resource:action:scope' → (resource, action, scope).
    For org permissions like 'org:departments:read' → ('org', 'departments', 'read').
    """
    parts = perm.split(":")
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return perm, "", ""


def has_permission(permissions: list[str], resource: str, action: str, scope: str = "any") -> bool:
    """Check if a permission list contains the required permission.
    
    scope = "any" → matches either own_dept or all
    scope = "all" → only matches :all
    scope = "own_dept" → matches :own_dept or :all
    """
    perm_all = f"{resource}:{action}:all"
    perm_own = f"{resource}:{action}:own_dept"

    if scope == "all":
        return perm_all in permissions
    elif scope == "own_dept":
        return perm_all in permissions or perm_own in permissions
    else:  # "any"
        return perm_all in permissions or perm_own in permissions


def has_any_permission(permissions: list[str], resource: str, action: str) -> bool:
    """Check if user has any variant (own_dept or all) of a resource:action."""
    return has_permission(permissions, resource, action, "any")


def get_scope_level(permissions: list[str], resource: str, action: str) -> Optional[str]:
    """Get the effective scope level for a resource:action.
    Returns 'all', 'own_dept', or None.
    """
    perm_all = f"{resource}:{action}:all"
    perm_own = f"{resource}:{action}:own_dept"
    if perm_all in permissions:
        return "all"
    if perm_own in permissions:
        return "own_dept"
    return None


# ---------------------------------------------------------------------------
# Global Realm: Document access
# ---------------------------------------------------------------------------

async def can_access_document(
    db: AsyncSession,
    user: Employee,
    source: Source,
    action: str = "read",
) -> bool:
    """Check if user can perform action on a source document.
    
    Logic:
    1. Admin → always True
    2. User has doc:{action}:all → True
    3. User has doc:{action}:own_dept →
       a. Source has no departments (Global doc) → True
       b. Source has a department matching user.department_id → True
       c. Otherwise → False
    4. Otherwise → False
    """
    if user.role == "admin":
        return True

    # Workspace-private sources: membership only. A global doc:* grant
    # does not open another team's project files.
    scope_type = getattr(source, "scope_type", None)
    scope_id = getattr(source, "scope_id", None)
    if scope_type == "project" and scope_id:
        if not await can_access_workspace(db, user, scope_id):
            return False
        if action == "read":
            return True
        member_role = await get_workspace_role(db, user, scope_id)
        return bool(member_role and workspace_role_can(member_role, "editor"))

    permissions = _get_user_permissions(user)

    # Has :all scope
    if f"doc:{action}:all" in permissions:
        return True

    # Has :own_dept scope
    if f"doc:{action}:own_dept" not in permissions:
        return False

    # Check if source is global (no departments) or belongs to user's department
    dept_result = await db.execute(
        select(SourceDepartment.department_id)
        .where(SourceDepartment.source_id == source.id)
    )
    source_dept_ids = {row[0] for row in dept_result.all()}

    if not source_dept_ids:
        # No departments = Global doc: readable by anyone holding doc:read:own_dept.
        #
        # Not writable by them, though. This returned True for EVERY action, so
        # `doc:delete:own_dept` passed on the company-wide HR handbook and
        # `delete_source_completely` wiped its MinIO objects and derived wiki pages. The
        # create path already says so — sources.py:137-141 requires doc:*:all to reach
        # department-less scope — and the write path contradicted it.
        return action == "read"

    return user.department_id in source_dept_ids


def build_document_filter(user: Employee, action: str = "read"):
    """Build SQLAlchemy filter clauses for listing documents based on user permissions.

    Returns: (needs_filter: bool, filter_clauses: list)
    If needs_filter is False, show all documents.

    NOTE — this has no production callers; `list_sources` implements its own inline filter.
    It is kept only because `tests/test_permission_engine_pairing.py` uses it to hold
    `can_access_document` honest, and it carries two sharp edges for anyone who wires it up:

      1. `(True, None)` means "deny everything", NOT "no filter". A caller that reads only
         the first element and then filters by nothing inverts a deny into an allow.
      2. The returned department list does not encode the global-object rule. A source with
         no departments is readable under `doc:read:own_dept` but NOT writable — see
         `can_access_document`. A caller must apply that rule itself; this tuple cannot
         express it, which is precisely why the pairing test passes `action` through.
    """
    if user.role == "admin":
        return False, []

    permissions = _get_user_permissions(user)

    if f"doc:{action}:all" in permissions:
        return False, []

    if f"doc:{action}:own_dept" in permissions:
        # Filter: source has no departments (global) OR has user's department
        return True, [user.department_id]

    # No permission at all — empty result
    return True, None


# ---------------------------------------------------------------------------
# Global Realm: AI Skill access
# ---------------------------------------------------------------------------

async def can_access_skill(
    db: AsyncSession,
    user: Employee,
    skill: Skill,
    action: str = "read",
) -> bool:
    """Check if user can perform action on an AI skill.
    
    Logic:
    1. Admin → True
    2. User has skill:{action}:all → True
    3. User has skill:{action}:own_dept →
       a. Skill has no department (Global) → True
       b. Skill's department matches user.department_id → True
       c. Otherwise → False
    4. Otherwise → False
    """
    if user.role == "admin":
        return True

    permissions = _get_user_permissions(user)

    if f"skill:{action}:all" in permissions:
        return True

    if f"skill:{action}:own_dept" not in permissions:
        return False

    # Skill visible if it's Global (no depts) OR user's dept is in skill's depts.
    #
    # "No departments" answers READ for everyone — that is what makes a skill global. It
    # must not answer DELETE the same way: this branch ignored `action` entirely, so
    # `skill:delete:own_dept` passed on any org-wide skill and a department contributor
    # could delete one (`DELETE /api/skills/{slug}`), with `_assert_not_system` the only
    # remaining barrier. Mutating something that belongs to no department is exactly the
    # case `:all` exists for, and an `:all` holder has already returned True above.
    skill_dept_ids = {sd.department_id for sd in skill.departments}
    if not skill_dept_ids:
        return action == "read"

    return user.department_id in skill_dept_ids


def build_skill_filter(user: Employee, action: str = "read"):
    """Build SQLAlchemy filter clauses for listing skills.
    Returns: (needs_filter: bool, filter_clauses: list)
    """
    if user.role == "admin":
        return False, []

    permissions = _get_user_permissions(user)

    if f"skill:{action}:all" in permissions:
        return False, []

    if f"skill:{action}:own_dept" in permissions:
        # Filter: skill has no department (global) OR matches user's department
        # This will be handled in SkillService.list_skills via allowed_department_ids
        return True, [user.department_id]

    return True, None


# ---------------------------------------------------------------------------
# Workspace Realm: Membership check
# ---------------------------------------------------------------------------

async def can_access_workspace(
    db: AsyncSession,
    user: Employee,
    workspace_id: uuid.UUID,
) -> bool:
    """Check if user can access a workspace.
    Admin (role='admin') can always access all workspaces.
    Otherwise, user must be a member.
    """
    if user.role == "admin":
        return True

    result = await db.execute(
        select(ProjectMember.role)
        .where(
            ProjectMember.project_id == workspace_id,
            ProjectMember.employee_id == user.id,
        )
    )
    return result.scalar_one_or_none() is not None


async def get_workspace_role(
    db: AsyncSession,
    user: Employee,
    workspace_id: uuid.UUID,
) -> Optional[str]:
    """Get user's role in a workspace.
    Admin gets 'admin' role in all workspaces.
    Returns None if user is not a member.
    """
    if user.role == "admin":
        return WorkspaceRole.ADMIN.value

    result = await db.execute(
        select(ProjectMember.role)
        .where(
            ProjectMember.project_id == workspace_id,
            ProjectMember.employee_id == user.id,
        )
    )
    return result.scalar_one_or_none()


def workspace_role_can(member_role: str, required_role: str) -> bool:
    """Check if a workspace role meets the minimum required level."""
    try:
        member_level = WORKSPACE_ROLE_HIERARCHY[WorkspaceRole(member_role)]
        required_level = WORKSPACE_ROLE_HIERARCHY[WorkspaceRole(required_role)]
        return member_level >= required_level
    except (ValueError, KeyError):
        return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_user_permissions(user: Employee) -> set[str]:
    """Extract effective permissions from user's custom role."""
    if user.role == "admin":
        from app.services.permissions import ALL_PERMISSIONS
        return set(ALL_PERMISSIONS)

    if not user.custom_role:
        # Deny, not a default grant.
        #
        # This returned EMPLOYEE_DEFAULT_PERMISSIONS, which made "no role" mean "the
        # standard role" — a fallback GRANT in the one function whose job is to decide
        # what someone may do. On REST the mistake was invisible because
        # `get_current_user` re-attaches the system Employee role before this runs. The
        # MCP and export paths have no such re-attach, so an admin stripping a departing
        # contractor's role to cut their access left their existing token holding
        # doc:read:own_dept, wiki:read:own_dept and wiki:WRITE:own_dept.
        #
        # An account with no role now resolves to no permissions, and the REST default
        # keeps coming from the role auth_service attaches — explicitly, where it can be
        # seen and audited.
        return set()

    stored = user.custom_role.permissions or []

    # Auto-migrate legacy permission names
    from app.services.permissions import LEGACY_PERMISSION_MAP
    effective: set[str] = set()
    for p in stored:
        if p in LEGACY_PERMISSION_MAP:
            effective.update(LEGACY_PERMISSION_MAP[p])
        else:
            effective.add(p)

    return effective


def get_effective_permissions(user: Employee) -> list[str]:
    """Public version — returns sorted list for API responses."""
    from app.services.permissions import ALL_PERMISSIONS
    perms = _get_user_permissions(user)
    return sorted(p for p in perms if p in ALL_PERMISSIONS)
