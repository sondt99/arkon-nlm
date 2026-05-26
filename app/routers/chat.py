"""
Chat REST router — RAG-based knowledge assistant conversations.

Endpoints:
  GET    /api/chat/conversations                       - list user's conversations
  POST   /api/chat/conversations                       - create new conversation
  DELETE /api/chat/conversations/{id}                  - delete conversation
  PATCH  /api/chat/conversations/{id}                  - rename conversation
  GET    /api/chat/conversations/{id}/messages         - list messages
  POST   /api/chat/conversations/{id}/messages         - send message (RAG + LLM)
  POST   /api/chat/conversations/{id}/to-wiki          - synthesize conversation → wiki page
"""

import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.registry import ProviderRegistry
from app.database import get_db
from app.database.models import ChatConversation, ChatMessage
from app.services import chat_service
from app.services.auth_service import get_current_user
from app.database.models import Employee

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class ConversationOut(BaseModel):
    id: uuid.UUID
    title: str
    scope_type: str
    scope_id: Optional[uuid.UUID]
    created_at: str
    updated_at: str

    @classmethod
    def from_orm(cls, c: ChatConversation) -> "ConversationOut":
        return cls(
            id=c.id,
            title=c.title,
            scope_type=c.scope_type,
            scope_id=c.scope_id,
            created_at=c.created_at.isoformat(),
            updated_at=c.updated_at.isoformat(),
        )


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str
    content: str
    sources: Optional[list]
    created_at: str

    @classmethod
    def from_orm(cls, m: ChatMessage) -> "MessageOut":
        return cls(
            id=m.id,
            role=m.role,
            content=m.content,
            sources=m.sources,
            created_at=m.created_at.isoformat(),
        )


class CreateConversationRequest(BaseModel):
    title: str = "New conversation"
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None


class RenameConversationRequest(BaseModel):
    title: str


class SendMessageRequest(BaseModel):
    content: str
    persona: str = "victor"


class EditMessageRequest(BaseModel):
    content: str
    persona: str = "victor"


# ---------------------------------------------------------------------------
# Conversation endpoints
# ---------------------------------------------------------------------------

