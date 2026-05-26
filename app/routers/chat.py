"""
Chat REST router — RAG-based knowledge assistant conversations.

Endpoints:
  GET    /api/chat/conversations          - list user's conversations
  POST   /api/chat/conversations          - create new conversation
  DELETE /api/chat/conversations/{id}     - delete conversation
  PATCH  /api/chat/conversations/{id}     - rename conversation
  GET    /api/chat/conversations/{id}/messages  - list messages
  POST   /api/chat/conversations/{id}/messages  - send message (RAG + LLM)
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
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
