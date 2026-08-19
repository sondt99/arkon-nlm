"""
Knowledge Base service — text/image extraction helpers for the wiki pipeline.

Ingestion itself lives in app/worker.py (`ingest_file_task` + `caption_images_task`), which
is the only path a source can take: upload → MinIO → arq. This module used to also carry a
whole second `ingest_source()` implementation of the same pipeline, inline and unqueued,
with no callers anywhere — it drifted (no resume, no per-image timeout, captions on the
request loop) and every reader had to work out which of the two was live. Deleted; the
helpers the worker imports are what remains.
"""

import asyncio
from typing import Optional

from loguru import logger

from app.services.image_service import ImageInfo

_MIN_OCR_CHARS = 50

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_content_type(file_name: str) -> str:
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    return {
        "pdf": "application/pdf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "doc": "application/msword",
        "txt": "text/plain",
        "md": "text/markdown",
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }.get(ext, "application/octet-stream")


def _sanitize_caption_for_alt(caption: str) -> str:
    """Make a caption safe to use inside markdown image alt text."""
    # Strip newlines + characters that would break `![alt](url)` parsing.
    cleaned = caption.replace("\n", " ").replace("\r", " ")
    cleaned = cleaned.replace("[", "(").replace("]", ")")
    return cleaned.strip()


def _inline_image_markers(pages_data: list[dict], images: list[ImageInfo]) -> None:
    """Inject markdown image markers into per-page text.

    Each image becomes `![caption](image://<uuid>)` appended at the end of the
    page it came from. The wiki compiler is instructed to preserve these
    markers in the most contextually-relevant wiki page, drop irrelevant ones,
    and never invent UUIDs. Mutates pages_data in place.
    """
    if not images:
        return

    by_page: dict[int, list[str]] = {}
    for img in images:
        if not img.image_id:
            continue
        if (img.caption or "").startswith("[decorative]"):
            continue
        alt = _sanitize_caption_for_alt(img.caption or "")
        marker = f"![{alt}](image://{img.image_id})"
        page_num = img.page_number or 1
        by_page.setdefault(page_num, []).append(marker)

    if not by_page:
        return

    for page in pages_data:
        pnum = page.get("page_number") or 1
        markers = by_page.get(pnum)
        if not markers:
            continue
        joined = "\n\n".join(markers)
        page["content"] = (page.get("content") or "") + f"\n\n{joined}\n"


def _clean_text(text: str) -> str:
    """Strip characters PostgreSQL UTF-8 cannot store (null bytes, lone surrogates)."""
    return (text or "").replace("\x00", "")


def _extract_pdf_pages(file_data: bytes) -> list[dict]:
    """Per-page PDF text, with 300-dpi OCR for pages that carry no digital text.

    Synchronous on purpose — _extract_text_from_file runs this in a thread.
    """
    import fitz
    pages_data: list[dict] = []
    doc = fitz.open(stream=file_data, filetype="pdf")
    for i, page in enumerate(doc):  # type: ignore[arg-type]
        text = _clean_text(page.get_text() or "").strip()
        if len(text) < _MIN_OCR_CHARS:
            try:
                tp = page.get_textpage_ocr(flags=0, language="vie+eng", dpi=300, full=True)
                ocr_text = _clean_text(page.get_text(textpage=tp) or "").strip()
                if len(ocr_text) > len(text):
                    text = ocr_text
            except Exception as ocr_err:
                logger.warning(f"OCR failed for page {i + 1}: {ocr_err}")
        pages_data.append({"content": text, "page_number": i + 1})
    doc.close()
    return pages_data


def _extract_docx_pages(file_data: bytes) -> Optional[list[dict]]:
    """Raw .docx text, or None when mammoth cannot read it so the caller falls through.

    Synchronous on purpose — _extract_text_from_file runs this in a thread.
    """
    import io

    import mammoth
    try:
        result = mammoth.extract_raw_text(io.BytesIO(file_data))
        return [{"content": _clean_text(result.value or ""), "page_number": 1}]
    except Exception:
        return None