@router.get("/chat/conversations")
async def list_conversations(
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[ConversationOut]:
    stmt = (
        select(ChatConversation)
        .where(ChatConversation.employee_id == current_user.id)
        .order_by(ChatConversation.updated_at.desc())
        .limit(50)
    )
    result = await db.execute(stmt)
    return [ConversationOut.from_orm(c) for c in result.scalars().all()]


@router.post("/chat/conversations", status_code=201)
async def create_conversation(
    body: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ConversationOut:
    conv = ChatConversation(
        employee_id=current_user.id,
        title=body.title,
        scope_type=body.scope_type,
        scope_id=body.scope_id,
    )
    db.add(conv)
    await db.commit()
    await db.refresh(conv)
    return ConversationOut.from_orm(conv)


@router.patch("/chat/conversations/{conversation_id}")
async def rename_conversation(
    conversation_id: uuid.UUID,
    body: RenameConversationRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ConversationOut:
    conv = await _get_owned_conversation(db, conversation_id, current_user.id)
    conv.title = body.title.strip() or "New conversation"
    await db.commit()
    await db.refresh(conv)
    return ConversationOut.from_orm(conv)


@router.delete("/chat/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> None:
    conv = await _get_owned_conversation(db, conversation_id, current_user.id)
    await db.delete(conv)
    await db.commit()


# ---------------------------------------------------------------------------
# Message endpoints
# ---------------------------------------------------------------------------

@router.get("/chat/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> list[MessageOut]:
    await _get_owned_conversation(db, conversation_id, current_user.id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(stmt)
    return [MessageOut.from_orm(m) for m in result.scalars().all()]


@router.post("/chat/conversations/{conversation_id}/messages", status_code=201)
async def send_message(
    conversation_id: uuid.UUID,
    body: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Message content cannot be empty")

    conv = await _get_owned_conversation(db, conversation_id, current_user.id)

    # Save user message
    user_msg = await chat_service.save_message(
        session=db,
        conversation_id=conv.id,
        role="user",
        content=body.content.strip(),
    )

    # Auto-title: use first message content (truncated)
    if conv.title == "New conversation":
        conv.title = body.content.strip()[:80]

    try:
        registry = ProviderRegistry(db)
        answer, sources = await chat_service.generate_reply(
            session=db,
            registry=registry,
            conversation=conv,
            question=body.content.strip(),
            persona=body.persona,
        )
    except Exception as exc:
        # Save error as assistant message so the UI shows feedback
        answer = f"Sorry, I encountered an error: {exc}"
        sources = []

    # Save assistant message
    assistant_msg = await chat_service.save_message(
        session=db,
        conversation_id=conv.id,
        role="assistant",
        content=answer,
        sources=sources or None,
    )

    await db.commit()
    await db.refresh(user_msg)
    await db.refresh(assistant_msg)

    return {
        "user_message": MessageOut.from_orm(user_msg),
        "assistant_message": MessageOut.from_orm(assistant_msg),
    }


# ---------------------------------------------------------------------------
# Edit a user message → regenerate from that point
# ---------------------------------------------------------------------------

@router.patch("/chat/conversations/{conversation_id}/messages/{message_id}/edit", status_code=200)
async def edit_message(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: EditMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> dict:
    """
    Edit a user message and regenerate the assistant reply.
    All messages after the edited message are deleted, then the LLM
    generates a fresh response with the corrected context.
    """
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Message content cannot be empty")

    conv = await _get_owned_conversation(db, conversation_id, current_user.id)

    # Load and validate the target message
    msg_result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.id == message_id,
            ChatMessage.conversation_id == conversation_id,
        )
    )
    msg = msg_result.scalar_one_or_none()
    if not msg or msg.role != "user":
        raise HTTPException(status_code=404, detail="User message not found")

    # Update the message content
    msg.content = body.content.strip()

    # Delete all messages that came after this one
    await db.execute(
        delete(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.created_at > msg.created_at,
        )
    )
    await db.flush()

    # Regenerate assistant reply with updated context
    try:
        registry = ProviderRegistry(db)
        answer, sources = await chat_service.generate_reply(
            session=db,
            registry=registry,
            conversation=conv,
            question=body.content.strip(),
            persona=body.persona,
        )
    except Exception as exc:
        answer = f"Sorry, I encountered an error: {exc}"
        sources = []

    assistant_msg = await chat_service.save_message(
        session=db,
        conversation_id=conv.id,
        role="assistant",
        content=answer,
        sources=sources or None,
    )

    await db.commit()
    await db.refresh(msg)
    await db.refresh(assistant_msg)

    return {
        "user_message": MessageOut.from_orm(msg),
        "assistant_message": MessageOut.from_orm(assistant_msg),
    }


# ---------------------------------------------------------------------------
# Convert conversation → wiki page
# ---------------------------------------------------------------------------

class ToWikiRequest(BaseModel):
    title: str
    page_type: str = "synthesis"
    scope_type: str = "global"
    scope_id: Optional[uuid.UUID] = None


class ToWikiResult(BaseModel):
    slug: str
    title: str


@router.post("/chat/conversations/{conversation_id}/to-wiki", status_code=201)
async def conversation_to_wiki(
    conversation_id: uuid.UUID,
    body: ToWikiRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = Depends(get_current_user),
) -> ToWikiResult:
    """
    Synthesize a chat conversation into a wiki page using the chatbot LLM.
    Creates a new WikiPage and stores its embedding for RAG search.
    """
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Title is required")

    conv = await _get_owned_conversation(db, conversation_id, current_user.id)

    # Load all messages
    msg_stmt = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
    )
    result = await db.execute(msg_stmt)
    messages = result.scalars().all()

    if not messages:
        raise HTTPException(status_code=422, detail="Conversation has no messages to save")

    # Build Q&A text
    qa_lines = []
    for m in messages:
        label = "Q" if m.role == "user" else "A"
        qa_lines.append(f"**{label}:** {m.content}")
    qa_text = "\n\n".join(qa_lines)

    # LLM synthesis
    registry = ProviderRegistry(db)
    try:
        llm = await registry.get_chatbot_llm()
        synthesis_prompt = (
            f"Given the following Q&A conversation, write a concise, encyclopedic wiki page "
            f"about the topic '{body.title.strip()}'.\n\n"
            "Requirements:\n"
            "- Write in prose, NOT Q&A format\n"
            "- Start with a 1-2 sentence introduction paragraph\n"
            "- Use ## headings to organize key concepts and insights\n"
            "- Extract facts, definitions, and actionable insights from the conversation\n"
            "- End with a ## Summary section with bullet points of key takeaways\n"
            "- Output ONLY the markdown body (no YAML front matter)\n\n"
            f"Conversation:\n---\n{qa_text}\n---"
        )
        system = "You are a technical wiki editor. Write structured, encyclopedic content."
        content_md = await llm.generate(synthesis_prompt, system=system, temperature=0.3)
    except Exception as exc:
        # Fallback: format as structured Q&A if LLM fails
        content_md = f"## Overview\n\nThis page was created from a chat conversation.\n\n## Q&A\n\n{qa_text}"
        raise HTTPException(
            status_code=503,
            detail=f"LLM synthesis failed: {exc}. Configure a chatbot provider in Settings.",
        )

    # Generate summary (first non-empty line of content)
    summary_line = next(
        (l.lstrip("#").strip() for l in content_md.splitlines() if l.strip() and not l.startswith("#")),
        body.title.strip(),
    )
    summary = summary_line[:300]

    # Generate unique slug
    base_slug = re.sub(r"[^a-z0-9]+", "-", body.title.strip().lower()).strip("-")[:80]
    slug = f"{base_slug}-{uuid.uuid4().hex[:6]}"

    # Create WikiPage
    from app.services import wiki_service
    try:
        page = await wiki_service.apply_create(
            session=db,
            slug=slug,
            title=body.title.strip(),
            page_type=body.page_type if body.page_type in wiki_service.PAGE_TYPES else "synthesis",
            content_md=content_md,
            summary=summary,
            knowledge_type_slugs=[],
            source_ids=[],
            scope_type=body.scope_type,
            scope_id=body.scope_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to create wiki page: {exc}")

    # Store embedding so the page appears in RAG search
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
            emb_provider = await registry.get_embedding(task="document")
            text = embedding_input_text(page.title, summary, content_md)
            vector = await emb_provider.embed(text)
            content_hash = compute_content_hash(page.title, summary, content_md)
            await upsert_page_embedding(db, page.id, spec, vector, content_hash)
    except Exception:
        pass  # Embedding failure is non-fatal; page is still created

    await db.commit()
    return ToWikiResult(slug=slug, title=body.title.strip())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_owned_conversation(
    db: AsyncSession,
    conversation_id: uuid.UUID,
    employee_id: uuid.UUID,
) -> ChatConversation:
    stmt = select(ChatConversation).where(
        ChatConversation.id == conversation_id,
        ChatConversation.employee_id == employee_id,
    )
    result = await db.execute(stmt)
    conv = result.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv
