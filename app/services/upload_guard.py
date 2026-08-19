"""Bounded reads for multipart uploads.

Every upload handler called `await file.read()` with no byte cap, and no body-size
middleware was registered. nginx permits 500 MB on /api/, and the API is a single uvicorn
process capped at 2 GB — and `storage_service.upload_file` then wraps the bytes in
`io.BytesIO(data)`, roughly doubling peak residency. Five concurrent 500 MB uploads
therefore held ~2.5 GB of bytes plus ~2.5 GB of copies and OOM-killed the container,
dropping every in-flight request for every user.

`read_upload_bounded` reads in chunks against a running total and aborts as soon as the
limit is passed, so an oversized body costs one chunk of memory rather than all of it.

`spooled_upload` is the cheaper option and the one to prefer: Starlette's multipart parser
has already streamed the part into a SpooledTemporaryFile that rolls over to disk past
1 MiB, so a handler that only needs to forward the bytes to object storage can hand that
handle to `storage_service.upload_stream_async` and never allocate the body on the heap at
all. Only callers that must inspect the bytes in-process (zip extraction) need
`read_upload_bounded`.

`BodySizeLimitMiddleware` is the backstop under all of them. Route-level guards only run
after the ASGI server has already received the entire body, so without it the real limit
on every endpoint — including the ones that take no upload at all — was nginx's 500 MB.
"""

import json
import os
from typing import IO, Any, Awaitable, Callable, MutableMapping, Optional

from fastapi import HTTPException, UploadFile

from app.config import settings

_CHUNK = 1024 * 1024  # 1 MiB


def _limit_bytes(limit_mb: Optional[int]) -> int:
    return (limit_mb if limit_mb is not None else settings.max_upload_mb) * 1024 * 1024


def _upload_length(file: UploadFile) -> int:
    """Byte length of an already-received upload, without reading it into memory.

    Starlette's multipart parser keeps a running total on `.size` as it writes the part
    out, so the length is known before the handler touches the body. The seek is the
    fallback for UploadFile objects constructed without a size.
    """
    declared = getattr(file, "size", None)
    if isinstance(declared, int):
        return declared

    handle = file.file
    handle.seek(0, os.SEEK_END)
    length = handle.tell()
    handle.seek(0)
    return length


def assert_upload_within_limit(
    file: UploadFile,
    *,
    limit_mb: Optional[int] = None,
    what: str = "file",
) -> int:
    """Reject an oversized upload and return its byte length.

    Does not consume the stream, so it is the guard for handlers that pass the UploadFile
    itself further down (the skill ZIP endpoints) rather than reading it here.
    """
    limit = _limit_bytes(limit_mb)
    length = _upload_length(file)
    if length > limit:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{what} is {length // (1024 * 1024)} MB, over the "
                f"{limit // (1024 * 1024)} MB limit."
            ),
        )
    return length


async def spooled_upload(
    file: UploadFile,
    *,
    limit_mb: Optional[int] = None,
    what: str = "file",
) -> tuple[IO[bytes], int]:
    """Return a rewound handle on a bounded upload plus its byte length.

    The handle is the SpooledTemporaryFile Starlette already filled, so passing it to
    `upload_stream_async` costs no heap copy. `await file.read()` produced one full copy and
    `upload_file`'s `io.BytesIO(data)` a second — two copies of every upload, which is what
    made five concurrent 500 MB uploads an OOM-kill of the whole API container.
    """
    length = assert_upload_within_limit(file, limit_mb=limit_mb, what=what)
    await file.seek(0)
    return file.file, length


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


# ---------------------------------------------------------------------------
# Request body size limit (ASGI middleware)
# ---------------------------------------------------------------------------

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]


def _declared_length(scope: Scope) -> Optional[int]:
    """Content-Length from the raw ASGI headers, or None if absent/unparseable."""
    for name, value in scope.get("headers") or ():
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_too_large(send: Send, limit: int) -> None:
    """Emit a 413 in FastAPI's own error shape, so clients parse it like any other."""
    body = json.dumps(
        {"detail": f"Request body exceeds the {limit // (1024 * 1024)} MB limit."}
    ).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            # The rest of the body is never drained, so the connection cannot be reused —
            # a keep-alive socket would present the leftover body bytes as the next request.
            (b"connection", b"close"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class BodySizeLimitMiddleware:
    """Reject oversized request bodies before a route can see them.

    Pure ASGI rather than BaseHTTPMiddleware on purpose: BaseHTTPMiddleware materialises
    the request to hand it to a dispatch function, which would buffer the very bytes this
    class exists to refuse.

    Two layers, because neither alone is enough:

    * Content-Length, checked before the app is called at all. An honest client is
      rejected having sent only headers.
    * A running total over the `http.request` messages actually delivered. Content-Length
      is advisory — a client can understate it, and under `Transfer-Encoding: chunked`
      there is no Content-Length to check — so this counter is the real enforcement. It
      counts messages as they pass through and never accumulates them.

    The cap has to sit above every per-route cap (see Settings.validate_upload_limit_
    relationships) or it would reject uploads the routes are documented to accept: the
    ZIP endpoint alone takes 200 MB.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: Optional[int] = None) -> None:
        self.app = app
        self._max_bytes = max_bytes

    @property
    def limit(self) -> int:
        if self._max_bytes is not None:
            return self._max_bytes
        return settings.max_request_body_mb * 1024 * 1024

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        limit = self.limit

        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            await _send_too_large(send, limit)
            return

        received = 0
        cut = False        # the body was refused; nothing more will be delivered
        answered = False   # the 413 is on the wire
        started = False    # the app got a response line out first

        async def counting_receive() -> Message:
            nonlocal received, cut, answered

            if cut:
                # Stay disconnected. Re-delivering body messages after the cut would let a
                # handler that swallows ClientDisconnect resume reading what we refused.
                return {"type": "http.disconnect"}

            message = await receive()
            if message.get("type") != "http.request":
                return message

            received += len(message.get("body") or b"")
            if received <= limit:
                return message

            cut = True
            if not started:
                answered = True
                await _send_too_large(send, limit)
            # A synthetic disconnect unwinds the app's body loop (Starlette raises
            # ClientDisconnect) instead of leaving it awaiting bytes we refuse to read.
            return {"type": "http.disconnect"}

        async def gated_send(message: Message) -> None:
            nonlocal started

            # Once the 413 is on the wire the app's own response must not follow it: a
            # second http.response.start is a protocol violation the server raises on.
            if answered:
                return
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, gated_send)
        except Exception:
            # Anything raised after the body was cut off is the app unwinding on the
            # synthetic disconnect. The client already has its 413, so re-raising would
            # only mislabel a request we deliberately refused as a server error.
            if not cut:
                raise
