"""
Wiki REST router — admin/portal access to LLM-compiled wiki pages.

Permission model v2:
  - wiki:read:own_dept → global wiki + project-scoped wiki (if member)
  - wiki:read:all → all wiki pages regardless of scope
  - Admin → full access

Scope filtering:
  - Global wiki pages (scope_type='global') → visible to all with wiki:read
  - Project-scoped wiki (scope_type='project') → visible only to workspace members + admin
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.database import get_db
from app.database.models import (
    Employee,
    ProjectMember,
    Source,
    WikiPage,
    WikiPageRevision,
)
from app.services import wiki_service
from app.services.audit_service import log_audit
from app.services.auth_service import get_current_user, require_permission
from app.services.permission_engine import (
    _get_user_permissions,
    get_scope_level,
    get_workspace_role,
    workspace_role_can,
)

router = APIRouter()


class WikiPageSummary(BaseModel):
    slug: str
    title: str
    page_type: str
    summary: str
    knowledge_type_slugs: list[str]
    source_ids: list[uuid.UUID]
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None
    version: int
    updated_at: str


class WikiPageDetail(WikiPageSummary):
    content_md: str
    backlinks: list[str]
    outlinks: list[str]
    orphaned: bool = False
    provenance_complete: bool = False
    source_documents: list[dict] = Field(default_factory=list)


class WikiDirectEditRequest(BaseModel):
    content_md: str
    change_note: Optional[str] = None

    @field_validator("content_md")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content_md must not be empty")
        return v


class WikiRevisionSummary(BaseModel):
    id: uuid.UUID
    version: int
    change_type: str
    changed_by_name: Optional[str] = None
    change_note: Optional[str] = None
    created_at: str


def _summary(p: WikiPage) -> WikiPageSummary:
    return WikiPageSummary(
        slug=p.slug,
        title=p.title,
        page_type=p.page_type,
        summary=p.summary or "",
        knowledge_type_slugs=p.knowledge_type_slugs or [],
        source_ids=list(p.source_ids or []),
        scope_type=p.scope_type or "global",
        scope_id=p.scope_id,
        version=p.version or 1,
        updated_at=p.updated_at.isoformat() if p.updated_at else "",
    )


def _detail(
    p: WikiPage,
    backlinks: list[str],
    outlinks: list[str],
    source_documents: Optional[list[dict]] = None,
) -> WikiPageDetail:
    return WikiPageDetail(
        **_summary(p).model_dump(),
        content_md=p.content_md or "",
        backlinks=sorted(backlinks),
        outlinks=sorted(outlinks),
        orphaned=p.orphaned or False,
        provenance_complete=p.provenance_complete or False,
        source_documents=source_documents or [],
    )


async def _can_read_page_scope(db: AsyncSession, user: Employee, page) -> bool:
    """Same scope rule get_wiki_page enforces, reusable by the graph endpoints."""
    if page.scope_type != "project" or not page.scope_id:
        return True
    if user.role == "admin":
        return True
    if "wiki:read:all" in _get_user_permissions(user):
        return True
    member = (await db.execute(
        select(ProjectMember.role).where(
            ProjectMember.project_id == page.scope_id,
            ProjectMember.employee_id == user.id,
        )
    )).scalar_one_or_none()
    return member is not None


async def _restrict_graph_to_visible(db: AsyncSession, user: Employee, graph: dict) -> dict:
    """Drop nodes and edges the caller may not see.

    get_neighborhood's recursive CTE has no scope predicate, and wiki_links edges are
    keyed on slugs that are only unique per (slug, scope_type, scope_id). Filtering the
    walk's output keeps the CTE unchanged while making the response scope-correct.
    """
    scope_filter = _build_wiki_scope_filter(user)
    if scope_filter is None:
        return graph  # admin or wiki:read:all — nothing to hide

    slugs = [n["slug"] for n in graph.get("nodes", []) if n.get("slug")]
    if not slugs:
        return graph

    visible = set((await db.execute(
        select(WikiPage.slug).where(WikiPage.slug.in_(slugs), scope_filter)
    )).scalars().all())

    return {
        **graph,
        "nodes": [n for n in graph.get("nodes", []) if n.get("slug") in visible],
        "edges": [
            e for e in graph.get("edges", [])
            if e.get("from") in visible and e.get("to") in visible
        ],
    }


def _build_wiki_scope_filter(user: Employee):
    """Build SQLAlchemy filter for wiki pages based on user permissions.

    Returns None if user can see everything (admin / wiki:read:all).
    Returns a filter clause otherwise.
    """
    if user.role == "admin":
        return None  # No filter

    perms = _get_user_permissions(user)
    scope_level = get_scope_level(list(perms), "wiki", "read")

    if scope_level == "all":
        return None  # No filter

    if scope_level == "own_dept":
        # Show: global wiki + project-scoped wiki where user is a member
        return or_(
            WikiPage.scope_type == "global",
            WikiPage.scope_id.in_(
                select(ProjectMember.project_id)
                .where(ProjectMember.employee_id == user.id)
            ),
        )

    # No wiki:read permission at all — should have been caught by require_permission
    return WikiPage.id == None  # noqa: E711 — empty result


@router.get("/wiki/pages", response_model=list[WikiPageSummary])
async def list_wiki_pages(
    response: Response,
    page_type: Optional[str] = Query(None),
    knowledge_type_slug: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """List wiki pages filtered by user's permission scope.

    Sets an `X-Total-Count` response header (count under the same filters,
    ignoring limit/offset) so callers can paginate server-side without a
    breaking change to the response body shape.
    """
    filters = [WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG])]

    scope_filter = _build_wiki_scope_filter(user)
    if scope_filter is not None:
        filters.append(scope_filter)
    if page_type:
        filters.append(WikiPage.page_type == page_type)
    if knowledge_type_slug:
        filters.append(WikiPage.knowledge_type_slugs.any(knowledge_type_slug))  # type: ignore[arg-type]

    count_stmt = select(func.count()).select_from(WikiPage).where(*filters)
    total = (await db.execute(count_stmt)).scalar_one()
    response.headers["X-Total-Count"] = str(total)

    stmt = (
        select(WikiPage)
        .where(*filters)
        .order_by(WikiPage.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.execute(stmt)
    return [_summary(p) for p in result.scalars().all()]


class WikiStats(BaseModel):
    total: int
    by_type: dict[str, int]
    last_updated: Optional[str] = None


@router.get("/wiki/stats", response_model=WikiStats)
async def get_wiki_stats(
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """Facet counts for the wiki landing page (total, per-type, last updated).

    Lets the UI render the stats bar and tab counts with two cheap aggregate
    queries instead of pulling every page just to count them client-side.
    """
    filters = [WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG])]
    scope_filter = _build_wiki_scope_filter(user)
    if scope_filter is not None:
        filters.append(scope_filter)

    rows = (
        await db.execute(
            select(WikiPage.page_type, func.count())
            .where(*filters)
            .group_by(WikiPage.page_type)
        )
    ).all()
    by_type = {ptype: count for ptype, count in rows}
    total = sum(by_type.values())
    last_updated = (
        await db.execute(select(func.max(WikiPage.updated_at)).where(*filters))
    ).scalar_one_or_none()

    return WikiStats(
        total=total,
        by_type=by_type,
        last_updated=last_updated.isoformat() if last_updated else None,
    )


class WikiTreeItem(BaseModel):
    slug: str
    title: str
    page_type: str
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None


@router.get("/wiki/tree", response_model=list[WikiTreeItem])
async def list_wiki_tree(
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """Slim page list for the navigation sidebar tree.

    Returns only the fields the tree renders (slug/title/type/scope) — roughly
    a seventh of the full `/wiki/pages` summary payload — so the nav tree stays
    lightweight while the page grid paginates server-side via `/wiki/pages`.
    """
    filters = [WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG])]
    scope_filter = _build_wiki_scope_filter(user)
    if scope_filter is not None:
        filters.append(scope_filter)

    rows = (
        await db.execute(
            select(
                WikiPage.slug,
                WikiPage.title,
                WikiPage.page_type,
                WikiPage.scope_type,
                WikiPage.scope_id,
            )
            .where(*filters)
            .order_by(WikiPage.page_type, WikiPage.title)
        )
    ).all()
    return [
        WikiTreeItem(
            slug=r.slug,
            title=r.title,
            page_type=r.page_type,
            scope_type=r.scope_type or "global",
            scope_id=r.scope_id,
        )
        for r in rows
    ]


class WikiSearchResult(BaseModel):
    slug: str
    title: str
    page_type: str
    summary: str
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None
    score: float


@router.get("/wiki/search", response_model=list[WikiSearchResult])
async def search_wiki_pages(
    q: str = Query(..., min_length=1),
    top_k: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """Semantic search over wiki pages, scoped identically to GET /wiki/pages."""
    if not q.strip():
        raise HTTPException(422, "q cannot be empty")

    scope_clause = _build_wiki_scope_filter(user)

    try:
        registry = ProviderRegistry(db)
        embedding_provider = await registry.get_embedding(task="search_query")
        query_embedding = await embedding_provider.embed(q)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Wiki search embedding failed for query={!r}", q)
        raise HTTPException(status_code=502, detail="Search is temporarily unavailable.") from exc

    try:
        hits = await wiki_service.search_pages_semantic(
            db, query_embedding=query_embedding, top_k=top_k, scope_clause=scope_clause,
        )
    except Exception as exc:
        logger.exception("Wiki search failed for query={!r}", q)
        raise HTTPException(status_code=502, detail="Search is temporarily unavailable.") from exc

    return [
        WikiSearchResult(
            slug=p.slug,
            title=p.title,
            page_type=p.page_type,
            summary=p.summary or "",
            scope_type=p.scope_type or "global",
            scope_id=p.scope_id,
            score=round(score, 4),
        )
        for p, score in hits
    ]


@router.get("/wiki/pages/{slug:path}", response_model=WikiPageDetail)
async def get_wiki_page(
    slug: str,
    scope_type: Optional[str] = Query(None),
    # Typed rather than parsed in the body: `uuid.UUID(scope_id)` on a client-supplied
    # string raised ValueError, and no ValueError handler is registered, so a typo in a
    # query string answered 500 instead of 422.
    scope_id: Optional[uuid.UUID] = Query(None),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    if scope_type:
        page = await wiki_service.get_page_by_slug(db, slug, scope_type=scope_type, scope_id=scope_id)
    else:
        page = await wiki_service.get_page_by_slug(db, slug, scope_type="global", scope_id=None)
        if not page:
            page = await wiki_service.get_page_by_slug_any_scope(db, slug)

    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    # Check scope access
    if page.scope_type == "project" and page.scope_id:
        if user.role != "admin":
            perms = _get_user_permissions(user)
            if "wiki:read:all" not in perms:
                # Check workspace membership
                member = (await db.execute(
                    select(ProjectMember.role)
                    .where(
                        ProjectMember.project_id == page.scope_id,
                        ProjectMember.employee_id == user.id,
                    )
                )).scalar_one_or_none()
                if not member:
                    raise HTTPException(403, "Access denied — you are not a member of this workspace")

    backlinks = await wiki_service.get_backlinks(db, slug)
    outlinks = await wiki_service.get_outlinks(db, slug)
    sources = []
    if page.source_ids:
        source_rows = (await db.execute(
            select(Source.id, Source.title, Source.file_name, Source.status)
            .where(Source.id.in_(page.source_ids))
        )).all()
        by_id = {row.id: row for row in source_rows}
        sources = [
            {
                "id": str(source_id),
                "title": (by_id[source_id].title or by_id[source_id].file_name)
                if source_id in by_id else "Deleted source",
                "status": by_id[source_id].status if source_id in by_id else "deleted",
            }
            for source_id in page.source_ids
        ]
    return _detail(page, backlinks, outlinks, sources)


@router.get("/wiki/index")
async def get_wiki_index(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("wiki:read"),
):
    page = await wiki_service.get_page_by_slug(db, wiki_service.INDEX_SLUG)
    return {"content_md": page.content_md if page else ""}


@router.get("/wiki/log")
async def get_wiki_log(
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("wiki:read"),
):
    page = await wiki_service.get_page_by_slug(db, wiki_service.LOG_SLUG)
    return {"content_md": page.content_md if page else ""}


@router.put("/wiki/pages/{slug:path}", response_model=WikiPageDetail)
async def direct_edit_wiki_page(
    slug: str,
    body: WikiDirectEditRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Direct sync edit by an editor or admin. No review step. Creates a revision."""
    if slug in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        raise HTTPException(400, "Cannot directly edit reserved pages")

    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    # Permission: workspace editor+ OR wiki:write:all OR admin
    if user.role != "admin":
        if page.scope_type == "project" and page.scope_id:
            member_role = await get_workspace_role(db, user, page.scope_id)
            if not member_role or not workspace_role_can(member_role, "editor"):
                raise HTTPException(403, "Requires editor role or above in this workspace")
        else:
            perms = _get_user_permissions(user)
            if "wiki:write:all" not in perms:
                raise HTTPException(403, "Requires wiki:write:all permission to directly edit global wiki pages")

    await wiki_service.direct_edit_page(db, page, user.id, body.content_md, body.change_note)
    await log_audit(db, user, "update", "wiki_page", str(page.id), reason=f"direct edit: {slug}")
    await db.commit()
    await db.refresh(page)

    backlinks = await wiki_service.get_backlinks(db, slug)
    outlinks = await wiki_service.get_outlinks(db, slug)
    return _detail(page, backlinks, outlinks)


