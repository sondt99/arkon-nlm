"""
Helpers for writing/reading wiki page embeddings across the
per-dimension `wiki_page_embeddings_<dim>` tables.

Use these instead of touching the embedding tables directly so callers don't
have to care which dimension corresponds to the active model.
"""

import hashlib
import uuid
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embedding_catalog import EmbeddingModelSpec, get_spec
from app.database.models import (
    EmbeddingJob,
    get_embedding_model_for_dim,
)


def compute_content_hash(title: str, summary: str, content_md: str) -> str:
    """Stable hash of the text we feed into the embedding model."""
    blob = f"{title}\n\n{summary or ''}\n\n{content_md or ''}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def embedding_input_text(title: str, summary: str, content_md: str) -> str:
    """The exact text that gets embedded — kept in one place so hash matches."""
    return f"{title}\n\n{summary or ''}\n\n{content_md or ''}"[:8000]


async def upsert_page_embedding(
    session: AsyncSession,
    page_id: uuid.UUID,
    spec: EmbeddingModelSpec,
    vector: list[float],
    content_hash: str,
) -> None:
    """Upsert one (page, model_spec_id) row into wiki_page_embeddings_<dim>."""
    Model = get_embedding_model_for_dim(spec.dimension)
    stmt = pg_insert(Model).values(
        page_id=page_id,
        model_spec_id=spec.id,
        content_hash=content_hash,
        embedding=vector,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["page_id", "model_spec_id"],
        set_={
            "embedding": stmt.excluded.embedding,
            "content_hash": stmt.excluded.content_hash,
            "embedded_at": stmt.excluded.embedded_at,
        },
    )
    await session.execute(stmt)


async def get_existing_hash(
    session: AsyncSession, page_id: uuid.UUID, spec_id: str, dimension: int
) -> Optional[str]:
    Model = get_embedding_model_for_dim(dimension)
    row = (
        await session.execute(
            select(Model.content_hash).where(
                Model.page_id == page_id, Model.model_spec_id == spec_id
            )
        )
    ).scalar_one_or_none()
    return row


async def get_existing_hashes(
    session: AsyncSession,
    page_ids: list[uuid.UUID],
    spec_id: str,
    dimension: int,
) -> dict[uuid.UUID, str]:
    """Batched twin of `get_existing_hash`: {page_id: content_hash} for one spec.

    Lets a bulk loop answer "is this page already embedded with this model, at this exact
    content?" for a whole batch in one round-trip instead of one per page. The re-embed
    job uses it to resume: arq re-delivers the same job id up to max_tries times, and
    without a resume check every retry re-paid for every page already done.
    """
    if not page_ids:
        return {}
    Model = get_embedding_model_for_dim(dimension)
    rows = (
        await session.execute(
            select(Model.page_id, Model.content_hash).where(
                Model.model_spec_id == spec_id,
                Model.page_id.in_(page_ids),
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def cleanup_stale_embeddings(
    session: AsyncSession, keep_spec_id: str
) -> int:
    """
    Delete rows in every wiki_page_embeddings_<dim> table whose model_spec_id
    is NOT `keep_spec_id`. Returns total deleted rows.

    Called after an atomic flip so the inactive model's vectors don't waste
    disk + index memory.
    """
    from app.database.models import (
        WikiPageEmbedding768,
        WikiPageEmbedding1024,
        WikiPageEmbedding1536,
        WikiPageEmbedding3072,
    )
    total = 0
    for Model in (
        WikiPageEmbedding768,
        WikiPageEmbedding1024,
        WikiPageEmbedding1536,
        WikiPageEmbedding3072,
    ):
        result = await session.execute(
            delete(Model).where(Model.model_spec_id != keep_spec_id)
        )
        total += result.rowcount or 0  # type: ignore[union-attr]
    return total


def get_spec_for_job(job: EmbeddingJob) -> EmbeddingModelSpec:
    return get_spec(job.model_spec_id)
