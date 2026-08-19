"""
MCP Auth Service — token verification and scope resolution.

This service is called by the MCP server to:
1. Verify employee MCP token
2. Resolve the employee's department
3. Compute the effective knowledge scope (what docs they can access)
4. Generate and revoke tokens

Permission model v2: uses source_departments M2M and scoped permissions.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from loguru import logger
from sqlalchemy import exists, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.database.models import (
    Employee,
    ProjectMember,
    ProjectSource,
    Source,
    SourceDepartment,
)


@dataclass
class ResolvedIdentity:
    """The authenticated employee context, passed to MCP tools."""
    employee_id: uuid.UUID
    employee_name: str
    department_id: uuid.UUID
    department_name: str
    allowed_knowledge_types: Optional[list[str]] = None  # None = all
    allowed_source_ids: Optional[list[str]] = None       # None = all
    project_source_ids: list[str] = field(default_factory=list)  # always granted via projects
    is_admin: bool = False
    wiki_readable: bool = True
    permissions: list[str] = field(default_factory=list)

    def wiki_visibility(self) -> tuple[Optional[list[str]], Optional[list[str]]]:
        """(allowed_kt_slugs, allowed_source_ids) for wiki queries.

        (None, None) = unrestricted. ([], []) = no wiki access.
        """
        if self.is_admin or (self.wiki_readable and self.allowed_knowledge_types is None):
            return None, None
        if not self.wiki_readable:
            return [], []
        if self.allowed_source_ids is None:
            return self.allowed_knowledge_types, None
        combined = list(dict.fromkeys(list(self.allowed_source_ids) + list(self.project_source_ids)))
        return self.allowed_knowledge_types, combined



# Throttle window for the last_connected write on the MCP read path.
_LAST_CONNECTED_THROTTLE = timedelta(minutes=5)


def hash_mcp_token(token: str) -> str:
    """SHA-256 hex digest of a bearer token.

    A plain digest is correct here rather than a slow KDF: the token is 256 bits of
    `secrets.token_urlsafe(32)` output, so there is no low-entropy secret to brute-force,
    and the lookup must stay indexable. The property we need is that a database read does
    not yield a usable credential.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class MCPAuthService:
    """Handles MCP token auth and knowledge scope resolution."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def verify_token(self, token: str) -> Optional[ResolvedIdentity]:
        """
        Verify an MCP bearer token and return the resolved identity.
        Returns None if token is invalid/inactive.
        """
        # Look up by digest — the plaintext token is never stored, so a database read
        # cannot yield usable credentials.
        stmt = (
            select(Employee)
            .where(
                Employee.mcp_token_hash == hash_mcp_token(token),
                Employee.is_active.is_(True),
            )
            .options(
                selectinload(Employee.department),
                selectinload(Employee.custom_role),
            )
        )
        result = await self.db.execute(stmt)
        employee = result.scalar_one_or_none()

        if not employee:
            return None

        now = datetime.now(timezone.utc)

        # Hard expiry. Previously a leaked token was valid forever unless an admin
        # happened to revoke it, so one exfiltrated from a developer's Claude Desktop
        # config years earlier still worked.
        if employee.mcp_token_expires_at is not None and employee.mcp_token_expires_at <= now:
            logger.info(f"Rejected expired MCP token for employee {employee.id}")
            return None

        # Throttle the last_connected write. This used to fire on EVERY tool call, turning
        # each read-only MCP request into an UPDATE + COMMIT on the employee row, so an
        # agent making 50 calls serialised 50 writes against the same row's lock.
        if (
            employee.last_connected is None
            or (now - employee.last_connected) > _LAST_CONNECTED_THROTTLE
        ):
            employee.last_connected = now
            await self.db.flush()

        # Resolve knowledge scope
        identity = await self._resolve_scope(employee)
        return identity

    async def _resolve_scope(self, employee: Employee) -> ResolvedIdentity:
        """
        Compute effective knowledge scope for an employee.
        Uses new permission model v2 (scoped permissions + source_departments).
        """
        from app.services.permission_engine import (
            get_effective_permissions,
            get_scope_level,
        )

        permissions = get_effective_permissions(employee)
        project_source_ids = await self._resolve_project_sources(employee.id)

        wiki_level = get_scope_level(permissions, "wiki", "read")
        wiki_readable = employee.role == "admin" or wiki_level is not None

        # Admin gets unrestricted access
        if employee.role == "admin":
            return ResolvedIdentity(
                employee_id=employee.id,
                employee_name=employee.name,
                department_id=employee.department_id,
                department_name=employee.department.name if employee.department else "",
                allowed_knowledge_types=None,
                allowed_source_ids=None,
                project_source_ids=project_source_ids,
                is_admin=True,
                wiki_readable=True,
                permissions=permissions,
            )

        # Determine doc:read scope
        scope = get_scope_level(permissions, "doc", "read")

        if scope == "all":
            allowed_kts = None if wiki_level == "all" else (
                [] if wiki_level is None
                else await self._knowledge_types_for_sources(None)
            )
            return ResolvedIdentity(
                employee_id=employee.id,
                employee_name=employee.name,
                department_id=employee.department_id,
                department_name=employee.department.name if employee.department else "",
                allowed_knowledge_types=allowed_kts,
                allowed_source_ids=None,
                project_source_ids=project_source_ids,
                wiki_readable=wiki_readable,
                permissions=permissions,
            )

        if scope == "own_dept":
            allowed_ids = await self._get_department_source_ids(employee.department_id)
            visible_ids = list(dict.fromkeys(allowed_ids + project_source_ids))
            if wiki_level is None:
                allowed_kts: Optional[list[str]] = []
            elif wiki_level == "all":
                allowed_kts = None
            else:
                allowed_kts = await self._knowledge_types_for_sources(visible_ids)
            return ResolvedIdentity(
                employee_id=employee.id,
                employee_name=employee.name,
                department_id=employee.department_id,
                department_name=employee.department.name if employee.department else "",
                allowed_knowledge_types=allowed_kts,
                allowed_source_ids=allowed_ids,
                project_source_ids=project_source_ids,
                wiki_readable=wiki_readable,
                permissions=permissions,
            )

        # No doc:read permission at all
        return ResolvedIdentity(
            employee_id=employee.id,
            employee_name=employee.name,
            department_id=employee.department_id,
            department_name=employee.department.name if employee.department else "",
            allowed_knowledge_types=[] if wiki_level != "all" else None,
            allowed_source_ids=[],
            project_source_ids=project_source_ids,
            wiki_readable=wiki_readable,
            permissions=permissions,
        )

    async def _knowledge_types_for_sources(
        self, source_ids: Optional[list[str]]
    ) -> list[str]:
        """Distinct KnowledgeType slugs of the given sources (all global sources if None)."""
        from app.database.models import KnowledgeType

        stmt = (
            select(KnowledgeType.slug)
            .join(Source, Source.knowledge_type_id == KnowledgeType.id)
            .where(Source.scope_type != "project")
            .distinct()
        )
        if source_ids is not None:
            if not source_ids:
                return []
            stmt = stmt.where(Source.id.in_([uuid.UUID(s) for s in source_ids]))
        result = await self.db.execute(stmt)
        return [row[0] for row in result.all() if row[0]]

    async def _get_department_source_ids(self, department_id: uuid.UUID) -> list[str]:
        """Get IDs of sources that are global (no departments) or in the given department."""
        # Sources with no department entries (global)
        # "Global" means no department rows AND not a workspace-private source.
        # Project-scoped files are added separately via membership.
        global_stmt = (
            select(Source.id)
            .where(
                Source.scope_type != "project",
                ~exists(
                    select(SourceDepartment.source_id)
                    .where(SourceDepartment.source_id == Source.id)
                )
            )
        )
        global_result = await self.db.execute(global_stmt)
        global_ids = [str(r[0]) for r in global_result.all()]

        # Sources in this department
        dept_stmt = (
            select(SourceDepartment.source_id)
            .where(SourceDepartment.department_id == department_id)
        )
        dept_result = await self.db.execute(dept_stmt)
        dept_ids = [str(r[0]) for r in dept_result.all()]

        return global_ids + dept_ids

    async def _resolve_project_sources(self, employee_id: uuid.UUID) -> list[str]:
        """Collect source IDs from all active projects the employee is a member of."""
        member_stmt = select(ProjectMember.project_id).where(
            ProjectMember.employee_id == employee_id
        )
        member_result = await self.db.execute(member_stmt)
        project_ids = [r[0] for r in member_result.all()]

        if not project_ids:
            return []

        from app.database.models import Project
        source_stmt = (
            select(ProjectSource.source_id)
            .join(Project, Project.id == ProjectSource.project_id)
            .where(
                ProjectSource.project_id.in_(project_ids),
                Project.status == "active",
            )
        )
        source_result = await self.db.execute(source_stmt)
        return [str(r[0]) for r in source_result.all()]

    # --- Token Management ---

    async def generate_token(self, employee_id: uuid.UUID) -> str:
        """Mint a new MCP token, store only its digest, and return the plaintext ONCE.

        Generating always rotates: any previous token's digest is overwritten, so the old
        credential stops working immediately. That is what makes the portal's "Regenerate
        Token" button honest — it previously called an idempotent endpoint that returned
        the existing token unchanged, so a user who believed they had rotated a leaked
        credential had not.
        """
        token = f"ark_{secrets.token_urlsafe(32)}"
        expires_at = datetime.now(timezone.utc) + timedelta(
            days=settings.mcp_token_expiry_days
        )

        stmt = (
            update(Employee)
            .where(Employee.id == employee_id)
            .values(
                mcp_token_hash=hash_mcp_token(token),
                mcp_token_expires_at=expires_at,
            )
        )
        await self.db.execute(stmt)
        await self.db.flush()

        logger.info(
            f"Generated MCP token for employee {employee_id} (expires {expires_at.isoformat()})"
        )
        return token

    async def revoke_token(self, employee_id: uuid.UUID) -> bool:
        """Revoke an employee's MCP token."""
        stmt = (
            update(Employee)
            .where(Employee.id == employee_id)
            .values(mcp_token_hash=None, mcp_token_expires_at=None)
        )
        result = await self.db.execute(stmt)
        await self.db.flush()
        return (result.rowcount or 0) > 0  # type: ignore[union-attr]