async def _extract_text_from_file(file_data: bytes, file_name: str) -> list[dict]:
    """Extract text from a binary file, returning per-page records.

    The PDF and .docx paths are uninterruptible CPU inside C extensions: a 300-page
    scanned PDF is minutes of 300-dpi rasterising plus in-process Tesseract (MuPDF links
    libtesseract; there is no subprocess to wait on). Run inline they froze the arq
    worker's event loop for that entire time, so its health_check_interval=30 heartbeat
    never reached Redis, the container was marked unhealthy after ~90 s, and everything
    behind `depends_on: service_healthy` went with it.
    """
    ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""

    if ext == "pdf":
        return await asyncio.to_thread(_extract_pdf_pages, file_data)

    if ext == "docx":
        pages_data = await asyncio.to_thread(_extract_docx_pages, file_data)
        if pages_data is not None:
            return pages_data
        # fall through to content_core

    if ext in ("txt", "md", "csv"):
        return [{"content": _clean_text(file_data.decode("utf-8", errors="ignore")), "page_number": 1}]

    # Other formats (doc, xlsx, pptx, ...): write to a temp file and let
    # content-core extract via file path. Passing raw bytes as "content"
    # doesn't work for binary formats — content-core expects a string there.
    import os
    import tempfile

    try:
        from content_core.content.extraction import extract_content
        suffix = f".{ext}" if ext else ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_data)
            tmp_path = tmp.name
        try:
            result = await extract_content({
                "file_path": tmp_path,
                "output_format": "markdown",
            })
            return [{"content": _clean_text(result.content or ""), "page_number": 1}]
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        logger.warning(f"content-core extraction failed for .{ext}: {e}")
        return [{"content": "", "page_number": 1}]


def _validate_url_not_internal(url: str) -> None:
    """Block URLs pointing to private/internal networks (SSRF prevention)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http/https URLs are allowed")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("Invalid URL: no hostname")

    blocked_hosts = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}
    if hostname.lower() in blocked_hosts:
        raise ValueError("URLs pointing to localhost are not allowed")

    try:
        resolved = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError(f"Cannot resolve hostname: {hostname}")

    for _, _, _, _, addr in resolved:
        ip = ipaddress.ip_address(addr[0])
        # is_multicast / is_unspecified cover 0.0.0.0, ::, and 224.0.0.0/4, which the
        # original set missed. IPv4-mapped IPv6 (::ffff:127.0.0.1) is unwrapped first,
        # because is_loopback is False on the mapped form.
        mapped = getattr(ip, "ipv4_mapped", None)
        if mapped is not None:
            ip = mapped
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError("URLs pointing to private/internal networks are not allowed")


async def _validate_url_not_internal_async(url: str) -> None:
    """Non-blocking wrapper for _validate_url_not_internal using asyncio.to_thread.

    socket.getaddrinfo is a blocking C call with no timeout we control: a hostname served
    by a deliberately slow authoritative nameserver holds the whole event loop for as long
    as the resolver keeps retrying, and the redirect walk in _fetch_url_guarded resolves
    once per hop.
    """
    await asyncio.to_thread(_validate_url_not_internal, url)


async def _extract_text_from_url(url: str) -> list[dict]:
    """Extract text from a URL — markdown output preferred."""
    await _validate_url_not_internal_async(url)
    try:
        from content_core.content.extraction import extract_content
        result = await extract_content({"url": url, "output_format": "markdown"})
        return [{"content": _clean_text(result.content or ""), "page_number": 1}]
    except Exception as e:
        logger.warning(f"URL extraction failed for {url}: {e}")
        return [{"content": _clean_text(await _fetch_url_guarded(url)), "page_number": 1}]


# Redirects are followed manually so every hop is validated. With
# follow_redirects=True only the *first* URL was ever checked, so an attacker-controlled
# host could 302 to 169.254.169.254 or minio:9000 and the response body was stored in
# Source.full_text — a readable SSRF, not a blind one.
_MAX_REDIRECTS = 5


async def _fetch_url_guarded(url: str) -> str:
    """GET a URL, validating the target before every hop."""
    import httpx

    current = url
    async with httpx.AsyncClient(follow_redirects=False, timeout=30) as client:
        for _ in range(_MAX_REDIRECTS):
            await _validate_url_not_internal_async(current)
            resp = await client.get(current)
            if resp.status_code in (301, 302, 303, 307, 308):
                location = resp.headers.get("location")
                if not location:
                    raise ValueError("Redirect response without a Location header")
                # Relative redirects must be resolved against the current URL before
                # validation, or a `/latest/meta-data` Location would slip through.
                from urllib.parse import urljoin

                current = urljoin(current, location)
                continue
            return resp.text
    raise ValueError(f"Too many redirects while fetching {url}")
