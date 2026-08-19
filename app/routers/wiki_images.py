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

# Raster types only. SVG is deliberately absent: it is an XML document that can carry
# script, so serving it inline from the app origin is the exact hazard this allowlist
# exists to prevent. An SVG stored by the extractor is served as a PNG-typed download
# attempt rather than rendered.
_SAFE_IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
}


def _safe_image_content_type(stored: "str | None") -> str:
    """Map a stored content type onto the allowlist, defaulting to a harmless one."""
    candidate = (stored or "").split(";")[0].strip().lower()
    if candidate in _SAFE_IMAGE_TYPES:
        return candidate
    return "application/octet-stream"



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
        # A wiki page resolves up to 100 images, so on the loop one page view became a
        # burst of blocking fetches that stalled every other request in the process.
        data = await storage_service.download_file_async(row.minio_key)
    except Exception as e:
        logger.warning(f"Failed to fetch image {image_id} from MinIO: {e}")
        raise HTTPException(status_code=502, detail="Could not retrieve image from storage")

    # Serve ONLY an allowlisted raster type, never the stored one.
    #
    # row.content_type comes from image_service, which trusts the content_type declared in
    # an uploader-authored DOCX relationship part (and maps svg -> image/svg+xml). Echoing
    # it meant a crafted document could get arbitrary bytes served as text/html or SVG from
    # this app's own origin — and MinIO is proxied on that same origin, so a same-origin CSP
    # would not have stopped it either. nosniff does not help when the type is *declared*
    # rather than sniffed.
    #
    # No working exploit existed, but every guard rail was incidental: the frontend happens
    # to fetch these into a blob and render them only in <img>, and the one anchor happens
    # to carry `download`. Removing that attribute, adding a "copy image link" affordance, or
    # any future use of the ?token= query parameter would have converted it into stored XSS
    # on an origin where the JWT lives in localStorage.
    content_type = _safe_image_content_type(row.content_type)

    def _stream():
        yield data

    return StreamingResponse(
        _stream(),
        media_type=content_type,
        headers={
            "Cache-Control": "private, max-age=3600",
            # inline is what the UI needs, but stating it explicitly stops a browser from
            # inferring anything else from the (now fixed) type.
            "Content-Disposition": "inline",
            "X-Content-Type-Options": "nosniff",
        },
    )