def apply_scope_filter(query, identity: ResolvedIdentity):
    """
    Apply knowledge scope filters to a SQLAlchemy query on the Source table.

    Sources are accessible when any of these conditions is true:
      1. No scope restrictions defined (open access)
      2. Source ID is in allowed_source_ids (explicit grant)
      3. Source knowledge_type is in allowed_knowledge_types (type-based grant)
      4. Source is in one of the employee's active projects (project grant)

    Usage:
        stmt = select(Source).where(Source.status == "ready")
        stmt = apply_scope_filter(stmt, identity)
    """
    project_uuids = [uuid.UUID(s) for s in identity.project_source_ids]

    if identity.allowed_source_ids is None and identity.allowed_knowledge_types is None:
        # Open access
        return query

    conditions = []

    if identity.allowed_source_ids is not None:
        conditions.append(Source.id.in_([uuid.UUID(s) for s in identity.allowed_source_ids]))

    if identity.allowed_knowledge_types is not None:
        from sqlalchemy import select as sa_select

        from app.database.models import KnowledgeType
        kt_subq = sa_select(KnowledgeType.id).where(
            KnowledgeType.slug.in_(identity.allowed_knowledge_types)
        )
        conditions.append(Source.knowledge_type_id.in_(kt_subq))

    if project_uuids:
        conditions.append(Source.id.in_(project_uuids))

    if conditions:
        query = query.where(or_(*conditions))

    return query


# ---------------------------------------------------------------------------
# FastAPI dependency — REST callers using the same token as MCP (export API)
# ---------------------------------------------------------------------------

_export_bearer = HTTPBearer(auto_error=False)


async def get_identity_from_export_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_export_bearer),
    db: AsyncSession = Depends(get_db),
) -> ResolvedIdentity:
    """
    FastAPI dependency — resolves an Employee.mcp_token bearer token to a
    ResolvedIdentity, for REST routers (e.g. the export API) outside the
    FastMCP tool-call surface. Reuses the same token as MCP/Claude Desktop.
    """
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")

    identity = await MCPAuthService(db).verify_token(credentials.credentials)
    if identity is None:
        raise HTTPException(status_code=401, detail="Invalid or inactive export token")
    return identity
