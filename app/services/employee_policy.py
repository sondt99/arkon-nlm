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
