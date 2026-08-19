"""
Department & Employee router — RBAC management for admin portal.
Permission model v2: uses scoped permission format.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Department, Employee, Role
from app.services.audit_service import log_audit
from app.services.auth_service import (
    get_current_user,
    hash_password_async,
    require_permission,
)
from app.services.employee_policy import (
    ensure_can_assign_custom_role,
    ensure_can_assign_role,
    ensure_can_set_password,
    ensure_can_toggle,
    ensure_not_last_admin,
    ensure_password_strength,
)
from app.services.mcp_auth_service import MCPAuthService

router = APIRouter()


async def _active_admin_count(db: AsyncSession) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(Employee)
            .where(Employee.role == "admin", Employee.is_active.is_(True))
        )
    ).scalar_one()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class DepartmentCreate(BaseModel):
    name: str
    description: Optional[str] = None


class DepartmentOut(BaseModel):
    id: str
    name: str
    description: Optional[str]
    employee_count: int = 0

    class Config:
        from_attributes = True


class EmployeeCreate(BaseModel):
    name: str
    email: str
    password: Optional[str] = None
    role: str = "employee"
    # Typed as UUID so a malformed id is a 422 from Pydantic. These were `str` and each
    # call site did a bare uuid.UUID(...) with no ValueError handler registered anywhere,
    # which turned every typo into a 500.
    department_id: uuid.UUID
    custom_role_id: Optional[uuid.UUID] = None


class EmployeeUpdate(BaseModel):
    """Partial update. Omitted fields are left unchanged."""
    name: Optional[str] = None
    email: Optional[str] = None
    password: Optional[str] = None
    role: Optional[str] = None
    department_id: Optional[uuid.UUID] = None
    custom_role_id: Optional[uuid.UUID] = None


class EmployeeOut(BaseModel):
    id: str
    name: str
    email: str
    role: str
    department_id: str
    department_name: str = ""
    is_active: bool
    has_token: bool
    last_connected: Optional[str] = None
    custom_role_id: Optional[str] = None
    custom_role_name: Optional[str] = None

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    token: str
    employee_name: str
    instructions: str


# ---------------------------------------------------------------------------
# Department CRUD
# ---------------------------------------------------------------------------

@router.get("/departments")
async def list_departments(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:departments:read"),
):
    """List all departments with employee counts."""
    stmt = select(Department).options(selectinload(Department.employees))
    result = await db.execute(stmt)
    departments = result.scalars().all()

    return [
        DepartmentOut(
            id=str(d.id),
            name=d.name,
            description=d.description,
            employee_count=len(d.employees),
        )
        for d in departments
    ]


@router.post("/departments", status_code=201)
async def create_department(
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:departments:manage"),
):
    """Create a new department."""
    # Explicit id, not the column default: `default=uuid.uuid4` is applied at INSERT, so
    # reading dept.id before the flush gave log_audit the string "None" — and an audit row
    # that cannot say which department was created is not an audit row. roles.py already
    # does this; departments and employees were missed.
    dept = Department(id=uuid.uuid4(), name=body.name, description=body.description)
    db.add(dept)
    await log_audit(db, _user, "create", "department", str(dept.id), reason=dept.name)
    await db.flush()
    return {"id": str(dept.id), "name": dept.name}


@router.put("/departments/{dept_id}")
async def update_department(
    dept_id: uuid.UUID,
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:departments:manage"),
):
    dept = await db.get(Department, dept_id)
    if not dept:
        raise HTTPException(404, "Department not found")
    dept.name = body.name
    dept.description = body.description
    await log_audit(db, _user, "update", "department", str(dept.id), reason=dept.name)
    await db.flush()
    return {"id": str(dept.id), "name": dept.name}


@router.delete("/departments/{dept_id}")
async def delete_department(
    dept_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:departments:manage"),
):
    dept = await db.get(Department, dept_id)
    if not dept:
        raise HTTPException(404, "Department not found")
    employee_count = (
        await db.execute(
            select(func.count()).select_from(Employee).where(Employee.department_id == dept.id)
        )
    ).scalar_one()
    if employee_count:
        raise HTTPException(
            409,
            f"Reassign or remove {employee_count} employee(s) before deleting this department",
        )
    await log_audit(db, _user, "delete", "department", str(dept.id), reason=dept.name)
    await db.delete(dept)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Employee CRUD
# ---------------------------------------------------------------------------

@router.get("/employees")
async def list_employees(
    department_id: Optional[uuid.UUID] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    # Bare `page_size: int = 20` accepted any integer, so one request could dump the whole
    # employee directory — names, emails, departments and token state — in a single page.
    # Capped at the same 200 as the audit log.
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:read"),
):
    """List employees with pagination, optionally filtered by department or search."""
    from sqlalchemy import func as sa_func

    base = select(Employee).options(
        selectinload(Employee.department), selectinload(Employee.custom_role)
    )
    count_base = select(sa_func.count(Employee.id))

    if department_id:
        base = base.where(Employee.department_id == department_id)
        count_base = count_base.where(Employee.department_id == department_id)
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        like = f"%{escaped}%"
        base = base.where(Employee.name.ilike(like) | Employee.email.ilike(like))
        count_base = count_base.where(Employee.name.ilike(like) | Employee.email.ilike(like))

    # Total count
    total = (await db.execute(count_base)).scalar() or 0

    # Paginated query
    offset = (max(page, 1) - 1) * page_size
    stmt = base.order_by(Employee.name).offset(offset).limit(page_size)
    result = await db.execute(stmt)
    employees = result.scalars().all()

    return {
        "items": [
            EmployeeOut(
                id=str(e.id),
                name=e.name,
                email=e.email,
                role=e.role,
                department_id=str(e.department_id),
                department_name=e.department.name if e.department else "",
                is_active=e.is_active,
                has_token=bool(e.mcp_token_hash),
                last_connected=e.last_connected.isoformat() if e.last_connected else None,
                custom_role_id=str(e.custom_role_id) if e.custom_role_id else None,
                custom_role_name=e.custom_role.name if e.custom_role else None,
            )
            for e in employees
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, -(-total // page_size)),  # ceil division
    }


@router.post("/employees", status_code=201)
async def create_employee(
    body: EmployeeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:manage"),
):
    """Create a new employee."""
    dept = await db.get(Department, body.department_id)
    if not dept:
        raise HTTPException(400, "Department not found")

    if not body.password:
        raise HTTPException(400, "Password is required")
    ensure_password_strength(body.password)
    if body.role == "admin":
        ensure_can_assign_role(_user, "admin")
    elif body.role != "employee":
        raise HTTPException(400, "Role must be 'admin' or 'employee'")

    new_role = await _resolve_custom_role(db, body.custom_role_id)
    ensure_can_assign_custom_role(_user, new_role)

    # Authorized only from here. bcrypt at cost 12 is ~250 ms of uninterruptible CPU; on the
    # event loop it blocks every other request this process is serving, /health included —
    # so a request destined for a 403 must not reach it.
    password_hash = await hash_password_async(body.password)

    emp = Employee(
        # See create_department: without an explicit id, log_audit records "None".
        id=uuid.uuid4(),
        name=body.name,
        email=body.email,
        password_hash=password_hash,
        role=body.role,
        department_id=body.department_id,
        custom_role_id=new_role.id if new_role else None,
    )
    db.add(emp)
    await log_audit(db, _user, "create", "employee", str(emp.id), reason=emp.email)
    await db.flush()

    return {"id": str(emp.id), "name": emp.name, "email": emp.email}


async def _resolve_custom_role(db: AsyncSession, custom_role_id: Optional[uuid.UUID]):
    """Load the Role a request is trying to assign, so its grants can be authorized.

    The id arrived from the request body and was written to the column unchecked, so the
    role's permissions were never looked at — which is what made this an escalation path.
    """
    if not custom_role_id:
        return None
    role = await db.get(Role, custom_role_id)
    if not role:
        raise HTTPException(404, "Role not found")
    return role


@router.put("/employees/{emp_id}")
async def update_employee(
    emp_id: uuid.UUID,
    body: EmployeeUpdate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:manage"),
):
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    if body.name is not None:
        emp.name = body.name
    if body.email is not None:
        emp.email = body.email
    if body.department_id is not None:
        emp.department_id = body.department_id
    if "custom_role_id" in body.model_fields_set:
        new_role = await _resolve_custom_role(db, body.custom_role_id)
        ensure_can_assign_custom_role(_user, new_role, target=emp)
        emp.custom_role_id = new_role.id if new_role else None

    if body.role is not None and body.role != emp.role:
        ensure_can_assign_role(_user, body.role, target=emp)
        ensure_not_last_admin(emp, body.role, await _active_admin_count(db))
        emp.role = body.role

    if body.password:
        ensure_can_set_password(_user)
        ensure_password_strength(body.password)
        emp.password_hash = await hash_password_async(body.password)

    await log_audit(db, _user, "update", "employee", str(emp.id), reason=emp.email)
    await db.flush()
    return {"id": str(emp.id), "name": emp.name}


@router.delete("/employees/{emp_id}")
async def delete_employee(
    emp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:manage"),
):
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    if emp.role == "admin":
        raise HTTPException(400, "Cannot delete an admin account")
    await log_audit(db, _user, "delete", "employee", str(emp.id), reason=emp.email)
    await db.delete(emp)
    return {"deleted": True}


@router.patch("/employees/{emp_id}/toggle")
async def toggle_employee(
    emp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:manage"),
):
    """Activate or deactivate an employee."""
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")
    ensure_can_toggle(_user, emp, active_admin_count=await _active_admin_count(db))
    emp.is_active = not emp.is_active
    await log_audit(db, _user, "update", "employee", str(emp.id), reason=f"toggle active={emp.is_active}")
    await db.flush()
    return {"id": str(emp.id), "is_active": emp.is_active}


# ---------------------------------------------------------------------------
# MCP Token Management
# ---------------------------------------------------------------------------

@router.post("/employees/{emp_id}/token", response_model=TokenResponse)
async def generate_mcp_token(
    emp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:manage"),
):
    """Generate (or regenerate) an MCP token for an employee."""
    emp = await db.get(Employee, emp_id)
    if not emp:
        raise HTTPException(404, "Employee not found")

    auth_svc = MCPAuthService(db)
    token = await auth_svc.generate_token(emp.id)

    return TokenResponse(
        token=token,
        employee_name=emp.name,
        instructions=(
            f"Add this to Claude Desktop config:\n"
            f'{{"mcpServers": {{"arkon": {{"url": "https://your-server/mcp", '
            f'"headers": {{"Authorization": "Bearer {token}"}}}}}}}}'
        ),
    )


@router.delete("/employees/{emp_id}/token")
async def revoke_mcp_token(
    emp_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("org:employees:manage"),
):
    """Revoke an employee's MCP token."""
    auth_svc = MCPAuthService(db)
    revoked = await auth_svc.revoke_token(emp_id)
    if not revoked:
        raise HTTPException(404, "Employee not found or has no token")
    return {"revoked": True}


