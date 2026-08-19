"""Bounded reads for multipart uploads.

Every upload handler called `await file.read()` with no byte cap, and no body-size
middleware was registered. nginx permits 500 MB on /api/, and the API is a single uvicorn
process capped at 2 GB — and `storage_service.upload_file` then wraps the bytes in
`io.BytesIO(data)`, roughly doubling peak residency. Five concurrent 500 MB uploads
therefore held ~2.5 GB of bytes plus ~2.5 GB of copies and OOM-killed the container,
dropping every in-flight request for every user.

`read_upload_bounded` reads in chunks against a running total and aborts as soon as the
limit is passed, so an oversized body costs one chunk of memory rather than all of it.
"""

from typing import Optional

from fastapi import HTTPException, UploadFile

from app.config import settings

_CHUNK = 1024 * 1024  # 1 MiB


def _limit_bytes(limit_mb: Optional[int]) -> int:
    return (limit_mb if limit_mb is not None else settings.max_upload_mb) * 1024 * 1024


async def read_upload_bounded(
    file: UploadFile,
    *,
    limit_mb: Optional[int] = None,
    what: str = "file",
) -> bytes:
    """Read an UploadFile, refusing anything over the limit.

    Checks Content-Length first when the client supplies it, so an oversized upload is
    rejected before a single chunk is buffered. That header is advisory — a client can lie
    or omit it — so the streaming total below is the actual enforcement.
    """
    limit = _limit_bytes(limit_mb)

    declared = getattr(file, "size", None)
    if isinstance(declared, int) and declared > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{what} is {declared // (1024 * 1024)} MB, over the "
                f"{limit // (1024 * 1024)} MB limit."
            ),
        )

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            # Stop reading immediately. Draining the rest would defeat the point of the
            # cap, since the client controls how much more it sends.
            raise HTTPException(
                status_code=413,
                detail=f"{what} exceeds the {limit // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)

    return b"".join(chunks)


def content_type_from_extension(filename: str, fallback: str = "application/octet-stream") -> str:
    """Derive a content type from the filename extension.

    Two upload paths persisted the client-supplied `file.content_type` verbatim, which let
    a caller store `text/html` against an uploaded file. Deriving it from an extension
    allowlist means the stored type is ours, not the uploader's — the same approach
    zip_service already takes for archive entries.
    """
    import os

    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    return _EXT_CONTENT_TYPES.get(ext, fallback)


_EXT_CONTENT_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
    "json": "application/json",
    "yaml": "text/yaml",
    "yml": "text/yaml",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "zip": "application/zip",
}
