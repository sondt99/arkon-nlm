"""Complete source deletion — storage, wiki detachment, embeddings, audit.

Extracted because two routers deleted sources by two different paths.
`DELETE /api/sources/{id}` did the full cleanup, while
`DELETE /api/projects/{id}/sources/{sid}` did a bare `db.delete(source)` — orphaning the
MinIO objects forever and leaving embeddings in place, so deleted material kept surfacing
in RAG answers and semantic search indefinitely.

One implementation, called from both.
"""

import uuid
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Employee, Source, WikiPage
from app.services.audit_service import log_audit


async def delete_source_completely(
    db: AsyncSession,
    source: Source,
    actor: Optional[Employee] = None,
) -> dict:
    """Remove a source and everything derived from it.

    Order matters: storage first (best-effort), then wiki detachment while the row still
    exists, then the row itself. Returns the knowledge-impact report so callers can show
    what the deletion changed.
    """
    source_id = source.id

    # Best-effort: a storage outage should not block the DB deletion, but it must be
    # logged rather than swallowed silently.
    try:
        from app.services.storage_service import storage_service

        # delete_prefix issues one blocking remove_object per object, so a source with
        # many extracted images held the loop for the whole sweep.
        await storage_service.delete_prefix_async(f"sources/{source_id}/")
    except Exception as e:
        logger.warning(f"Failed to clean MinIO files for source {source_id}: {e}")

    from app.services import wiki_service

    merge_llm = None
    registry = None
    try:
        from app.ai.registry import ProviderRegistry

        registry = ProviderRegistry(db)
        merge_llm = await registry.get_llm()
    except Exception as exc:
        logger.warning(f"Source-aware rebuild will use lossless fallback: {exc}")

    impact = await wiki_service.detach_source_from_wiki(db, source_id, merge_llm=merge_llm)

    # Pages that survived with rewritten content need their vectors refreshed, or search
    # keeps ranking them by an embedding of text that mentioned the deleted source.
    if registry and impact.get("rebuilt_page_ids"):
        try:
            from app.ai.embedding_catalog import get_spec
            from app.services.embedding_storage import (
                compute_content_hash,
                embedding_input_text,
                upsert_page_embedding,
            )

            spec_id = await registry.get_active_embedding_spec_id()
            if spec_id:
                spec = get_spec(spec_id)
                provider = await registry.get_embedding(task="document", spec_id=spec_id)
                for page_id in impact["rebuilt_page_ids"]:
                    page = await db.get(WikiPage, uuid.UUID(page_id))
                    if page:
                        text = embedding_input_text(page.title, page.summary, page.content_md)
                        vector = await provider.embed(text)
                        content_hash = compute_content_hash(
                            page.title, page.summary, page.content_md
                        )
                        await upsert_page_embedding(db, page.id, spec, vector, content_hash)
        except Exception as exc:
            logger.warning(f"Could not refresh rebuilt page embeddings: {exc}")

    await wiki_service.regenerate_index(
        db,
        scope_type=source.scope_type or "global",
        scope_id=source.scope_id,
    )

    if actor is not None:
        await log_audit(db, actor, "delete", "source", str(source.id), reason=source.title)

    await db.delete(source)
    return impact
