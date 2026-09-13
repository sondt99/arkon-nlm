"""
Wiki Service — CRUD, semantic search, and wikilink graph for WikiPage.

The wiki is the LLM-compiled knowledge layer. It replaces chunk-based RAG.
Each page is markdown that may contain `[[slug]]` wikilinks; after every
upsert, refresh_links() re-parses the content and rewrites the wiki_links
edge table so 1-2 hop graph queries (backlinks, neighborhood) stay fast in
PostgreSQL — no separate graph DB needed.

Scope support: every page belongs to a scope (global or workspace). Query
functions accept scope_type/scope_id to isolate results. Default is global.
"""

import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy import false as sql_false
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    WikiLink,
    WikiPage,
    WikiPageContribution,
    WikiPageDraft,
    WikiPageRevision,
)

# Reserved page slugs — these are regular WikiPage rows but treated specially.
INDEX_SLUG = "_index"
LOG_SLUG = "_log"

# Recognized page types — used for filtering and prompt hints to the compiler.
PAGE_TYPES = {"entity", "concept", "source", "topic", "synthesis", "index", "log"}

# `[[slug]]` or `[[slug|display text]]` — captures the slug only.
_WIKILINK_RE = re.compile(r"\[\[([^\]\|]+)(?:\|[^\]]*)?]]")


# ---------------------------------------------------------------------------
# Scope filter helper
# ---------------------------------------------------------------------------

def _rbac_visibility_clause(
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
):
    """SQL clause for MCP / export / RAG wiki visibility.

    None on both arguments = unrestricted.
    An empty list on either argument is fail-closed (matches nothing for that axis).
    Pages compiled from an allowed source stay visible even if their KT array is empty.
    Pages with no source_ids are visible only when their KT slugs overlap the allow-list.
    Empty KT arrays are never treated as world-readable when a restriction is set.
    """
    if allowed_kt_slugs is None and allowed_source_ids is None:
        return None
    if allowed_source_ids is not None:
        if not allowed_source_ids and not allowed_kt_slugs:
            return sql_false()
        source_uuids = [
            s if isinstance(s, uuid.UUID) else uuid.UUID(str(s))
            for s in allowed_source_ids
        ]
        source_match = WikiPage.source_ids.overlap(source_uuids) if source_uuids else sql_false()
        if allowed_kt_slugs:
            return or_(
                source_match,
                and_(
                    func.cardinality(WikiPage.source_ids) == 0,
                    WikiPage.knowledge_type_slugs.overlap(allowed_kt_slugs),
                ),
            )
        return source_match
    if allowed_kt_slugs is not None:
        if not allowed_kt_slugs:
            return sql_false()
        return WikiPage.knowledge_type_slugs.overlap(allowed_kt_slugs)
    return None


async def wiki_visibility_for(db, user):
    """`(allowed_kt_slugs, allowed_source_ids)` for an Employee on a REST surface.

    One definition of wiki visibility, shared by every surface that reads pages — MCP
    resolves it from a token, chat and the REST wiki routers resolve it from the session
    user, and all three must agree. It lived privately in `app/routers/chat.py`, which is
    why the REST wiki routers never applied it at all.
    """
    from app.services.mcp_auth_service import MCPAuthService

    identity = await MCPAuthService(db)._resolve_scope(user)
    return identity.wiki_visibility()


def page_is_visible(
    page: WikiPage,
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
) -> bool:
    """Python-side counterpart of `_rbac_visibility_clause` for single-page reads."""
    if allowed_kt_slugs is None and allowed_source_ids is None:
        return True
    source_ids = list(page.source_ids or [])
    kts = list(page.knowledge_type_slugs or [])
    if allowed_source_ids is not None:
        allowed = {
            s if isinstance(s, uuid.UUID) else uuid.UUID(str(s))
            for s in allowed_source_ids
        }
        if any((sid if isinstance(sid, uuid.UUID) else uuid.UUID(str(sid))) in allowed for sid in source_ids):
            return True
        if source_ids:
            return False
        if allowed_kt_slugs:
            return any(s in allowed_kt_slugs for s in kts)
        return False
    if allowed_kt_slugs is not None:
        if not allowed_kt_slugs or not kts:
            return False
        return any(s in allowed_kt_slugs for s in kts)
    return True


