"""
Wiki Draft router — propose, review, approve, and reject wiki page drafts.

Permission model:
  - Propose (POST /drafts): workspace contributor+ OR global wiki:write
  - Review/Approve/Reject: workspace editor+ OR wiki:write:all OR admin
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import (
    WORKSPACE_ROLE_HIERARCHY,
    Employee,
    ProjectMember,
    WikiPage,
    WikiPageDraft,
    WorkspaceRole,
)
from app.services import wiki_service
from app.services.audit_service import log_audit
from app.services.auth_service import get_current_user, require_permission
from app.services.permission_engine import (
    _get_user_permissions,
    get_workspace_role,
    has_any_permission,
    workspace_role_can,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ProposeDraftRequest(BaseModel):
    content_md: str
    note: Optional[str] = None

    @field_validator("content_md")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content_md must not be empty")
        if len(v) > 50_000:
            raise ValueError("content_md exceeds 50,000 character limit")
        return v


class ApproveDraftRequest(BaseModel):
    reviewer_note: Optional[str] = None
    edited_content_md: Optional[str] = None


class RejectDraftRequest(BaseModel):
    reviewer_note: str

    @field_validator("reviewer_note")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("reviewer_note is required when rejecting")
        return v


class DraftResponse(BaseModel):
    id: uuid.UUID
    page_id: uuid.UUID
    page_slug: str
    page_title: str
    author_id: Optional[uuid.UUID]
    author_name: Optional[str]
    content_md: str
    note: Optional[str]
    status: str
    source: str
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[str] = None
    reviewer_note: Optional[str] = None
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _can_propose(db: AsyncSession, user: Employee, page: WikiPage) -> bool:
    """Contributor+ in workspace, or any wiki:write for global pages."""
    if user.role == "admin":
        return True
    if page.scope_type == "project" and page.scope_id:
        role = await get_workspace_role(db, user, page.scope_id)
        return bool(role) and workspace_role_can(role, "contributor")
    perms = _get_user_permissions(user)
    return has_any_permission(list(perms), "wiki", "write")


async def _can_review(db: AsyncSession, user: Employee, page: WikiPage) -> bool:
    """Editor+ in workspace, or wiki:write:all, or admin."""
    if user.role == "admin":
        return True
    if page.scope_type == "project" and page.scope_id:
        role = await get_workspace_role(db, user, page.scope_id)
        return bool(role) and workspace_role_can(role, "editor")
    perms = _get_user_permissions(user)
    return "wiki:write:all" in perms


def _build_reviewable_filter(user: Employee):
    """`_can_review` as a WHERE clause over the joined WikiPage, or None for "everything".

    Must stay in step with `_can_review` above — same two branches, same thresholds:
    project-scoped pages need editor+ membership, global pages need wiki:write:all.
    """
    if user.role == "admin":
        return None

    editor_or_above = [
        role.value
        for role, level in WORKSPACE_ROLE_HIERARCHY.items()
        if level >= WORKSPACE_ROLE_HIERARCHY[WorkspaceRole.EDITOR]
    ]
    editor_of = select(ProjectMember.project_id).where(
        ProjectMember.employee_id == user.id,
        ProjectMember.role.in_(editor_or_above),
    )
    project_scoped = and_(
        WikiPage.scope_type == "project",
        WikiPage.scope_id.isnot(None),
        WikiPage.scope_id.in_(editor_of),
    )

    if "wiki:write:all" in _get_user_permissions(user):
        return or_(
            project_scoped,
            or_(WikiPage.scope_type != "project", WikiPage.scope_id.is_(None)),
        )
    return project_scoped


async def _load_draft(db: AsyncSession, draft_id: str) -> WikiPageDraft:
    try:
        did = uuid.UUID(draft_id)
    except ValueError:
        raise HTTPException(400, "Invalid draft ID format")
    draft = await db.get(WikiPageDraft, did)
    if not draft:
        raise HTTPException(404, f"Draft {draft_id} not found")
    return draft


def _draft_out(
    draft: WikiPageDraft,
    page: Optional[WikiPage],
    names: dict[uuid.UUID, str],
) -> DraftResponse:
    """Shape one draft. Pure, so the list endpoints can batch their lookups."""
    return DraftResponse(
        id=draft.id,
        page_id=draft.page_id,
        page_slug=page.slug if page else "",
        page_title=page.title if page else "",
        author_id=draft.author_id,
        author_name=names.get(draft.author_id) if draft.author_id else None,
        content_md=draft.content_md,
        note=draft.note,
        status=draft.status,
        source=draft.source,
        reviewed_by_name=names.get(draft.reviewed_by_id) if draft.reviewed_by_id else None,
        reviewed_at=draft.reviewed_at.isoformat() if draft.reviewed_at else None,
        reviewer_note=draft.reviewer_note,
        created_at=draft.created_at.isoformat(),
        updated_at=draft.updated_at.isoformat(),
    )


async def _employee_names(db: AsyncSession, ids) -> dict[uuid.UUID, str]:
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    return {
        row.id: row.name
        for row in (await db.execute(
            select(Employee.id, Employee.name).where(Employee.id.in_(wanted))
        )).all()
    }


async def _draft_response(db: AsyncSession, draft: WikiPageDraft) -> DraftResponse:
    """Single-draft shortcut for the endpoints that address exactly one draft.

    Three primary-key gets is not an N+1 when there is one draft; it becomes one when a
    list calls it per row, which is what `_draft_responses` below exists to avoid.
    """
    page = await db.get(WikiPage, draft.page_id)
    names = {}
    for emp_id in (draft.author_id, draft.reviewed_by_id):
        if emp_id and emp_id not in names:
            employee = await db.get(Employee, emp_id)
            if employee:
                names[emp_id] = employee.name
    return _draft_out(draft, page, names)


async def _draft_responses(
    db: AsyncSession, drafts: list[WikiPageDraft], pages: dict[uuid.UUID, WikiPage]
) -> list[DraftResponse]:
    """Shape a whole page of drafts with one employee lookup for the batch.

    _draft_response issues three primary-key SELECTs per draft (page, author, reviewer), so
    a 50-draft page cost ~150 round trips plus the caller's own per-draft page load.
    """
    names = await _employee_names(
        db, [i for d in drafts for i in (d.author_id, d.reviewed_by_id)]
    )
    return [_draft_out(d, pages.get(d.page_id), names) for d in drafts]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/wiki/pages/{slug:path}/drafts", response_model=DraftResponse, status_code=201)
async def propose_draft(
    slug: str,
    body: ProposeDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Propose an edit to an existing wiki page. Creates a pending draft for editor review."""
    if slug in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        raise HTTPException(400, "Cannot propose drafts for reserved pages")

    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    if not await _can_propose(db, user, page):
        raise HTTPException(403, "Insufficient permission to propose a draft for this page")

    draft = await wiki_service.create_draft(
        db,
        page_id=page.id,
        author_id=user.id,
        content_md=body.content_md,
        note=body.note,
        source="web_ui",
    )
    await log_audit(db, user, "create", "wiki_draft", str(draft.id), reason=f"draft for: {slug}")
    await db.commit()
    await db.refresh(draft)
    return await _draft_response(db, draft)


