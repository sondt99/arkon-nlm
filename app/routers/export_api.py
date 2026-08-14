"""
Export API — REST access to Arkon's knowledge base for external tools.

Authenticates with the same bearer token used for MCP (Employee.mcp_token),
so any tool that isn't an MCP client (n8n, Zapier, custom scripts, other AI
platforms) can still chat with Victor/Ashley or search the wiki directly.

Endpoints:
  POST /api/export/v1/chat    - chat with a persona (Victor/Ashley), KB-grounded
  GET  /api/export/v1/search  - direct semantic search over wiki pages
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.database import get_db
from app.database.models import ChatConversation, Employee
from app.services import chat_service
from app.services.mcp_auth_service import (
    ResolvedIdentity,
    get_identity_from_export_token,
)
from app.services.permission_engine import can_access_workspace
from app.services.rate_limiter import check_token_rate_limit

router = APIRouter()

_VALID_PERSONAS = {"victor", "ashley"}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ExportChatRequest(BaseModel):
    persona: str = "victor"
    question: str
    conversation_id: Optional[uuid.UUID] = None
    workspace_id: Optional[uuid.UUID] = None


class ExportChatSource(BaseModel):
    slug: str
    title: str


class ExportChatResponse(BaseModel):
    answer: str
    sources: list[ExportChatSource]
    conversation_id: uuid.UUID


class ExportSearchResult(BaseModel):
    slug: str
    title: str
    summary: Optional[str]
    page_type: str
    knowledge_type_slugs: list[str]
    score: float


class ExportSearchResponse(BaseModel):
    query: str
    results: list[ExportSearchResult]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _resolve_scope(
    db: AsyncSession,
    identity: ResolvedIdentity,
    workspace_id: Optional[uuid.UUID],
) -> tuple[str, Optional[uuid.UUID]]:
    """Turn an optional workspace_id into (scope_type, scope_id), enforcing membership."""
    if workspace_id is None:
        return "global", None

    employee = await db.get(Employee, identity.employee_id)
    if not employee or not await can_access_workspace(db, employee, workspace_id):
        raise HTTPException(status_code=403, detail="You do not have access to this workspace")
    return "project", workspace_id


async def _require_enabled(db: AsyncSession) -> None:
    """Reject with 503 if an admin has turned the Export API off."""
    from app.services.config_service import ConfigService

    flag = await ConfigService(db).get("export_api_enabled")
    enabled = (flag or "true").strip().lower() not in ("false", "0", "off")
    if not enabled:
        raise HTTPException(status_code=503, detail="Export API is disabled by an administrator")


async def _generation_overrides(db: AsyncSession) -> dict:
    """Read admin-configured temperature/max_tokens/top_p for Export API calls."""
    from app.services.config_service import ConfigService

    svc = ConfigService(db)
    temperature_raw = await svc.get("export_api_temperature")
    max_tokens_raw = await svc.get("export_api_max_tokens")
    top_p_raw = await svc.get("export_api_top_p")

    overrides: dict = {}
    if temperature_raw:
        try:
            overrides["temperature"] = float(temperature_raw)
        except ValueError:
            pass
    if max_tokens_raw:
        try:
            overrides["max_tokens"] = int(max_tokens_raw)
        except ValueError:
            pass
    if top_p_raw:
        try:
            overrides["top_p"] = float(top_p_raw)
        except ValueError:
            pass
    return overrides


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

@router.post("/export/v1/chat", response_model=ExportChatResponse)
async def export_chat(
    body: ExportChatRequest,
    db: AsyncSession = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_identity_from_export_token),
) -> ExportChatResponse:
    await _require_enabled(db)
    await check_token_rate_limit(identity.employee_id, "export_chat", max_requests=30, window_seconds=60)
    if body.persona not in _VALID_PERSONAS:
        raise HTTPException(status_code=422, detail=f"persona must be one of {sorted(_VALID_PERSONAS)}")
    if not body.question.strip():
        raise HTTPException(status_code=422, detail="question cannot be empty")

    if body.conversation_id is not None:
        stmt = select(ChatConversation).where(
            ChatConversation.id == body.conversation_id,
            ChatConversation.employee_id == identity.employee_id,
        )
        result = await db.execute(stmt)
        conv = result.scalar_one_or_none()
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
    else:
        scope_type, scope_id = await _resolve_scope(db, identity, body.workspace_id)
        conv = ChatConversation(
            employee_id=identity.employee_id,
            title=body.question.strip()[:80],
            scope_type=scope_type,
            scope_id=scope_id,
        )
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

    user_msg = await chat_service.save_message(
        session=db,
        conversation_id=conv.id,
        role="user",
        content=body.question.strip(),
    )
    # Persist before the LLM call so the message survives even if generation fails.
    await db.commit()
    await db.refresh(user_msg)

    try:
        registry = ProviderRegistry(db)
        answer, sources = await chat_service.generate_reply(
            session=db,
            registry=registry,
            conversation=conv,
            question=body.question.strip(),
            persona=body.persona,
            exclude_message_id=user_msg.id,
            **await _generation_overrides(db),
        )
    except Exception as exc:
        logger.exception("Export API chat generation failed for conversation={}", conv.id)
        raise HTTPException(status_code=502, detail="Chat generation failed — the configured provider returned an error") from exc

    await chat_service.save_message(
        session=db,
        conversation_id=conv.id,
        role="assistant",
        content=answer,
        sources=sources or None,
    )
    await db.commit()

    return ExportChatResponse(
        answer=answer,
        sources=[ExportChatSource(**s) for s in sources],
        conversation_id=conv.id,
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@router.get("/export/v1/search", response_model=ExportSearchResponse)
async def export_search(
    q: str,
    top_k: int = 10,
    workspace_id: Optional[uuid.UUID] = None,
    db: AsyncSession = Depends(get_db),
    identity: ResolvedIdentity = Depends(get_identity_from_export_token),
) -> ExportSearchResponse:
    await _require_enabled(db)
    await check_token_rate_limit(identity.employee_id, "export_search", max_requests=60, window_seconds=60)
    if not q.strip():
        raise HTTPException(status_code=422, detail="q cannot be empty")
    top_k = min(max(1, top_k), 50)

    scope_type, scope_id = await _resolve_scope(db, identity, workspace_id)

    from app.services import wiki_service

    try:
        registry = ProviderRegistry(db)
        embedding_provider = await registry.get_embedding(task="search_query")
        query_embedding = await embedding_provider.embed(q)

        hits = await wiki_service.search_pages_semantic(
            db,
            query_embedding=query_embedding,
            top_k=top_k,
            allowed_kt_slugs=identity.allowed_knowledge_types,
            scope_type=scope_type,
            scope_id=scope_id,
        )
    except Exception as exc:
        logger.exception("Export API search failed for query={!r}", q)
        raise HTTPException(status_code=502, detail="Search failed — the configured provider returned an error") from exc

    return ExportSearchResponse(
        query=q,
        results=[
            ExportSearchResult(
                slug=page.slug,
                title=page.title,
                summary=page.summary,
                page_type=page.page_type,
                knowledge_type_slugs=page.knowledge_type_slugs or [],
                score=round(score, 4),
            )
            for page, score in hits
        ],
    )