def _scope_filter(scope_type: str = "global", scope_id: Optional[uuid.UUID] = None):
    """Return SQLAlchemy WHERE clauses for scope filtering."""
    if scope_id:
        return and_(WikiPage.scope_type == scope_type, WikiPage.scope_id == scope_id)
    return and_(WikiPage.scope_type == scope_type, WikiPage.scope_id.is_(None))


# ---------------------------------------------------------------------------
# Wikilink parsing & graph maintenance
# ---------------------------------------------------------------------------

def extract_wikilinks(content_md: str) -> list[str]:
    """Return the list of slugs referenced by `[[slug]]` patterns, deduped."""
    if not content_md:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _WIKILINK_RE.finditer(content_md):
        slug = match.group(1).strip()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


async def refresh_links(
    session: AsyncSession,
    from_slug: str,
    content_md: str,
) -> None:
    """
    Replace all outgoing edges from `from_slug` with the wikilinks parsed from
    its current `content_md`. Self-links and links pointing to the page itself
    are dropped to keep the graph sane.
    """
    await session.execute(
        delete(WikiLink).where(WikiLink.from_slug == from_slug)
    )
    targets = [s for s in extract_wikilinks(content_md) if s != from_slug]
    if not targets:
        return
    await session.execute(
        pg_insert(WikiLink)
        .values([{"from_slug": from_slug, "to_slug": t} for t in targets])
        .on_conflict_do_nothing()
    )


async def get_backlinks(session: AsyncSession, slug: str) -> list[str]:
    """Slugs of pages that link to `slug`."""
    result = await session.execute(
        select(WikiLink.from_slug).where(WikiLink.to_slug == slug)
    )
    return [row[0] for row in result.all()]


async def get_outlinks(session: AsyncSession, slug: str) -> list[str]:
    """Slugs that `slug` links to."""
    result = await session.execute(
        select(WikiLink.to_slug).where(WikiLink.from_slug == slug)
    )
    return [row[0] for row in result.all()]


async def get_neighborhood(
    session: AsyncSession,
    slug: str,
    depth: int = 1,
) -> dict:
    """
    Return nodes (slug, title, page_type) and edges within `depth` hops of `slug`.
    Uses an undirected recursive CTE — useful for Obsidian-style graph view.
    """
    depth = max(1, min(depth, 3))  # cap at 3 hops to keep queries cheap
    # Recursive CTE walking both directions; stop at depth.
    cte_sql = text(
        """
        WITH RECURSIVE walk(slug, dist) AS (
            SELECT CAST(:start AS varchar), 0
          UNION
            SELECT
              CASE WHEN l.from_slug = w.slug THEN l.to_slug ELSE l.from_slug END,
              w.dist + 1
            FROM walk w
            JOIN wiki_links l
              ON l.from_slug = w.slug OR l.to_slug = w.slug
            WHERE w.dist < :depth
        )
        SELECT DISTINCT slug FROM walk
        """
    )
    rows = await session.execute(cte_sql, {"start": slug, "depth": depth})
    slugs = [r[0] for r in rows.all()]
    if not slugs:
        return {"nodes": [], "edges": []}

    pages_result = await session.execute(
        select(WikiPage.slug, WikiPage.title, WikiPage.page_type)
        .where(WikiPage.slug.in_(slugs))
    )
    nodes = [
        {"slug": r.slug, "title": r.title, "page_type": r.page_type}
        for r in pages_result.all()
    ]
    edges_result = await session.execute(
        select(WikiLink.from_slug, WikiLink.to_slug)
        .where(and_(WikiLink.from_slug.in_(slugs), WikiLink.to_slug.in_(slugs)))
    )
    edges = [{"from": r.from_slug, "to": r.to_slug} for r in edges_result.all()]
    return {"nodes": nodes, "edges": edges}


# ---------------------------------------------------------------------------
# Page CRUD
# ---------------------------------------------------------------------------