@router.get("/wiki/pages/{slug:path}/revisions", response_model=list[WikiRevisionSummary])
async def list_wiki_page_revisions(
    slug: str,
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """List revision history for a wiki page (most recent first)."""
    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    from app.database.models import Employee as Emp
    rows = (await db.execute(
        select(WikiPageRevision, Emp.name.label("changed_by_name"))
        .outerjoin(Emp, WikiPageRevision.changed_by_id == Emp.id)
        .where(WikiPageRevision.page_id == page.id)
        .order_by(WikiPageRevision.version.desc())
        .limit(limit)
    )).all()

    return [
        WikiRevisionSummary(
            id=r.WikiPageRevision.id,
            version=r.WikiPageRevision.version,
            change_type=r.WikiPageRevision.change_type,
            changed_by_name=r.changed_by_name,
            change_note=r.WikiPageRevision.change_note,
            created_at=r.WikiPageRevision.created_at.isoformat(),
        )
        for r in rows
    ]


@router.post("/wiki/pages/{slug:path}/revisions/{version}/rollback", response_model=WikiPageDetail)
async def rollback_wiki_page(
    slug: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """Rollback a wiki page to a specific version. Admin only."""
    if user.role != "admin":
        raise HTTPException(403, "Only admins can rollback wiki pages")

    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    try:
        await wiki_service.rollback_to_revision(db, page, version, user.id)
    except ValueError as e:
        raise HTTPException(404, str(e))

    await log_audit(db, user, "update", "wiki_page", str(page.id), reason=f"rollback to v{version}: {slug}")
    await db.commit()
    await db.refresh(page)

    backlinks = await wiki_service.get_backlinks(db, slug)
    outlinks = await wiki_service.get_outlinks(db, slug)
    return _detail(page, backlinks, outlinks)


@router.get("/wiki/orphaned", response_model=list[WikiPageSummary])
async def list_orphaned_wiki_pages(
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
):
    """List wiki pages that have no source document (orphaned). Admin only."""
    if user.role != "admin":
        raise HTTPException(403, "Only admins can view orphaned pages")

    pages = (await db.execute(
        select(WikiPage).where(WikiPage.orphaned == True).order_by(WikiPage.updated_at.desc())  # noqa: E712
    )).scalars().all()
    return [_summary(p) for p in pages]


@router.delete("/wiki/pages/{slug:path}")
async def delete_wiki_page(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:delete"),
):
    """Delete a wiki page and cascade-cleanup all references.

    Authorised the same way direct_edit_wiki_page authorises a write: workspace editor+ for
    a project-scoped page, the `:all` grant for a global one. The guard here used to be
    `user.role not in ("admin", "super_admin")` — `"super_admin"` is not a role this
    codebase ever assigns, so that reduced to "admin only" and made `wiki:delete:own_dept`
    and `wiki:delete:all` dead permissions: granting either to a custom role did nothing
    while ACCESS-CONTROL.md documented them as working.
    """
    if slug in (wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG):
        raise HTTPException(400, "Cannot delete reserved pages")

    # get_page_by_slug defaults to scope_type="global", so every project-scoped page 404'd
    # on delete regardless of who asked.
    page = await wiki_service.get_page_by_slug_any_scope(db, slug)
    if not page:
        raise HTTPException(404, f"Wiki page not found: {slug}")

    if user.role != "admin":
        if page.scope_type == "project" and page.scope_id:
            member_role = await get_workspace_role(db, user, page.scope_id)
            if not member_role:
                # 404 rather than 403: a non-member is not entitled to learn that a page
                # exists inside a workspace they cannot see, and the old global-only
                # lookup already answered 404 here.
                raise HTTPException(404, f"Wiki page not found: {slug}")
            if not workspace_role_can(member_role, "editor"):
                raise HTTPException(403, "Requires editor role or above in this workspace")
        elif "wiki:delete:all" not in _get_user_permissions(user):
            raise HTTPException(
                403, "Requires wiki:delete:all permission to delete global wiki pages"
            )

    # delete_page_cascade strips wiki_links by slug (that table is keyed on slugs alone and
    # carries no scope column) but resolves the row to delete with the *global-scoped*
    # get_page_by_slug. Now that project-scoped pages reach this code at all, resolve what
    # the cascade will hit before calling it: on a project page it either finds nothing or,
    # when a global page happens to share the slug, deletes that one instead.
    cascade_target = await wiki_service.get_page_by_slug(db, slug)
    if cascade_target is not None and cascade_target.id != page.id:
        raise HTTPException(
            409,
            f"A global wiki page shares the slug '{slug}' — deleting by slug alone is "
            "ambiguous. Delete the global page first.",
        )

    deleted_title = page.title
    await log_audit(db, user, "delete", "wiki", slug, reason=deleted_title)
    await wiki_service.delete_page_cascade(db, slug)
    if cascade_target is None:
        # Project-scoped page: the cascade cleaned the edges but found no row to remove.
        await db.delete(page)
    await wiki_service.regenerate_index(db)
    await wiki_service.append_log(db, f"Deleted page: {deleted_title} ({slug})")
    await db.commit()
    return {"ok": True, "deleted_slug": slug}


@router.get("/wiki/graph")
async def get_wiki_graph(
    slug: Optional[str] = Query(None, description="Center the graph on this slug; omit for full graph"),
    depth: int = Query(1, ge=1, le=3),
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: Employee = require_permission("wiki:read"),
):
    """Return nodes/edges for visualization with scope filtering."""
    if slug:
        # The comment that used to sit here claimed a check the code did not perform.
        # get_neighborhood walks wiki_links through a recursive CTE with no scope
        # predicate at all, so a non-member received slugs and titles of pages in
        # workspaces they cannot open — the exact case get_wiki_page blocks with a 403.
        centre = await wiki_service.get_page_by_slug_any_scope(db, slug)
        if not centre:
            raise HTTPException(404, f"Wiki page not found: {slug}")
        if not await _can_read_page_scope(db, user, centre):
            raise HTTPException(
                403, "Access denied — you are not a member of this workspace"
            )

        neighborhood = await wiki_service.get_neighborhood(db, slug, depth=depth)
        return await _restrict_graph_to_visible(db, user, neighborhood)

    # Full graph — paginated, with scope filtering
    from sqlalchemy import func as sqlfunc
    from sqlalchemy import outerjoin

    from app.database.models import Project, WikiLink

    base_filter = WikiPage.slug.notin_([wiki_service.INDEX_SLUG, wiki_service.LOG_SLUG])

    # Apply scope filter
    scope_filter = _build_wiki_scope_filter(user)

    # Total count
    count_stmt = select(sqlfunc.count()).select_from(WikiPage).where(base_filter)
    if scope_filter is not None:
        count_stmt = count_stmt.where(scope_filter)
    total = (await db.execute(count_stmt)).scalar() or 0

    # Fetch paginated nodes
    stmt = (
        select(
            WikiPage.slug,
            WikiPage.title,
            WikiPage.page_type,
            WikiPage.scope_type,
            WikiPage.scope_id,
            Project.name.label("scope_name"),
        )
        .select_from(
            outerjoin(WikiPage, Project, WikiPage.scope_id == Project.id)
        )
        .where(base_filter)
        .order_by(WikiPage.slug)
        .offset(offset)
        .limit(limit)
    )
    if scope_filter is not None:
        stmt = stmt.where(scope_filter)

    pages = (await db.execute(stmt)).all()

    # Edges — restricted to the slugs actually returned in this response.
    #
    # This previously selected every row in wiki_links with no WHERE and no LIMIT, while
    # the *nodes* beside it were correctly scope-filtered. That leaked the complete
    # org-wide link graph (slug pairs from workspaces the caller cannot read) and made the
    # query a full-table transfer on every first-page request.
    if offset == 0:
        visible_slugs = [r.slug for r in pages]
        if visible_slugs:
            edges = (await db.execute(
                select(WikiLink.from_slug, WikiLink.to_slug).where(
                    WikiLink.from_slug.in_(visible_slugs),
                    WikiLink.to_slug.in_(visible_slugs),
                )
            )).all()
        else:
            edges = []
    else:
        edges = []

    return {
        "nodes": [
            {
                "slug": r.slug,
                "title": r.title,
                "page_type": r.page_type,
                "scope_type": r.scope_type or "global",
                "scope_id": str(r.scope_id) if r.scope_id else None,
                "scope_name": r.scope_name,
            }
            for r in pages
        ],
        "edges": [{"from": r.from_slug, "to": r.to_slug} for r in edges],
        "total": total,
        "offset": offset,
        "has_more": offset + limit < total,
    }
