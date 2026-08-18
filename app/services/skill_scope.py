"""Validate skill-contribution scope and who may approve it.

Approval is derived from the *target skill's* current departments, never
from the contributor's self-declared scope_type. Only a system admin may
widen a skill from department-scoped to global.
"""

from typing import Iterable, Optional
from uuid import UUID

from fastapi import HTTPException

VALID_SKILL_SCOPES = frozenset({"global", "department"})


def _as_uuid(value) -> UUID:
    return value if isinstance(value, UUID) else UUID(str(value))


def skill_department_ids(skill) -> list[UUID]:
    if skill is None:
        return []
    return [_as_uuid(sd.department_id) for sd in (getattr(skill, "departments", None) or [])]


def normalize_contribution_scope(
    scope_type: str,
    scope_ids: Optional[Iterable],
    *,
    skill=None,
) -> tuple[str, Optional[list[UUID]]]:
    """Return a validated (scope_type, scope_ids).

    When a target skill is provided, the skill's live departments win.
    The contributor cannot claim 'global' to skip department review.
    """
    if skill is not None:
        depts = skill_department_ids(skill)
        if depts:
            return "department", depts
        return "global", None

    if scope_type not in VALID_SKILL_SCOPES:
        raise HTTPException(
            status_code=400,
            detail="scope_type must be 'global' or 'department'",
        )
    if scope_type == "department":
        ids = [_as_uuid(i) for i in (scope_ids or [])]
        if not ids:
            raise HTTPException(
                status_code=400,
                detail="scope_ids is required when scope_type is department",
            )
        return "department", ids
    return "global", None


def ensure_reviewer_can_approve(reviewer, skill, contribution) -> None:
    if getattr(reviewer, "role", None) == "admin":
        return

    depts = skill_department_ids(skill)
    if not depts and skill is None:
        raw = getattr(contribution, "scope_ids", None) or []
        depts = [_as_uuid(i) for i in raw] if getattr(contribution, "scope_type", None) == "department" else []

    if not depts:
        # Global skill (or a new global skill): any holder of
        # skill:contribution:review may approve.
        return

    reviewer_dept = getattr(reviewer, "department_id", None)
    if reviewer_dept is None or _as_uuid(reviewer_dept) not in depts:
        raise HTTPException(
            status_code=403,
            detail="You can only approve contributions for skills in your department",
        )


def ensure_scope_change_allowed(reviewer, final_scope_type: Optional[str]) -> None:
    """Non-admins may not pass final_scope_type at all (cannot widen or move)."""
    if final_scope_type is None:
        return
    if final_scope_type not in VALID_SKILL_SCOPES:
        raise HTTPException(
            status_code=400,
            detail="final_scope_type must be 'global' or 'department'",
        )
    if getattr(reviewer, "role", None) != "admin":
        raise HTTPException(
            status_code=403,
            detail="Only a system admin can change a skill's scope on approval",
        )