async def get_page_by_slug(
    session: AsyncSession,
    slug: str,
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> Optional[WikiPage]:
    """
    Fetch a page by slug within a specific scope. Restricted identities
    never get a reserved slug as a back door — `_index` / `_log` are
    filtered the same way as every other page.
    """
    stmt = select(WikiPage).where(
        WikiPage.slug == slug,
        _scope_filter(scope_type, scope_id),
    )
    result = await session.execute(stmt)
    page = result.scalars().first()
    if page is None:
        return None
    if not page_is_visible(page, allowed_kt_slugs, allowed_source_ids):
        return None
    return page


async def get_page_by_slug_any_scope(
    session: AsyncSession,
    slug: str,
) -> Optional[WikiPage]:
    """
    Fetch a page by slug across ALL scopes (no scope filtering).
    Used as a fallback when no explicit scope is specified, e.g. global graph view
    clicking on a workspace-scoped wiki page.
    """
    stmt = select(WikiPage).where(WikiPage.slug == slug).limit(1)
    result = await session.execute(stmt)
    return result.scalars().first()


async def list_pages(
    session: AsyncSession,
    page_type: Optional[str] = None,
    knowledge_type_slug: Optional[str] = None,
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
    limit: int = 50,
    offset: int = 0,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> list[WikiPage]:
    """List pages with filtering within a specific scope. Reserved slugs excluded."""
    stmt = (
        select(WikiPage)
        .where(
            WikiPage.slug.notin_([INDEX_SLUG, LOG_SLUG]),
            _scope_filter(scope_type, scope_id),
        )
        .order_by(WikiPage.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    if page_type:
        stmt = stmt.where(WikiPage.page_type == page_type)
    if knowledge_type_slug:
        stmt = stmt.where(WikiPage.knowledge_type_slugs.any(knowledge_type_slug))  # type: ignore[arg-type]
    visibility = _rbac_visibility_clause(allowed_kt_slugs, allowed_source_ids)
    if visibility is not None:
        stmt = stmt.where(visibility)
    result = await session.execute(stmt)
    return list(result.scalars().all())


_SCOPE_CLAUSE_UNSET = object()


async def search_pages_semantic(
    session: AsyncSession,
    query_embedding: list[float],
    top_k: int = 10,
    allowed_kt_slugs: Optional[list[str]] = None,
    allowed_source_ids: Optional[list] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
    spec_id: Optional[str] = None,
    scope_clause=_SCOPE_CLAUSE_UNSET,
) -> list[tuple[WikiPage, float]]:
    """
    Cosine-similarity search over wiki page embeddings within a scope.

    Embeddings live in per-dimension tables (`wiki_page_embeddings_<dim>`).
    The active embedding model spec determines which table to query and which
    `model_spec_id` rows to filter to. Pass `spec_id` explicitly to override —
    only used by tests and internal tooling.

    `scope_clause`, when passed, is a SQLAlchemy clause (or None for "no
    restriction") that overrides scope_type/scope_id entirely — used by
    callers whose scoping can't be expressed as a single global/one-scope
    pair (e.g. "global + every workspace I'm a member of"). Defaults to
    deriving the clause from scope_type/scope_id, unchanged from before.

    Returns (page, similarity) pairs sorted by similarity descending. Returns
    an empty list if no active embedding model is configured.
    """
    from app.ai.embedding_catalog import get_spec
    from app.ai.registry import ProviderRegistry
    from app.database.models import get_embedding_model_for_dim

    if spec_id is None:
        registry = ProviderRegistry(session)
        spec_id = await registry.get_active_embedding_spec_id()
    if not spec_id:
        return []

    spec = get_spec(spec_id)
    Emb = get_embedding_model_for_dim(spec.dimension)

    if scope_clause is _SCOPE_CLAUSE_UNSET:
        scope_clause = _scope_filter(scope_type, scope_id)

    conditions = [
        Emb.model_spec_id == spec.id,
        WikiPage.slug.notin_([INDEX_SLUG, LOG_SLUG]),
    ]
    if scope_clause is not None:
        conditions.append(scope_clause)

    stmt = (
        select(
            WikiPage,
            (1 - Emb.embedding.cosine_distance(query_embedding)).label("similarity"),
        )
        .join(Emb, Emb.page_id == WikiPage.id)
        .where(and_(*conditions))
        .order_by(Emb.embedding.cosine_distance(query_embedding))
        .limit(top_k)
    )
    visibility = _rbac_visibility_clause(allowed_kt_slugs, allowed_source_ids)
    if visibility is not None:
        stmt = stmt.where(visibility)
    result = await session.execute(stmt)
    return [(row[0], float(row[1])) for row in result.all()]


# ---------------------------------------------------------------------------
# Compiler ops application
# ---------------------------------------------------------------------------

async def apply_create(
    session: AsyncSession,
    slug: str,
    title: str,
    page_type: str,
    content_md: str,
    summary: str,
    knowledge_type_slugs: list[str],
    source_ids: list[uuid.UUID],
    embedding: Optional[list[float]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """Insert a new page in the given scope. Conflicts raise — caller should use update."""
    page = WikiPage(
        slug=slug,
        title=title,
        page_type=page_type if page_type in PAGE_TYPES else "concept",
        content_md=content_md,
        summary=summary,
        knowledge_type_slugs=list(knowledge_type_slugs or []),
        source_ids=list(source_ids or []),
        # embedding intentionally omitted: stored in wiki_page_embeddings_<dim>
        scope_type=scope_type,
        scope_id=scope_id,
        version=1,
    )
    _ = embedding  # backward-compat parameter, ignored
    session.add(page)
    await session.flush()
    await refresh_links(session, slug, content_md)
    session.add(WikiPageRevision(
        page_id=page.id, version=page.version,
        content_md=content_md, change_type="agent_compile",
    ))
    return page


async def apply_update(
    session: AsyncSession,
    slug: str,
    new_content_md: str,
    summary: Optional[str] = None,
    title: Optional[str] = None,
    add_knowledge_type_slug: Optional[str] = None,
    add_source_id: Optional[uuid.UUID] = None,
    embedding: Optional[list[float]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> Optional[WikiPage]:
    """
    Update an existing page atomically within the given scope:
      - Replace content_md with new_content_md.
      - Optionally update title/summary.
      - Union add_knowledge_type_slug into knowledge_type_slugs.
      - Append add_source_id to source_ids if not present.
      - Bump version, refresh updated_at, refresh embedding if supplied.
    Returns None if the page does not exist.
    """
    page = await get_page_by_slug(session, slug, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        return None

    page.content_md = new_content_md
    if title is not None:
        page.title = title
    if summary is not None:
        page.summary = summary
    if add_knowledge_type_slug and add_knowledge_type_slug not in (page.knowledge_type_slugs or []):
        page.knowledge_type_slugs = [*(page.knowledge_type_slugs or []), add_knowledge_type_slug]
    if add_source_id and add_source_id not in (page.source_ids or []):
        page.source_ids = [*(page.source_ids or []), add_source_id]
    # Embeddings are no longer stored on WikiPage; the compiler calls
    # _reembed_pages after this returns, which writes into the active
    # wiki_page_embeddings_<dim> table. The `embedding` parameter is accepted
    # only for backward compatibility and ignored here.
    _ = embedding
    page.version = (page.version or 1) + 1
    await session.flush()
    await refresh_links(session, slug, new_content_md)
    session.add(WikiPageRevision(
        page_id=page.id, version=page.version,
        content_md=new_content_md, change_type="agent_compile",
    ))
    return page


async def upsert_page(
    session: AsyncSession,
    slug: str,
    title: str,
    page_type: str,
    content_md: str,
    summary: str,
    knowledge_type_slugs: list[str],
    source_ids: list[uuid.UUID],
    embedding: Optional[list[float]] = None,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """Create-or-update by slug within a scope."""
    # Acquire a transaction-level advisory lock based on the hash of the slug
    # to serialize concurrent upserts for the exact same page.
    lock_query = select(func.pg_advisory_xact_lock(func.hashtext(slug)))
    await session.execute(lock_query)

    existing = await get_page_by_slug(session, slug, scope_type=scope_type, scope_id=scope_id)
    if existing is None:
        return await apply_create(
            session, slug, title, page_type, content_md, summary,
            knowledge_type_slugs, source_ids, embedding,
            scope_type=scope_type, scope_id=scope_id,
        )
    return await apply_update(
        session,
        slug=slug,
        new_content_md=content_md,
        summary=summary,
        title=title,
        add_knowledge_type_slug=knowledge_type_slugs[0] if knowledge_type_slugs else None,
        add_source_id=source_ids[0] if source_ids else None,
        embedding=embedding,
        scope_type=scope_type, scope_id=scope_id,
    ) or existing


# ---------------------------------------------------------------------------
# Reserved pages: _index and _log
# ---------------------------------------------------------------------------

async def regenerate_index(
    session: AsyncSession,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """
    Rebuild the `_index` page within the given scope.
    Grouped by page_type, alphabetical within group. Excludes reserved slugs.
    """
    stmt = (
        select(WikiPage.slug, WikiPage.title, WikiPage.page_type, WikiPage.summary)
        .where(
            WikiPage.slug.notin_([INDEX_SLUG, LOG_SLUG]),
            _scope_filter(scope_type, scope_id),
        )
        .order_by(WikiPage.page_type, WikiPage.title)
    )
    rows = (await session.execute(stmt)).all()

    by_type: dict[str, list[tuple[str, str, str]]] = {}
    for r in rows:
        by_type.setdefault(r.page_type, []).append((r.slug, r.title, r.summary or ""))

    lines = ["# Wiki Index", ""]
    if not by_type:
        lines.append("_(empty — no pages yet)_")
    else:
        for ptype in sorted(by_type.keys()):
            lines.append(f"## {ptype.capitalize()}")
            lines.append("")
            for slug, title, summary in by_type[ptype]:
                summary_part = f" — {summary}" if summary else ""
                lines.append(f"- [[{slug}|{title}]]{summary_part}")
            lines.append("")

    new_md = "\n".join(lines).rstrip() + "\n"

    # Locked for a different reason than append_log. This overwrites rather than appends, so
    # a lost body is survivable — both writers derive `new_md` from the same table. The real
    # hazards are the `page is None` branch (two concurrent regenerations both create an
    # `_index` row, and nothing makes slug+scope unique for reserved pages) and the `version`
    # increment below, which is a read-modify-write like any other.
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"{INDEX_SLUG}:{scope_type}:{scope_id}")))
    )

    page = await get_page_by_slug(session, INDEX_SLUG, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        page = WikiPage(
            slug=INDEX_SLUG,
            title="Wiki Index",
            page_type="index",
            content_md=new_md,
            summary="Catalog of all wiki pages",
            knowledge_type_slugs=[],
            source_ids=[],
            scope_type=scope_type,
            scope_id=scope_id,
        )
        session.add(page)
    else:
        page.content_md = new_md
        page.version = (page.version or 1) + 1
    await session.flush()
    return page


async def append_log(
    session: AsyncSession,
    entry: str,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> WikiPage:
    """
    Append a timestamped line to the `_log` page within the given scope.
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    line = f"## [{ts}] {entry.strip()}"

    # BEFORE the read, not after: this is a read-modify-write on a string column, so without
    # the lock two concurrent ingests both read the same `content_md` and the second write
    # overwrites the first. Demonstrated with two sessions — the surviving page held only
    # `INGEST-DOC-B`, `INGEST-DOC-A` and the seed line were gone, and `version` went 1->2
    # instead of 1->3. On the page whose entire purpose is being an audit trail.
    #
    # `run_commit_phase` takes this same lock per page slug, but `append_log` and
    # `regenerate_index` are called OUTSIDE its span, which is how they were left unguarded.
    #
    # Keyed on slug AND scope: every scope has its own `_log`, and `hashtext(LOG_SLUG)` alone
    # would serialise a workspace ingest against an unrelated global one.
    await session.execute(
        select(func.pg_advisory_xact_lock(func.hashtext(f"{LOG_SLUG}:{scope_type}:{scope_id}")))
    )

    page = await get_page_by_slug(session, LOG_SLUG, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        page = WikiPage(
            slug=LOG_SLUG,
            title="Wiki Log",
            page_type="log",
            content_md=f"# Wiki Log\n\n{line}\n",
            summary="Chronological activity log",
            knowledge_type_slugs=[],
            source_ids=[],
            scope_type=scope_type,
            scope_id=scope_id,
        )
        session.add(page)
    else:
        existing = page.content_md or "# Wiki Log\n"
        if "_(empty" in existing:
            existing = "# Wiki Log\n"
        page.content_md = existing.rstrip() + f"\n\n{line}\n"
        page.version = (page.version or 1) + 1
    await session.flush()
    return page


# ---------------------------------------------------------------------------
# Page deletion — cascade cleanup
# ---------------------------------------------------------------------------

async def _rebuild_outgoing_links(session: AsyncSession, from_slug: str) -> None:
    """Re-derive the wiki_links rows for one from_slug from the pages that still carry it.

    wiki_links is keyed on (from_slug, to_slug) and has no scope column, so one row can be
    the edge of pages in several scopes at once. Deleting edges by slug alone therefore took
    out structure another scope's page still owns; re-deriving from the surviving content is
    what keeps that scope's graph intact.
    """
    contents = (await session.execute(
        select(WikiPage.content_md).where(WikiPage.slug == from_slug)
    )).scalars().all()
    await refresh_links(session, from_slug, "\n".join(c or "" for c in contents))


async def delete_page_cascade(
    session: AsyncSession,
    slug: str,
    scope_type: str = "global",
    scope_id: Optional[uuid.UUID] = None,
) -> None:
    """
    Delete a wiki page and cascade-cleanup all references, within one scope:
    1. Locate the page in the requested scope — nothing is touched if it is not there
    2. Remove [[slug]] and [[slug|text]] wikilinks from pages in the SAME scope
    3. Delete the page itself
    4. Re-derive the wikilink edges of every slug whose content this call changed

    Steps 1-3 used to run with no scope predicate while the page itself was fetched with
    get_page_by_slug's `scope_type="global"` default. Deleting a global `budget` therefore
    rewrote workspace W's `overview.content_md` and dropped W's internal `[[budget]]` edge,
    stripping a link to a page that still existed; and passing a workspace-scoped slug found
    no page to delete at all, yet still wrecked the global graph and logged success.
    """
    page = await get_page_by_slug(session, slug, scope_type=scope_type, scope_id=scope_id)
    if page is None:
        raise ValueError(
            f"delete_page_cascade: no page {slug!r} in scope {scope_type}/{scope_id} — "
            "refusing to cascade a deletion that would not delete anything"
        )

    # 2: pages in this scope that reference the slug in their content
    referring_pages = (await session.execute(
        select(WikiPage).where(
            WikiPage.content_md.contains(f"[[{slug}]]")
            | WikiPage.content_md.contains(f"[[{slug}|"),
            _scope_filter(scope_type, scope_id),
        )
    )).scalars().all()

    rewritten: list[str] = []
    for ref_page in referring_pages:
        if ref_page.slug == slug:
            continue
        cleaned = ref_page.content_md or ""
        # Replace [[slug|display]] with just display text
        cleaned = re.sub(
            rf"\[\[{re.escape(slug)}\|([^\]]+)]]",
            r"\1",
            cleaned,
        )
        # Replace [[slug]] with slug text
        cleaned = cleaned.replace(f"[[{slug}]]", slug.split("/")[-1])
        ref_page.content_md = cleaned
        rewritten.append(ref_page.slug)

    # 3: Delete the page
    await session.delete(page)
    await session.flush()

    # 4: Only the slugs whose content changed here get their edges recomputed. Edges owned
    # by pages in other scopes are left alone — their content still justifies them, and
    # blowing them away is exactly the damage this function used to do.
    for from_slug in [slug, *rewritten]:
        await _rebuild_outgoing_links(session, from_slug)

    await session.flush()
    logger.info(
        f"delete_page_cascade({slug} @ {scope_type}/{scope_id}): deleted page "
        f"+ cleaned {len(rewritten)} references"
    )


# ---------------------------------------------------------------------------
# Source removal — used when deleting a source
# ---------------------------------------------------------------------------

async def upsert_source_contribution(
    session: AsyncSession,
    page: WikiPage,
    source_id: uuid.UUID,
    content_md: str,
    summary: str,
    source_title: Optional[str] = None,
    knowledge_type_slug: Optional[str] = None,
) -> None:
    """Persist the exact content owned by one source for one wiki page."""
    stmt = pg_insert(WikiPageContribution).values(
        page_id=page.id, source_id=source_id, content_md=content_md,
        summary=summary or "", source_title=source_title,
        knowledge_type_slug=knowledge_type_slug,
    ).on_conflict_do_update(
        constraint="uq_wpc_page_source",
        set_={
            "content_md": content_md,
            "summary": summary or "",
            "source_title": source_title,
            "knowledge_type_slug": knowledge_type_slug,
            "updated_at": func.now(),
        },
    )
    await session.execute(stmt)
    await session.flush()


async def get_page_contributions(
    session: AsyncSession,
    page_id: uuid.UUID,
    exclude_source_id: Optional[uuid.UUID] = None,
) -> list[WikiPageContribution]:
    stmt = select(WikiPageContribution).where(WikiPageContribution.page_id == page_id)
    if exclude_source_id is not None:
        stmt = stmt.where(WikiPageContribution.source_id != exclude_source_id)
    stmt = stmt.order_by(WikiPageContribution.created_at, WikiPageContribution.id)
    return list((await session.execute(stmt)).scalars().all())


async def rebuild_page_from_contributions(
    session: AsyncSession,
    page: WikiPage,
    contributions: list[WikiPageContribution],
    merge_llm=None,
    change_note: Optional[str] = None,
) -> WikiPage:
    """Rebuild canonical content using only the supplied source contributions."""
    if not contributions:
        raise ValueError("Cannot rebuild a wiki page without contributions")

    content = contributions[0].content_md
    if len(contributions) > 1:
        if merge_llm is not None:
            from app.ai.mrp.merger import merge_page_content
            for contribution in contributions[1:]:
                content = await merge_page_content(
                    merge_llm, content, contribution.content_md, page.slug,
                )
        else:
            from app.ai.mrp.merger import lossless_merge_fallback
            for contribution in contributions[1:]:
                content = lossless_merge_fallback(content, contribution.content_md)

    summaries = list(dict.fromkeys(c.summary.strip() for c in contributions if c.summary.strip()))
    page.content_md = content
    page.summary = " ".join(summaries)
    page.source_ids = [c.source_id for c in contributions]
    page.knowledge_type_slugs = list(dict.fromkeys(
        c.knowledge_type_slug for c in contributions if c.knowledge_type_slug
    ))
    page.provenance_complete = True
    page.version = (page.version or 1) + 1
    await session.flush()
    await refresh_links(session, page.slug, content)
    session.add(WikiPageRevision(
        page_id=page.id, version=page.version, content_md=content,
        change_type="source_rebuild", change_note=change_note,
    ))
    return page


async def detach_source_from_wiki(
    session: AsyncSession,
    source_id: uuid.UUID,
    merge_llm=None,
) -> dict:
    """
    Remove source-owned content and rebuild shared pages from surviving sources.
    Legacy multi-source pages without complete provenance are detached without
    rewriting content, avoiding destructive guesses about historical ownership.
    """
    contribution_pages = select(WikiPageContribution.page_id).where(
        WikiPageContribution.source_id == source_id,
    )
    stmt = select(WikiPage).where(or_(
        WikiPage.source_ids.contains([source_id]),  # type: ignore[arg-type]
        WikiPage.id.in_(contribution_pages),
    ))
    pages = list((await session.execute(stmt)).scalars().all())
    deleted_count = 0
    rebuilt_count = 0
    legacy_count = 0
    rebuilt_page_ids: list[str] = []
    for page in pages:
        remaining = [sid for sid in (page.source_ids or []) if sid != source_id]
        contributions = await get_page_contributions(
            session, page.id, exclude_source_id=source_id,
        )
        await session.execute(delete(WikiPageContribution).where(
            WikiPageContribution.page_id == page.id,
            WikiPageContribution.source_id == source_id,
        ))
        if page.provenance_complete:
            if not contributions:
                await session.delete(page)
                deleted_count += 1
            else:
                await rebuild_page_from_contributions(
                    session, page, contributions, merge_llm=merge_llm,
                    change_note=f"Removed source {source_id}",
                )
                rebuilt_count += 1
                rebuilt_page_ids.append(str(page.id))
        else:
            page.source_ids = remaining
            legacy_count += 1
    await session.flush()
    logger.info(
        f"detach_source_from_wiki({source_id}): deleted={deleted_count}, "
        f"rebuilt={rebuilt_count}, legacy={legacy_count}"
    )
    return {
        "pages_deleted": deleted_count,
        "pages_rebuilt": rebuilt_count,
        "legacy_pages_detached": legacy_count,
        "rebuilt_page_ids": rebuilt_page_ids,
    }


# ---------------------------------------------------------------------------
# Draft workflow
# ---------------------------------------------------------------------------

async def create_draft(
    session: AsyncSession,
    page_id: uuid.UUID,
    author_id: uuid.UUID,
    content_md: str,
    note: Optional[str] = None,
    source: str = "web_ui",
    source_metadata: Optional[dict] = None,
) -> WikiPageDraft:
    """Create a pending draft for editor review."""
    draft = WikiPageDraft(
        page_id=page_id,
        author_id=author_id,
        content_md=content_md,
        note=note,
        status="pending",
        source=source,
        source_metadata=source_metadata,
    )
    session.add(draft)
    await session.flush()
    return draft


async def approve_draft(
    session: AsyncSession,
    draft: WikiPageDraft,
    reviewer_id: uuid.UUID,
    reviewer_note: Optional[str] = None,
    edited_content_md: Optional[str] = None,
) -> WikiPage:
    """
    Approve a pending draft. Writes the final content to wiki_pages.content_md,
    creates a revision, and marks the draft approved.
    If edited_content_md is provided, that is used instead of the original draft content.
    """
    page = await session.get(WikiPage, draft.page_id)
    if page is None:
        raise ValueError(f"Wiki page {draft.page_id} not found")

    # Lock before reading `page.version`: the increment below is a read-modify-write, and the
    # WikiPageRevision written from it is stamped with that number. Two concurrent writers
    # therefore produced two revisions claiming the SAME version, which makes the history
    # ambiguous and `rollback_to_revision` non-deterministic about which snapshot it restores.
    # Keyed on the page id, so unrelated pages never contend.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(str(page.id)))))

    final_content = edited_content_md.strip() if edited_content_md else draft.content_md
    page.content_md = final_content
    page.version = (page.version or 1) + 1
    await session.flush()
    await refresh_links(session, page.slug, final_content)

    session.add(WikiPageRevision(
        page_id=page.id,
        version=page.version,
        content_md=final_content,
        change_type="draft_approved",
        draft_id=draft.id,
        changed_by_id=reviewer_id,
        change_note=reviewer_note,
    ))

    draft.status = "approved"
    draft.reviewed_by_id = reviewer_id
    draft.reviewed_at = datetime.now(timezone.utc)
    draft.reviewer_note = reviewer_note
    await session.flush()
    return page


async def reject_draft(
    session: AsyncSession,
    draft: WikiPageDraft,
    reviewer_id: uuid.UUID,
    reviewer_note: str,
) -> WikiPageDraft:
    """Reject a pending draft with a required reason."""
    draft.status = "rejected"
    draft.reviewed_by_id = reviewer_id
    draft.reviewed_at = datetime.now(timezone.utc)
    draft.reviewer_note = reviewer_note
    await session.flush()
    return draft


async def direct_edit_page(
    session: AsyncSession,
    page: WikiPage,
    editor_id: uuid.UUID,
    content_md: str,
    change_note: Optional[str] = None,
) -> WikiPage:
    """
    Sync write by an editor/admin — no review step.
    Creates a revision immediately.
    """
    # Lock before reading `page.version`: the increment below is a read-modify-write, and the
    # WikiPageRevision written from it is stamped with that number. Two concurrent writers
    # therefore produced two revisions claiming the SAME version, which makes the history
    # ambiguous and `rollback_to_revision` non-deterministic about which snapshot it restores.
    # Keyed on the page id, so unrelated pages never contend.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(str(page.id)))))
    page.content_md = content_md
    page.version = (page.version or 1) + 1
    await session.flush()
    await refresh_links(session, page.slug, content_md)

    session.add(WikiPageRevision(
        page_id=page.id,
        version=page.version,
        content_md=content_md,
        change_type="editor_edit",
        changed_by_id=editor_id,
        change_note=change_note,
    ))
    await session.flush()
    return page


async def rollback_to_revision(
    session: AsyncSession,
    page: WikiPage,
    target_version: int,
    actor_id: uuid.UUID,
) -> WikiPage:
    """
    Restore a page to a previous revision snapshot.
    Creates a new revision recording the rollback.
    """
    # Lock before reading `page.version`: the increment below is a read-modify-write, and the
    # WikiPageRevision written from it is stamped with that number. Two concurrent writers
    # therefore produced two revisions claiming the SAME version, which makes the history
    # ambiguous and `rollback_to_revision` non-deterministic about which snapshot it restores.
    # Keyed on the page id, so unrelated pages never contend.
    await session.execute(select(func.pg_advisory_xact_lock(func.hashtext(str(page.id)))))
    revision = (await session.execute(
        select(WikiPageRevision).where(
            WikiPageRevision.page_id == page.id,
            WikiPageRevision.version == target_version,
        )
    )).scalar_one_or_none()
    if revision is None:
        raise ValueError(f"Revision v{target_version} not found for page {page.slug}")

    page.content_md = revision.content_md
    page.version = (page.version or 1) + 1
    await session.flush()
    await refresh_links(session, page.slug, revision.content_md)

    session.add(WikiPageRevision(
        page_id=page.id,
        version=page.version,
        content_md=revision.content_md,
        change_type="rollback",
        changed_by_id=actor_id,
        change_note=f"rollback to v{target_version}",
    ))
    await session.flush()
    return page