# ---------------------------------------------------------------------------
# Self-Service: Employee gets their own MCP token
# ---------------------------------------------------------------------------

@router.post("/my/mcp-token", response_model=TokenResponse)
async def get_my_mcp_token(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """
    Generate (or show) the current employee's own MCP token.
    This is the self-service endpoint employees use from their portal.
    """
    auth_svc = MCPAuthService(db)

    # Always rotates. This endpoint used to return the existing token unchanged when one
    # already existed, which made the portal's "Regenerate Token" button a no-op: a user
    # who believed they had rotated a leaked credential had not. Only the digest is stored,
    # so there is nothing to return on a second call even if we wanted to.
    token = await auth_svc.generate_token(current_user.id)

    return TokenResponse(
        token=token,
        employee_name=current_user.name,
        instructions=(
            f"Add this to Claude Desktop config:\n"
            f'{{"mcpServers": {{"arkon": {{"url": "https://your-server/mcp", '
            f'"headers": {{"Authorization": "Bearer {token}"}}}}}}}}'
        ),
    )


@router.delete("/my/mcp-token")
async def revoke_my_mcp_token(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
):
    """Revoke the current employee's own MCP token."""
    auth_svc = MCPAuthService(db)
    await auth_svc.revoke_token(current_user.id)
    return {"revoked": True}


@router.get("/my/mcp-token/status")
async def get_my_mcp_token_status(
    current_user: Employee = Depends(get_current_user),
):
    """Check if the current employee has an active MCP token (without revealing it)."""
    return {"has_token": bool(current_user.mcp_token_hash)}
