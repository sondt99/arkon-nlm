"""
Notes router — CRUD.
"""

import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.database.models import Employee, Note
from app.database.repository import Repository
from app.services.auth_service import get_current_user, require_permission

router = APIRouter()


class NoteCreate(BaseModel):
    title: Optional[str] = None
    content: str
    note_type: Optional[str] = "human"


class NoteResponse(BaseModel):
    id: uuid.UUID
    title: Optional[str]
    content: Optional[str]
    note_type: Optional[str]
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


@router.get("/notes", response_model=list[NoteResponse])
async def list_notes(
    # This endpoint had no limit of any kind: `get_all` selects every row, and Note.content
    # is unbounded Text, so the response grew without limit as the table did. Newest-first
    # with an explicit window, matching how the capped siblings paginate.
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    _user: Employee = Depends(get_current_user),
):
    notes = (await db.execute(
        select(Note).order_by(Note.created_at.desc()).offset(offset).limit(limit)
    )).scalars().all()
    return [
        NoteResponse(
            id=n.id, title=n.title, content=n.content,
            note_type=n.note_type,
            created_at=n.created_at.isoformat(),
            updated_at=n.updated_at.isoformat(),
        )
        for n in notes
    ]


@router.post("/notes", response_model=NoteResponse)
async def create_note(req: NoteCreate, db: AsyncSession = Depends(get_db), _user: Employee = require_permission("wiki:write")):
    repo = Repository(db)
    note = Note(**req.model_dump())
    note = await repo.create(note)
    return NoteResponse(
        id=note.id, title=note.title, content=note.content,
        note_type=note.note_type,
        created_at=note.created_at.isoformat(),
        updated_at=note.updated_at.isoformat(),
    )


@router.delete("/notes/{note_id}")
async def delete_note(note_id: uuid.UUID, db: AsyncSession = Depends(get_db), _user: Employee = require_permission("wiki:delete")):
    repo = Repository(db)
    deleted = await repo.delete_by_id(Note, note_id)
    if not deleted:
        raise HTTPException(404, "Note not found")
    return {"deleted": True}
