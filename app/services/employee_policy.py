"""Rules for who may change another employee's role, password, or active flag.

Kept as plain functions so the denial paths can be unit-tested without a
database or FastAPI request. Routers call these before writing the row.
"""

from typing import Optional

from fastapi import HTTPException

VALID_SYSTEM_ROLES = frozenset({"admin", "employee"})


def is_system_admin(user) -> bool:
    return getattr(user, "role", None) == "admin"


def validate_system_role(role: str) -> str:
    if role not in VALID_SYSTEM_ROLES:
        raise HTTPException(status_code=400, detail="Role must be 'admin' or 'employee'")
    return role


def ensure_password_strength(password: str) -> None:
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")


def ensure_can_assign_role(actor, new_role: str, target=None) -> None:
    """Promote / demote. Only a system admin may write Employee.role."""
    validate_system_role(new_role)
    if not is_system_admin(actor):
        raise HTTPException(
            status_code=403,
            detail="Only a system admin can assign or change the system role",
        )
    if target is not None and str(getattr(actor, "id", "")) == str(getattr(target, "id", "")):
        if new_role != getattr(target, "role", None):
            raise HTTPException(status_code=403, detail="You cannot change your own system role")


def held_permissions(actor) -> set[str]:
    """The actor's own effective permission set."""
    from app.services.permission_engine import _get_user_permissions

    return _get_user_permissions(actor)


def ensure_no_escalation(actor, granting, *, already_held=()) -> None:
    """Refuse to hand out a permission the actor does not hold itself.

    This is the rule that keeps `org:roles:manage` and `org:employees:manage` from being
    paths to full admin. It has to hold at EVERY write that can widen a permission set,
    which is why it lives here rather than inline in one router: it was originally added to
    `update_role` alone, so `create_role` and both `custom_role_id` assignments were left
    open — and the guard could simply be walked around by creating a role instead of
    editing one.
    """
    if is_system_admin(actor):
        return

    escalating = sorted(set(granting) - set(already_held) - held_permissions(actor))
    if escalating:
        raise HTTPException(
            status_code=403,
            detail="You cannot grant permissions you do not hold yourself: "
            + ", ".join(escalating),
        )


def ensure_can_assign_custom_role(actor, role, target=None) -> None:
    """Assign or clear `Employee.custom_role_id`.

    `Employee.role` was guarded by `ensure_can_assign_role`, but the custom role confers the
    same authority through the permission engine and was written straight from the request
    body on both create and update. So `org:employees:manage` alone was enough to PUT your
    own id with a richer `custom_role_id` and hold those permissions on the next request;
    combined with an over-broad role created through `create_role`, that reached every
    permission in ALL_PERMISSIONS.
    """
    if role is None:
        return

    ensure_no_escalation(actor, getattr(role, "permissions", None) or [])

    if target is not None and str(getattr(actor, "id", "")) == str(getattr(target, "id", "")):
        current = getattr(target, "custom_role_id", None)
        if str(current or "") != str(getattr(role, "id", "") or ""):
            raise HTTPException(
                status_code=403,
                detail="You cannot change your own custom role",
            )


def ensure_can_set_password(actor) -> None:
    if not is_system_admin(actor):
        raise HTTPException(
            status_code=403,
            detail="Only a system admin can set or reset a password",
        )


def ensure_not_last_admin(target, new_role: Optional[str], active_admin_count: int) -> None:
    if getattr(target, "role", None) != "admin":
        return
    if new_role is None or new_role == "admin":
        return
    if active_admin_count <= 1:
        raise HTTPException(status_code=409, detail="Cannot demote the last active admin")


def ensure_can_toggle(actor, target, *, active_admin_count: int) -> None:
    if str(getattr(actor, "id", "")) == str(getattr(target, "id", "")):
        raise HTTPException(
            status_code=403,
            detail="You cannot activate or deactivate your own account",
        )
    if getattr(target, "role", None) == "admin" and not is_system_admin(actor):
        raise HTTPException(
            status_code=403,
            detail="Only a system admin can change an admin account's status",
        )
    # Toggling an active admin off is a deactivation.
    if (
        getattr(target, "role", None) == "admin"
        and getattr(target, "is_active", False)
        and active_admin_count <= 1
    ):
        raise HTTPException(status_code=409, detail="Cannot deactivate the last active admin")