@router.get("/wiki/drafts", response_model=list[DraftResponse])
async def list_all_drafts(
    status: Optional[str] = Query("pending", description="Filter by status: pending | approved | rejected"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """List wiki drafts. Editors see drafts for pages they can review. Admins see all.

    The reviewability rule is expressed in SQL rather than applied per row after the fetch.
    LIMIT ran *before* the old `_can_review` loop, so a reviewer scoped to one workspace was
    served whatever fraction of the newest 50 drafts happened to be theirs — anything from a
    full page to an empty one — with no signal that the rest of their queue existed.
    """
    stmt = (
        select(WikiPageDraft, WikiPage)
        .join(WikiPage, WikiPage.id == WikiPageDraft.page_id)
        .order_by(WikiPageDraft.created_at.desc())
    )
    if status:
        stmt = stmt.where(WikiPageDraft.status == status)

    reviewable = _build_reviewable_filter(user)
    if reviewable is not None:
        stmt = stmt.where(reviewable)

    rows = (await db.execute(stmt.limit(limit))).all()
    drafts = [row[0] for row in rows]
    pages = {page.id: page for _, page in rows}
    return await _draft_responses(db, drafts, pages)


@router.get("/wiki/pages/{slug:path}/drafts", response_model=list[DraftResponse])
async def list_page_drafts(
    slug: str,
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List drafts for a specific wiki page."""
    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    if not await _can_review(db, user, page):
        raise HTTPException(403, "Insufficient permission to view drafts for this page")

    stmt = (
        select(WikiPageDraft)
        .where(WikiPageDraft.page_id == page.id)
        .order_by(WikiPageDraft.created_at.desc())
    )
    if status:
        stmt = stmt.where(WikiPageDraft.status == status)

    drafts = (await db.execute(stmt)).scalars().all()
    # One employee lookup for the batch: this list is unbounded (every draft ever proposed
    # for the page) and _draft_response would re-fetch the same page row for each one.
    return await _draft_responses(db, list(drafts), {page.id: page})


@router.get("/wiki/drafts/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: str,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Get a single draft by ID."""
    draft = await _load_draft(db, draft_id)
    page = await db.get(WikiPage, draft.page_id)
    if not page or not await _can_review(db, user, page):
        raise HTTPException(403, "Insufficient permission to view this draft")
    return await _draft_response(db, draft)


@router.post("/wiki/drafts/{draft_id}/approve", response_model=DraftResponse)
async def approve_draft(
    draft_id: str,
    body: ApproveDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Approve a pending draft. Optionally provide edited content before approving."""
    draft = await _load_draft(db, draft_id)
    if draft.status != "pending":
        raise HTTPException(400, f"Draft is already {draft.status}")

    page = await db.get(WikiPage, draft.page_id)
    if not page or not await _can_review(db, user, page):
        raise HTTPException(403, "Insufficient permission to approve drafts for this page")

    await wiki_service.approve_draft(
        db, draft, user.id,
        reviewer_note=body.reviewer_note,
        edited_content_md=body.edited_content_md,
    )
    await log_audit(db, user, "update", "wiki_draft", str(draft.id), reason=f"approved draft for: {page.slug}")
    await db.commit()
    await db.refresh(draft)
    return await _draft_response(db, draft)


@router.post("/wiki/drafts/{draft_id}/reject", response_model=DraftResponse)
async def reject_draft(
    draft_id: str,
    body: RejectDraftRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Reject a pending draft. reviewer_note is required."""
    draft = await _load_draft(db, draft_id)
    if draft.status != "pending":
        raise HTTPException(400, f"Draft is already {draft.status}")

    page = await db.get(WikiPage, draft.page_id)
    if not page or not await _can_review(db, user, page):
        raise HTTPException(403, "Insufficient permission to reject drafts for this page")

    await wiki_service.reject_draft(db, draft, user.id, body.reviewer_note)
    await log_audit(db, user, "update", "wiki_draft", str(draft.id), reason=f"rejected draft for: {page.slug}")
    await db.commit()
    await db.refresh(draft)
    return await _draft_response(db, draft)
