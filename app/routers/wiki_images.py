"""Wiki image resolver + proxy.

resolve  POST /api/wiki/images/resolve  → returns API proxy URLs (no MinIO hostname)
proxy    GET  /api/wiki/images/<uuid>   → streams image bytes from MinIO

Using an internal proxy instead of presigned MinIO URLs means the image URL
is always relative (/api/...) and works from any IP or hostname without
re-configuring MINIO_PUBLIC_ENDPOINT.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import Employee, Source, SourceImage
from app.services.auth_service import get_current_user, get_current_user_image
from app.services.permission_engine import can_access_document
from app.services.storage_service import storage_service

router = APIRouter()

MAX_IDS_PER_REQUEST = 100


class ResolveRequest(BaseModel):
    ids: list[uuid.UUID] = Field(default_factory=list)


class ResolveResponse(BaseModel):
    resolved: dict[str, str]  # uuid -> /api/wiki/images/<uuid>
    denied: list[str]


@router.post("/wiki/images/resolve", response_model=ResolveResponse)
async def resolve_wiki_images(
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user),
) -> ResolveResponse:
    if not body.ids:
        return ResolveResponse(resolved={}, denied=[])
    if len(body.ids) > MAX_IDS_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=f"Too many ids (max {MAX_IDS_PER_REQUEST} per request)",
        )

    unique_ids = list({i for i in body.ids})
    rows = (await db.execute(
        select(SourceImage)
        .options(selectinload(SourceImage.source).selectinload(Source.departments))
        .where(SourceImage.id.in_(unique_ids))
    )).scalars().all()

    resolved: dict[str, str] = {}
    denied: list[str] = []
    access_cache: dict[uuid.UUID, bool] = {}

    for img in rows:
        source = img.source
        if source is None:
            continue
        if source.id not in access_cache:
            access_cache[source.id] = await can_access_document(db, user, source, "read")
        if not access_cache[source.id]:
            denied.append(str(img.id))
            continue
        # Return a relative API proxy URL — works from any hostname/IP
        resolved[str(img.id)] = f"/api/wiki/images/{img.id}"

    return ResolveResponse(resolved=resolved, denied=denied)


@router.get("/wiki/images/{image_id}")
async def proxy_wiki_image(
    image_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: Employee = Depends(get_current_user_image),
):
    """Stream an image from MinIO after verifying the user can access it."""
    row = (await db.execute(
        select(SourceImage)
        .options(selectinload(SourceImage.source).selectinload(Source.departments))
        .where(SourceImage.id == image_id)
    )).scalar_one_or_none()

    if row is None:
        raise HTTPException(status_code=404, detail="Image not found")

    if row.source is None or not await can_access_document(db, user, row.source, "read"):
        raise HTTPException(status_code=403, detail="Access denied")

    try:
        data = storage_service.download_file(row.minio_key)
    except Exception as e:
        logger.warning(f"Failed to fetch image {image_id} from MinIO: {e}")
        raise HTTPException(status_code=502, detail="Could not retrieve image from storage")

    content_type = row.content_type or "image/jpeg"

    def _stream():
        yield data

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
