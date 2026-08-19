"""
Knowledge Types router — admin-defined document categories.

Admin creates types like "SOP", "Product Spec", "HR Policy", etc.
Each type has a slug, display name, icon, and color for the UI.

Permission model:
  The taxonomy is a single org-wide table with no department column, so every write here
  is an org-wide write. The mutating endpoints therefore require the `:all` variant of the
  document permissions (`doc:create:all` / `doc:edit:all` / `doc:delete:all`) rather than
  the bare `doc:create` form, which `require_permission` also satisfies from `:own_dept`.
  Under the bare form a Contributor could rename an org-wide type and a Department Admin
  could DELETE one — and `Source.knowledge_type_id` is `ondelete="SET NULL"`, so that
  silently de-categorised every other department's sources with no way back.
"""

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Employee, KnowledgeType
from app.services.auth_service import get_current_user, require_permission

router = APIRouter()


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------

class KnowledgeTypeCreate(BaseModel):
    name: str
    slug: Optional[str] = None
    color: str = "#6366f1"
    description: Optional[str] = None
    extraction_hints: Optional[str] = None

    @field_validator("slug", mode="before")
    @classmethod
    def generate_slug(cls, v, info):
        if v:
            return re.sub(r"[^a-z0-9-]", "", v.lower().replace(" ", "-"))
        # Will be generated from name in the endpoint
        return v


class KnowledgeTypeOut(BaseModel):
    id: str
    slug: str
    name: str
    color: str
    description: Optional[str]
    extraction_hints: Optional[str] = None
    sort_order: int
    source_count: int = 0

    class Config:
        from_attributes = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _assert_slug_free(
    db: AsyncSession, slug: str, *, exclude_id: Optional[uuid.UUID] = None
) -> None:
    """Reject a slug that is already taken, before the INSERT/UPDATE reaches Postgres.

    create_knowledge_type had this pre-check; update_knowledge_type did not, so renaming a
    type onto an existing slug surfaced as an uncaught IntegrityError from the unique index
    — a 500 for what is plainly a client error.
    """
    stmt = select(KnowledgeType.id).where(KnowledgeType.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(KnowledgeType.id != exclude_id)
    if (await db.execute(stmt)).scalar_one_or_none() is not None:
        raise HTTPException(409, f"Knowledge type with slug '{slug}' already exists")


def _to_out(kt: KnowledgeType, source_count: int = 0) -> KnowledgeTypeOut:
    return KnowledgeTypeOut(
        id=str(kt.id),
        slug=kt.slug,
        name=kt.name,
        color=kt.color or "#6366f1",
        description=kt.description,
        extraction_hints=kt.extraction_hints,
        sort_order=kt.sort_order,
        source_count=source_count,
    )


def _resolve_hints(explicit: Optional[str], *candidates: str) -> Optional[str]:
    """Fall back to the seeded extraction hints when the request supplies none."""
    if explicit:
        return explicit
    from app.scripts.seed_security_kt_hints import _match_hints

    for candidate in candidates:
        hints = _match_hints(candidate)
        if hints:
            return hints
    return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

@router.get("/knowledge-types", response_model=list[KnowledgeTypeOut])
async def list_knowledge_types(
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    """List all knowledge types, ordered by sort_order."""
    from sqlalchemy import func

    from app.database.models import Source

    stmt = select(KnowledgeType).order_by(KnowledgeType.sort_order, KnowledgeType.name)
    result = await db.execute(stmt)
    types = result.scalars().all()

    # Count sources per type
    count_stmt = (
        select(Source.knowledge_type_id, func.count(Source.id))
        .group_by(Source.knowledge_type_id)
    )
    count_result = await db.execute(count_stmt)
    counts = {str(row[0]): row[1] for row in count_result.all() if row[0]}

    return [_to_out(t, counts.get(str(t.id), 0)) for t in types]


@router.post("/knowledge-types", status_code=201, response_model=KnowledgeTypeOut)
async def create_knowledge_type(
    body: KnowledgeTypeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:create:all"),
):
    """Create a new knowledge type."""
    # Generate slug from name if not provided
    slug = body.slug or re.sub(r"[^a-z0-9-]", "", body.name.lower().replace(" ", "-"))

    await _assert_slug_free(db, slug)

    # Get next sort_order
    max_order_result = await db.execute(
        select(KnowledgeType.sort_order).order_by(KnowledgeType.sort_order.desc()).limit(1)
    )
    max_order = max_order_result.scalar() or 0

    kt = KnowledgeType(
        slug=slug,
        name=body.name,
        color=body.color,
        description=body.description,
        extraction_hints=_resolve_hints(
            body.extraction_hints, slug, body.name.lower().replace(" ", "-")
        ),
        sort_order=max_order + 1,
    )
    db.add(kt)
    await db.flush()

    return _to_out(kt)


@router.put("/knowledge-types/{kt_id}", response_model=KnowledgeTypeOut)
async def update_knowledge_type(
    kt_id: uuid.UUID,
    body: KnowledgeTypeCreate,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:edit:all"),
):
    """Update a knowledge type."""
    kt = await db.get(KnowledgeType, kt_id)
    if not kt:
        raise HTTPException(404, "Knowledge type not found")

    if body.slug and body.slug != kt.slug:
        await _assert_slug_free(db, body.slug, exclude_id=kt.id)
        kt.slug = body.slug

    kt.name = body.name
    kt.color = body.color
    kt.description = body.description
    kt.extraction_hints = _resolve_hints(
        body.extraction_hints, kt.slug, kt.name.lower().replace(" ", "-")
    )
    await db.flush()

    return _to_out(kt)


@router.delete("/knowledge-types/{kt_id}")
async def delete_knowledge_type(
    kt_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:delete:all"),
):
    """Delete a knowledge type. Sources using it will have their type set to NULL."""
    kt = await db.get(KnowledgeType, kt_id)
    if not kt:
        raise HTTPException(404, "Knowledge type not found")
    slug = kt.slug
    await db.delete(kt)
    return {"deleted": True, "slug": slug}


@router.patch("/knowledge-types/reorder")
async def reorder_knowledge_types(
    order: list[uuid.UUID],
    db: AsyncSession = Depends(get_db),
    _user: Employee = require_permission("doc:edit:all"),
):
    """
    Reorder knowledge types.
    Args:
        order: List of knowledge type IDs in desired order.
    """
    # One SELECT for the whole list, not a db.get per id: the list is client-supplied and
    # unbounded, so this was N round trips chosen by the caller.
    rows = (await db.execute(
        select(KnowledgeType).where(KnowledgeType.id.in_(order))
    )).scalars().all()
    by_id = {kt.id: kt for kt in rows}

    reordered = 0
    for idx, kt_id in enumerate(order):
        kt = by_id.get(kt_id)
        if kt:
            kt.sort_order = idx
            reordered += 1
    await db.flush()
    return {"reordered": reordered}
