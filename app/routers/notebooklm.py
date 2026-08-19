"""NotebookLM integration router.

Endpoints:
  GET    /api/notebooklm/auth/status         — check if NLM session is active + email
  POST   /api/notebooklm/auth/import-cookies — import Google cookies from browser export
  DELETE /api/notebooklm/auth/session        — clear session (logout)
  GET  /api/notebooklm/notebooks            — list notebooks (current user)
  POST /api/notebooklm/notebooks            — create notebook (optionally from source)
  GET  /api/notebooklm/notebooks/{id}       — notebook detail + artifact list
  DELETE /api/notebooklm/notebooks/{id}     — delete notebook
  POST /api/notebooklm/notebooks/{id}/artifacts  — trigger artifact generation
  GET  /api/notebooklm/artifacts/{id}       — artifact status (polling)
  POST /api/notebooklm/artifacts/{id}/ingest    — add artifact to Arkon wiki
  GET  /api/notebooklm/artifacts/{id}/download  — proxy-download artifact file
"""

import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.database.models import (
    Employee,
    NotebookLMArtifact,
    NotebookLMNotebook,
    NotebookLMPassthroughOwner,
    Source,
)
from app.services.audit_service import log_audit
from app.services.auth_service import (
    get_current_user,
    require_admin,
    require_permission,
)
from app.worker import get_arq_pool

router = APIRouter()

def _content_disposition(title: str, ext: str) -> str:
    """Build a Content-Disposition header that survives non-Latin-1 titles.

    Starlette encodes response headers as latin-1, so interpolating a title raw raised
    UnicodeEncodeError — *after* the artifact bytes had already been fetched from Google.
    Any Vietnamese title triggered it, and Vietnamese is this product's default language,
    so the download path 500'd on ordinary content.

    RFC 5987: send an ASCII-safe `filename` for old clients plus a percent-encoded
    `filename*` carrying the real name. Quotes and control characters are stripped rather
    than escaped, since a `"` in the title could otherwise close the quoted string.
    """
    from urllib.parse import quote

    cleaned = "".join(
        c for c in (title or "download") if c.isprintable() and c not in '"\\/\r\n'
    ).strip() or "download"

    ascii_fallback = cleaned.encode("ascii", "replace").decode("ascii").replace("?", "_")
    quoted = quote(f"{cleaned}.{ext}", safe="")
    return f'attachment; filename="{ascii_fallback}.{ext}"; filename*=UTF-8\'\'{quoted}'


VALID_ARTIFACT_TYPES = {
    "audio", "video", "report", "quiz", "flashcards",
    "slide_deck", "infographic", "data_table",
}
VALID_REPORT_FORMATS = {"briefing_doc", "study_guide", "blog_post", "custom"}


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class NotebookCreate(BaseModel):
    title: str
    source_id: Optional[uuid.UUID] = None


class ArtifactGenerate(BaseModel):
    artifact_type: str
    report_format: Optional[str] = None  # only for artifact_type="report"


class NotebookResponse(BaseModel):
    id: uuid.UUID
    notebook_id: str
    title: str
    source_id: Optional[uuid.UUID] = None
    source_title: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: str
    updated_at: str
    artifact_count: int = 0

    model_config = {"from_attributes": True}


class ArtifactResponse(BaseModel):
    id: uuid.UUID
    notebook_ref_id: uuid.UUID
    artifact_id: Optional[str] = None
    task_id: Optional[str] = None
    artifact_type: str
    report_format: Optional[str] = None
    title: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    download_url: Optional[str] = None
    minio_key: Optional[str] = None
    ingest_source_id: Optional[uuid.UUID] = None
    can_add_to_wiki: bool = False
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _notebook_to_response(nb: NotebookLMNotebook) -> NotebookResponse:
    return NotebookResponse(
        id=nb.id,
        notebook_id=nb.notebook_id,
        title=nb.title,
        source_id=nb.source_id,
        source_title=nb.source.title if nb.source else None,
        status=nb.status,
        error_message=nb.error_message,
        created_at=nb.created_at.isoformat(),
        updated_at=nb.updated_at.isoformat(),
        artifact_count=len(nb.artifacts) if nb.artifacts else 0,
    )


def _artifact_to_response(art: NotebookLMArtifact) -> ArtifactResponse:
    from app.services.notebooklm_service import TEXT_ARTIFACT_TYPES
    can_add = (
        art.status == "completed"
        and art.ingest_source_id is None
        and art.artifact_type in TEXT_ARTIFACT_TYPES
    )
    return ArtifactResponse(
        id=art.id,
        notebook_ref_id=art.notebook_ref_id,
        artifact_id=art.artifact_id,
        task_id=art.task_id,
        artifact_type=art.artifact_type,
        report_format=art.report_format,
        title=art.title,
        status=art.status,
        error_message=art.error_message,
        download_url=art.download_url,
        minio_key=art.minio_key,
        ingest_source_id=art.ingest_source_id,
        can_add_to_wiki=can_add,
        created_at=art.created_at.isoformat(),
        updated_at=art.updated_at.isoformat(),
    )


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@router.get("/notebooklm/auth/status")
async def notebooklm_auth_status(
    current_user=Depends(get_current_user),
):
    """Check whether a valid NotebookLM session exists (file-only check, no network)."""
    import json

    from app.services.notebooklm_service import _storage_path

    storage = _storage_path()
    if storage is not None:
        state_file = storage / "storage_state.json"
    else:
        try:
            from notebooklm.paths import get_storage_path as nlm_storage_path
            state_file = nlm_storage_path()
        except Exception:
            return {"authenticated": False, "email": None, "message": "No storage path configured."}

    if not state_file.exists():
        return {"authenticated": False, "email": None, "message": "No session file. Import cookies to connect."}

    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        cookie_names = {c.get("name") for c in data.get("cookies", []) if c.get("name")}
        from notebooklm.auth import MINIMUM_REQUIRED_COOKIES
        missing = MINIMUM_REQUIRED_COOKIES - cookie_names
        if missing:
            return {
                "authenticated": False,
                "email": None,
                "message": f"Session missing required cookies: {', '.join(sorted(missing))}. Re-import cookies.",
            }
        # Try to read email from sibling context.json (written by notebooklm-py login)
        email = None
        try:
            context_file = state_file.with_name("context.json")
            if context_file.exists():
                ctx = json.loads(context_file.read_text(encoding="utf-8"))
                email = (ctx.get("account") or {}).get("email")
        except Exception:
            pass
        last_refreshed = None
        try:
            last_refreshed = state_file.stat().st_mtime
        except Exception:
            pass
        return {
            "authenticated": True,
            "email": email,
            "message": f"Connected{f' as {email}' if email else ''}.",
            "last_refreshed": last_refreshed,
        }
    except Exception:
        # Reported with HTTP 200 and rendered verbatim in the admin UI, so `{e}` put the
        # server's filesystem paths and JSON parser internals on an operator's screen.
        logger.exception("NLM session-file read failed")
        return {
            "authenticated": False,
            "email": None,
            "message": "Could not read the stored session. Re-import cookies.",
        }


@router.post("/notebooklm/auth/refresh")
async def notebooklm_refresh_session(
    current_user=Depends(get_current_user),
):
    """Manually refresh the NotebookLM session cookies."""
    from app.services.notebooklm_service import _storage_path, get_client

    storage = _storage_path()
    if not storage:
        return {"success": False, "message": "No storage path configured"}

    state_file = storage / "storage_state.json"
    if not state_file.exists():
        return {"success": False, "message": "No session file. Import cookies first."}

    try:
        async with await get_client() as client:
            await client.refresh_auth()
        last_refreshed = state_file.stat().st_mtime
        return {"success": True, "message": "Session refreshed", "last_refreshed": last_refreshed}
    except Exception:
        logger.exception("NLM manual session refresh failed")
        return {
            "success": False,
            "message": "Session refresh failed. Re-import cookies or install a master token.",
        }


class CookieImport(BaseModel):
    cookies: list[dict]  # Array of cookies in browser-extension export format


@router.post("/notebooklm/auth/import-cookies", status_code=200)
async def import_cookies(
    body: CookieImport,
    current_user=Depends(require_admin),
):
    """Import Google session cookies exported from a browser extension (Cookie-Editor format).

    The cookies array must come from notebooklm.google.com and related Google domains.
    Minimum required cookies: SID and __Secure-1PSIDTS.

    Instructions for the user:
    1. Install "Cookie-Editor" extension in Chrome/Firefox
    2. Visit https://notebooklm.google.com (make sure you're logged in)
    3. Open Cookie-Editor → Export → Export as JSON
    4. Paste the JSON array here
    """
    if not body.cookies:
        raise HTTPException(status_code=400, detail="cookies array is empty")

    # The conversion lives in the service so it can be tested without a request: it is
    # format-sensitive (Cookie-Editor vs rookiepy key names) and silently lossy when it
    # guesses wrong. See notebooklm_service.cookies_to_storage_state.
    from app.services.notebooklm_service import (
        CookieConversionError,
        cookies_to_storage_state,
    )

    try:
        storage_state = cookies_to_storage_state(body.cookies)
    except CookieConversionError as exc:
        if not exc.missing:
            # Only raised for a missing notebooklm-py, which is a server config problem.
            logger.exception("cookie conversion failed")
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        raise HTTPException(
            status_code=400,
            detail=(
                f"Missing required Google cookies: {', '.join(sorted(exc.missing))}. "
                "Export cookies while on notebooklm.google.com (must be logged in). "
                f"Found: {', '.join(sorted(exc.found)[:10]) or 'none'}."
            ),
        ) from exc

    converted = storage_state["cookies"]

    # Determine storage directory
    import json

    from app.services.notebooklm_service import _storage_path

    storage = _storage_path()
    if storage is None:
        try:
            from notebooklm.paths import get_storage_path as nlm_storage_path
            storage = nlm_storage_path().parent
        except Exception:
            raise HTTPException(
                status_code=500,
                detail="NOTEBOOKLM_STORAGE_PATH is not configured on the server.",
            )

    storage.mkdir(parents=True, exist_ok=True)
    state_file = storage / "storage_state.json"
    state_file.write_text(
        json.dumps(storage_state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    # The required-cookie check already passed inside cookies_to_storage_state, so logging
    # the constant added nothing. The domains are the useful detail when diagnosing an
    # export that saved cleanly and then failed to authenticate.
    logger.info(
        "NLM session cookies saved: %d cookies across %d domain(s)",
        len(converted), len({c["domain"] for c in converted}),
    )

    # Live verification: saving the file only proves the format is valid, not that
    # Google accepts the cookies. Do one real call so the user learns immediately
    # whether the session actually works.
    from app.services.notebooklm_service import get_client

    try:
        async with await get_client() as client:
            await client.refresh_auth()
        return {
            "success": True,
            "verified": True,
            "message": f"Connected — session verified ({len(converted)} cookies).",
        }
    except Exception as e:
        if _is_auth_expired(e):
            # Empirically (2026-08), Google rejects the whole session on server-side
            # replay — even accounts.google.com/RotateCookies returns 401, and this
            # reproduces with fresh cookies from BOTH Chrome and Firefox, same egress
            # IP. That points to Google session binding (DBSC extended beyond Chrome)
            # or an account security policy (Workspace context-aware access / Advanced
            # Protection), not a missing/expired cookie. Cookie import cannot fix this;
            # only a real browser holding the device-bound key can mint a usable session.
            logger.warning("NLM cookies saved but Google rejected the session server-side: {}", e)
            return {
                "success": True,
                "verified": False,
                "message": (
                    "Cookies saved, but Google rejected this session on the server "
                    "(its cookie-rotation endpoint returned 401). Replaying cookies from "
                    "the server is blocked for this account — typically device-bound "
                    "sessions (DBSC) or an account security policy (Workspace "
                    "context-aware access / Advanced Protection). This reproduces with "
                    "cookies from both Chrome and Firefox, so re-exporting will not help. "
                    "Try a plain personal Google account without those protections, or "
                    "use a browser-based login (notebooklm login) on a host that has a "
                    "browser. See docs/notebooklm-auth.md."
                ),
            }
        logger.exception("NLM cookie verification failed with a non-auth error")
        return {
            "success": True,
            "verified": False,
            "message": (
                "Cookies saved, but the verification call failed for an unexpected "
                "reason. See the server log."
            ),
        }


class MasterTokenImport(BaseModel):
    # Contents of the master_token.json produced by `notebooklm login --master-token`.
    master_token: str
    email: str
    android_id: str


@router.post("/notebooklm/auth/master-token", status_code=200)
async def import_master_token(
    body: MasterTokenImport,
    current_user=Depends(require_admin),
):
    """Install a Google master token for headless auth (notebooklm-py ADR-0023).

    Unlike cookie import, a master token lets the server MINT fresh NotebookLM web
    cookies on demand (via gpsoauth), so it survives cookie expiry and Google's
    server-side rejection of DBSC-bound browser cookies. Mint the token once with
    `notebooklm login --master-token` (a dedicated/throwaway account is strongly
    recommended — it is a full-account, long-lived credential) and paste the
    resulting master_token.json here.

    Stored 0600 beside storage_state.json; the value is never logged.
    """
    import json
    import os

    from app.services.notebooklm_service import _storage_path

    if not (body.master_token.strip() and body.email.strip() and body.android_id.strip()):
        raise HTTPException(status_code=400, detail="master_token, email and android_id are required")

    storage = _storage_path()
    if storage is None:
        try:
            from notebooklm.paths import get_storage_path as nlm_storage_path
            storage = nlm_storage_path().parent
        except Exception:
            raise HTTPException(status_code=500, detail="NOTEBOOKLM_STORAGE_PATH is not configured on the server.")

    storage.mkdir(parents=True, exist_ok=True)
    mt_file = storage / "master_token.json"
    # Created 0600 before a byte is written, rather than written under the default umask and
    # narrowed afterwards. `except OSError: pass` on the chmod meant a failure left a
    # full-account, long-lived Google credential world-readable while the docstring above,
    # the response and the log all still promised 0600 — the exact "reports success" shape.
    # os.open with 0o600 removes the window as well as the silence.
    try:
        fd = os.open(mt_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, json.dumps({
                "master_token": body.master_token.strip(),
                "email": body.email.strip(),
                "android_id": body.android_id.strip(),
            }).encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(mt_file, 0o600)  # in case the file already existed with wider bits
    except OSError as exc:
        # Do not leave a credential behind that we could not protect.
        logger.exception("Could not store the NLM master token with 0600 permissions")
        mt_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=(
                "Could not store the master token with owner-only permissions, so it was "
                "not saved. Check the NOTEBOOKLM_STORAGE_PATH mount on the server."
            ),
        ) from exc

    # Remove any stale cookie session so the client mints cleanly from the token
    # (from_storage takes the "storage absent -> mint from sibling token" path).
    stale = storage / "storage_state.json"
    if stale.exists():
        stale.unlink()

    logger.info("NLM master token installed for {} (value not logged)", body.email.strip())

    # Verify: the client should now mint a working session from the token.
    from app.services.notebooklm_service import get_client

    try:
        async with await get_client() as client:
            await client.refresh_auth()
        return {"success": True, "verified": True, "message": f"Connected — master token verified for {body.email.strip()}."}
    except Exception as e:
        if _is_auth_expired(e):
            logger.warning("NLM master token installed but Google rejected the mint: {}", e)
            return {
                "success": True,
                "verified": False,
                "message": (
                    "Master token saved, but Google rejected it when minting a session. "
                    "The token may be revoked/invalid, or the account blocks the Android "
                    "auth path (2FA/Advanced Protection). Re-run 'notebooklm login "
                    "--master-token' with a personal/throwaway account and try again."
                ),
            }
        logger.exception("NLM master token verification failed with a non-auth error")
        return {
            "success": True,
            "verified": False,
            "message": (
                "Master token saved, but the verification call failed for an unexpected "
                "reason. See the server log."
            ),
        }


@router.delete("/notebooklm/auth/session", status_code=200)
async def clear_session(
    current_user=Depends(require_admin),
):
    """Clear the stored NotebookLM session (logout). Also removes the master token."""
    from app.services.notebooklm_service import _storage_path

    storage = _storage_path()
    if storage is None:
        try:
            from notebooklm.paths import get_storage_path as nlm_storage_path
            storage = nlm_storage_path().parent
        except Exception:
            return {"success": True, "message": "No session to clear."}

    state_file = storage / "storage_state.json"
    context_file = storage / "context.json"
    master_token_file = storage / "master_token.json"

    for f in [state_file, context_file, master_token_file]:
        if f.exists():
            f.unlink()

    return {"success": True, "message": "Session cleared."}


# ---------------------------------------------------------------------------
# Notebook endpoints
# ---------------------------------------------------------------------------

@router.get("/notebooklm/notebooks", response_model=list[NotebookResponse])
async def list_notebooks(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """List all NotebookLM notebooks created by the current user."""
    stmt = (
        select(NotebookLMNotebook)
        .where(NotebookLMNotebook.created_by_employee_id == current_user.id)
        .options(
            selectinload(NotebookLMNotebook.source),
            selectinload(NotebookLMNotebook.artifacts),
        )
        .order_by(NotebookLMNotebook.created_at.desc())
    )
    result = await db.execute(stmt)
    notebooks = result.scalars().all()
    return [_notebook_to_response(nb) for nb in notebooks]


@router.post("/notebooklm/notebooks", response_model=NotebookResponse, status_code=201)
async def create_notebook(
    body: NotebookCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Create a new NotebookLM notebook.

    If source_id is provided, the source's full_text is added to the notebook.
    """
    from app.services.notebooklm_service import create_notebook_with_text

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")

    source: Optional[Source] = None
    text_content = f"Notebook: {title}"
    source_title = title

    if body.source_id:
        source = await db.get(Source, body.source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        from app.services.permission_engine import can_access_document
        if not await can_access_document(db, current_user, source, "read"):
            raise HTTPException(status_code=403, detail="Access denied")
        if not source.full_text:
            raise HTTPException(
                status_code=400,
                detail="Source has no extracted text yet. Wait for ingestion to complete.",
            )
        text_content = source.full_text
        source_title = source.title or title

    try:
        notebook_nlm_id, _ = await create_notebook_with_text(
            title=title,
            text_content=text_content,
            source_title=source_title,
        )
    except Exception as exc:
        logger.exception("NLM create_notebook failed")
        raise _nlm_error(exc)

    nb = NotebookLMNotebook(
        notebook_id=notebook_nlm_id,
        title=title,
        source_id=source.id if source else None,
        created_by_employee_id=current_user.id,
        status="active",
    )
    db.add(nb)
    await db.flush()
    await db.refresh(nb)
    await db.commit()

    # Reload with relations
    stmt = (
        select(NotebookLMNotebook)
        .where(NotebookLMNotebook.id == nb.id)
        .options(
            selectinload(NotebookLMNotebook.source),
            selectinload(NotebookLMNotebook.artifacts),
        )
    )
    result = await db.execute(stmt)
    nb = result.scalar_one()
    return _notebook_to_response(nb)


@router.get("/notebooklm/notebooks/{notebook_db_id}", response_model=NotebookResponse)
async def get_notebook(
    notebook_db_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = (
        select(NotebookLMNotebook)
        .where(
            NotebookLMNotebook.id == notebook_db_id,
            NotebookLMNotebook.created_by_employee_id == current_user.id,
        )
        .options(
            selectinload(NotebookLMNotebook.source),
            selectinload(NotebookLMNotebook.artifacts),
        )
    )
    result = await db.execute(stmt)
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")
    return _notebook_to_response(nb)


@router.delete("/notebooklm/notebooks/{notebook_db_id}", status_code=204)
async def delete_notebook(
    notebook_db_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    stmt = select(NotebookLMNotebook).where(
        NotebookLMNotebook.id == notebook_db_id,
        NotebookLMNotebook.created_by_employee_id == current_user.id,
    )
    result = await db.execute(stmt)
    nb = result.scalar_one_or_none()
    if not nb:
        raise HTTPException(status_code=404, detail="Notebook not found")

    # Best-effort: delete on NLM side
    from app.services.notebooklm_service import delete_notebook as nlm_delete
    await nlm_delete(nb.notebook_id)

    await db.delete(nb)
    await db.commit()


# ---------------------------------------------------------------------------
# Artifact endpoints
# ---------------------------------------------------------------------------

@router.get("/notebooklm/notebooks/{notebook_db_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(
    notebook_db_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    # Verify ownership
    nb = await db.get(NotebookLMNotebook, notebook_db_id)
    if not nb or nb.created_by_employee_id != current_user.id:
        raise HTTPException(status_code=404, detail="Notebook not found")

    stmt = (
        select(NotebookLMArtifact)
        .where(NotebookLMArtifact.notebook_ref_id == notebook_db_id)
        .order_by(NotebookLMArtifact.created_at.desc())
    )
    result = await db.execute(stmt)
    artifacts = result.scalars().all()
    return [_artifact_to_response(art) for art in artifacts]


@router.post("/notebooklm/notebooks/{notebook_db_id}/artifacts", response_model=ArtifactResponse, status_code=202)
async def generate_artifact(
    notebook_db_id: uuid.UUID,
    body: ArtifactGenerate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Trigger async generation of an artifact (podcast, video, report, quiz, etc.)."""
    if body.artifact_type not in VALID_ARTIFACT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"artifact_type must be one of: {', '.join(sorted(VALID_ARTIFACT_TYPES))}",
        )
    if body.artifact_type == "report" and body.report_format and body.report_format not in VALID_REPORT_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"report_format must be one of: {', '.join(sorted(VALID_REPORT_FORMATS))}",
        )

    # Verify ownership
    nb = await db.get(NotebookLMNotebook, notebook_db_id)
    if not nb or nb.created_by_employee_id != current_user.id:
        raise HTTPException(status_code=404, detail="Notebook not found")
    if nb.status != "active":
        raise HTTPException(status_code=400, detail="Notebook is not in active state")

    art = NotebookLMArtifact(
        notebook_ref_id=notebook_db_id,
        artifact_type=body.artifact_type,
        report_format=body.report_format,
        status="pending",
    )
    db.add(art)
    await db.flush()
    await db.refresh(art)
    await db.commit()

    # Enqueue background generation
    pool = await get_arq_pool()
    await pool.enqueue_job("notebooklm_generate_task", str(art.id))

    return _artifact_to_response(art)


@router.get("/notebooklm/artifacts/{artifact_db_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_db_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Poll artifact generation status."""
    art = await db.get(NotebookLMArtifact, artifact_db_id)
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    # Verify ownership via notebook
    nb = await db.get(NotebookLMNotebook, art.notebook_ref_id)
    if not nb or nb.created_by_employee_id != current_user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")

    return _artifact_to_response(art)


@router.post("/notebooklm/artifacts/{artifact_db_id}/ingest", status_code=202)
async def ingest_artifact(
    artifact_db_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = require_permission("doc:create"),
):
    """Add a completed artifact to the Arkon wiki (trigger ingest pipeline)."""
    art = await db.get(NotebookLMArtifact, artifact_db_id)
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    nb = await db.get(NotebookLMNotebook, art.notebook_ref_id)
    if not nb or nb.created_by_employee_id != current_user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")

    if art.status != "completed":
        raise HTTPException(status_code=400, detail="Artifact is not completed yet")
    if art.ingest_source_id:
        raise HTTPException(status_code=400, detail="Artifact has already been added to wiki")
    if not art.artifact_id:
        raise HTTPException(status_code=400, detail="Artifact has no NLM ID — generation may still be in progress")

    await log_audit(db, current_user, "create", "source", str(artifact_db_id), reason="notebooklm artifact ingest queued")
    pool = await get_arq_pool()
    await pool.enqueue_job("notebooklm_ingest_artifact_task", str(artifact_db_id))

    return {"status": "queued", "message": "Artifact is being ingested into the wiki"}


@router.get("/notebooklm/artifacts/{artifact_db_id}/download")
async def download_artifact(
    artifact_db_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Proxy-download a completed binary artifact (audio/video/etc.)."""
    from app.services.notebooklm_service import (
        ARTIFACT_EXT,
        ARTIFACT_MIME,
        get_artifact_bytes,
    )

    art = await db.get(NotebookLMArtifact, artifact_db_id)
    if not art:
        raise HTTPException(status_code=404, detail="Artifact not found")

    nb = await db.get(NotebookLMNotebook, art.notebook_ref_id)
    if not nb or nb.created_by_employee_id != current_user.id:
        raise HTTPException(status_code=404, detail="Artifact not found")

    if art.status != "completed":
        raise HTTPException(status_code=400, detail="Artifact not ready")
    if not art.artifact_id:
        raise HTTPException(status_code=400, detail="No artifact ID")

    # If already in MinIO, redirect via presigned URL
    if art.minio_key:
        from app.services.storage_service import storage_service

        # Presigning is local HMAC work, but minio's client can reach for bucket region
        # metadata over a synchronous socket on first use — enough to stall the event loop
        # for every other request when MinIO is slow or unreachable.
        url = await storage_service.get_presigned_url_async(art.minio_key)
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url=url)

    # Otherwise stream bytes directly
    data = await get_artifact_bytes(nb.notebook_id, art.artifact_id, art.artifact_type)
    if not data:
        raise HTTPException(status_code=502, detail="Could not download artifact from NotebookLM")

    ext = ARTIFACT_EXT.get(art.artifact_type, "bin")
    mime = ARTIFACT_MIME.get(art.artifact_type, "application/octet-stream")
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": _content_disposition(
                art.title or art.artifact_type, ext
            )
        },
    )


# ---------------------------------------------------------------------------
# NLM passthrough endpoints — direct NLM API, no Arkon DB
# Notebooks, sources, artifacts, and chat are fetched live from NotebookLM.
# ---------------------------------------------------------------------------

class NLMNotebookCreate(BaseModel):
    title: str


class NLMSourceAdd(BaseModel):
    kind: str  # "url" | "text" | "arkon" | "drive"
    url: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    source_id: Optional[uuid.UUID] = None  # for kind="arkon"


def _parse_drive_url(url: str) -> tuple[str, str, str]:
    """Return (file_id, mime_type, suggested_title) from a Google Drive/Docs URL."""
    m = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1), "application/vnd.google-apps.document", "Google Doc"
    m = re.search(r"/presentation/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1), "application/vnd.google-apps.presentation", "Google Slides"
    m = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1), "application/vnd.google-apps.spreadsheet", "Google Sheet"
    m = re.search(r"/file/d/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1), "application/pdf", "Drive file"
    from urllib.parse import parse_qs, urlparse
    qs = parse_qs(urlparse(url).query)
    if "id" in qs:
        return qs["id"][0], "application/pdf", "Drive file"
    raise ValueError(f"Could not extract Google Drive file ID from URL: {url}")


class NLMArtifactGenerate(BaseModel):
    artifact_type: str
    report_format: Optional[str] = None
    instructions: Optional[str] = None


class NLMChatAsk(BaseModel):
    question: str
    conversation_id: Optional[str] = None


# Substrings notebooklm-py emits when the Google session is dead but the error
# is NOT the typed AuthError (e.g. a login redirect surfaced as a generic error).
# Matching these keeps expiry mapped to 401 so the UI reliably prompts a reconnect.
_AUTH_EXPIRED_MARKERS = (
    "authentication expired",
    "accounts.google.com",
    "notebooklm login",
    "re-authenticate",
    "not authenticated",
)


def _is_auth_expired(e: Exception) -> bool:
    try:
        from notebooklm.exceptions import AuthError
        if isinstance(e, AuthError):
            return True
    except ImportError:
        pass
    msg = str(e).lower()
    return any(marker in msg for marker in _AUTH_EXPIRED_MARKERS)


def _nlm_error(e: Exception) -> HTTPException:
    if _is_auth_expired(e):
        return HTTPException(
            status_code=401,
            detail="NotebookLM session expired. Re-import cookies to reconnect.",
        )
    # Fixed text: every 502 in this router funnels through here, and the upstream
    # exception message routinely carries request URLs and Google response bodies.
    return HTTPException(status_code=502, detail="NotebookLM API error — see server log.")


# ---------------------------------------------------------------------------
# Passthrough ownership boundary
#
# Every notebook below lives in ONE shared Google account, so NotebookLM's own per-user
# isolation gives us nothing: the client Arkon builds from storage_state.json can reach
# every notebook in the company. Ownership therefore has to be enforced here, against
# Arkon's own record, before any nlm_id is forwarded to Google.
#
# Two tables count as a record of ownership:
#   * notebooklm_passthrough_owners — written when this router creates a notebook.
#   * notebooklm_notebooks          — written by the DB-backed /notebooklm/notebooks
#                                     endpoints, which also create a real NLM notebook.
# Ignoring the second would make a notebook created by "Send to NotebookLM" invisible on
# the passthrough page the user is redirected to immediately afterwards, so both are
# consulted rather than dual-writing the same fact into two tables.
#
# Notebooks with no record in either table — everything that predates this boundary, plus
# anything created directly in the Google account — resolve for admins only. The two
# alternatives were both rejected: hiding them from every list while still honouring a
# guessed id in DELETE leaves them invisible-but-deletable, and treating "unowned" as
# "public" re-creates the exact company-wide read that this code exists to stop. Admin-only
# keeps them reachable for cleanup by someone who is already trusted with every notebook.
# ---------------------------------------------------------------------------

async def _owned_nlm_ids(db: AsyncSession, current_user: Employee) -> set[str]:
    """Return the Google notebook ids this employee is recorded as having created."""
    passthrough = await db.execute(
        select(NotebookLMPassthroughOwner.nlm_id).where(
            NotebookLMPassthroughOwner.owner_employee_id == current_user.id
        )
    )
    db_backed = await db.execute(
        select(NotebookLMNotebook.notebook_id).where(
            NotebookLMNotebook.created_by_employee_id == current_user.id
        )
    )
    return set(passthrough.scalars().all()) | set(db_backed.scalars().all())


async def _resolve_nlm_notebook(
    db: AsyncSession, current_user: Employee, nlm_id: str
) -> str:
    """Return nlm_id if the caller may act on it, else 404.

    Admins pass unconditionally, matching require_permission(), which short-circuits on
    `role == "admin"` before consulting any grant.

    A foreign notebook answers 404 and not 403 on purpose: 403 confirms that the id names a
    real notebook in the shared account, which is enough to enumerate other employees'
    notebooks one id at a time.
    """
    if current_user.role == "admin":
        return nlm_id

    owned = await db.execute(
        select(NotebookLMPassthroughOwner.id).where(
            NotebookLMPassthroughOwner.nlm_id == nlm_id,
            NotebookLMPassthroughOwner.owner_employee_id == current_user.id,
        )
    )
    if owned.scalar_one_or_none() is not None:
        return nlm_id

    db_backed = await db.execute(
        select(NotebookLMNotebook.id).where(
            NotebookLMNotebook.notebook_id == nlm_id,
            NotebookLMNotebook.created_by_employee_id == current_user.id,
        )
    )
    if db_backed.scalars().first() is not None:
        return nlm_id

    raise HTTPException(status_code=404, detail="Notebook not found")


@router.get("/notebooklm/nlm/notebooks")
async def nlm_list_notebooks(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import list_nlm_notebooks
    try:
        notebooks = await list_nlm_notebooks()
    except Exception as e:
        raise _nlm_error(e)

    if current_user.role == "admin":
        return notebooks

    owned = await _owned_nlm_ids(db, current_user)
    return [nb for nb in notebooks if nb.get("id") in owned]


@router.post("/notebooklm/nlm/notebooks", status_code=201)
async def nlm_create_notebook(
    body: NLMNotebookCreate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import create_nlm_notebook
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title is required")
    try:
        nb = await create_nlm_notebook(title)
    except Exception as e:
        raise _nlm_error(e)

    # The notebook now exists in the shared Google account and is reachable by every
    # request Arkon makes. Claim it before returning, or it is born unowned — which means
    # admin-only, i.e. the creator immediately loses the notebook they just made.
    db.add(
        NotebookLMPassthroughOwner(
            nlm_id=nb["id"], owner_employee_id=current_user.id
        )
    )
    await db.commit()

    return nb


@router.delete("/notebooklm/nlm/notebooks/{nlm_id}", status_code=204)
async def nlm_delete_notebook(
    nlm_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import delete_nlm_notebook
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        deleted = await delete_nlm_notebook(nlm_id)
    except Exception as e:
        raise _nlm_error(e)

    # A falsey return means Google refused the delete. Answering 204 here — as this route
    # did unconditionally — made the UI drop a notebook from the list that still existed,
    # so the user believed confidential content was gone when it was not.
    if not deleted:
        raise HTTPException(
            status_code=502,
            detail="NotebookLM did not confirm the delete; the notebook still exists.",
        )

    await db.execute(
        delete(NotebookLMPassthroughOwner).where(
            NotebookLMPassthroughOwner.nlm_id == nlm_id
        )
    )
    await db.commit()


@router.get("/notebooklm/nlm/notebooks/{nlm_id}/sources")
async def nlm_list_sources(
    nlm_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import list_nlm_sources
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        return await list_nlm_sources(nlm_id)
    except Exception as e:
        raise _nlm_error(e)


@router.post("/notebooklm/nlm/notebooks/{nlm_id}/sources", status_code=201)
async def nlm_add_source(
    nlm_id: str,
    body: NLMSourceAdd,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import add_nlm_source_text, add_nlm_source_url
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        if body.kind == "url":
            if not body.url:
                raise HTTPException(status_code=400, detail="url is required")
            return await add_nlm_source_url(nlm_id, body.url)

        elif body.kind == "text":
            if not body.content:
                raise HTTPException(status_code=400, detail="content is required")
            return await add_nlm_source_text(nlm_id, body.title or "Pasted text", body.content)

        elif body.kind == "arkon":
            if not body.source_id:
                raise HTTPException(status_code=400, detail="source_id is required")
            source = await db.get(Source, body.source_id)
            if not source:
                raise HTTPException(status_code=404, detail="Arkon source not found")
            from app.services.permission_engine import can_access_document
            if not await can_access_document(db, current_user, source, "read"):
                raise HTTPException(status_code=403, detail="Access denied")
            if not source.full_text:
                raise HTTPException(status_code=400, detail="Source has no extracted text yet")
            return await add_nlm_source_text(nlm_id, source.title or "Arkon source", source.full_text)

        elif body.kind == "drive":
            if not body.url:
                raise HTTPException(status_code=400, detail="url is required for Google Drive sources")
            try:
                file_id, mime_type, suggested_title = _parse_drive_url(body.url)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            from app.services.notebooklm_service import add_nlm_source_drive
            return await add_nlm_source_drive(nlm_id, file_id, body.title or suggested_title, mime_type)

        else:
            raise HTTPException(status_code=400, detail="kind must be url, text, arkon, or drive")
    except HTTPException:
        raise
    except Exception as e:
        raise _nlm_error(e)


@router.delete("/notebooklm/nlm/notebooks/{nlm_id}/sources/{source_id}", status_code=204)
async def nlm_delete_source(
    nlm_id: str,
    source_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import delete_nlm_source
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        await delete_nlm_source(nlm_id, source_id)
    except Exception as e:
        raise _nlm_error(e)


@router.post("/notebooklm/nlm/notebooks/{nlm_id}/sources/upload", status_code=201)
async def nlm_upload_source(
    nlm_id: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upload a file (PDF, DOCX, markdown, CSV, EPUB, image) as a notebook source."""
    import os
    import tempfile

    from app.services.notebooklm_service import add_nlm_source_file
    from app.services.upload_guard import read_upload_bounded

    await _resolve_nlm_notebook(db, current_user, nlm_id)
    content = await read_upload_bounded(file, what="Upload")
    suffix = Path(file.filename or "upload").suffix or ".bin"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        return await add_nlm_source_file(
            nlm_id,
            Path(tmp_path),
            mime_type=file.content_type,
            title=title or file.filename or "Uploaded file",
        )
    except Exception as e:
        raise _nlm_error(e)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


@router.get("/notebooklm/nlm/notebooks/{nlm_id}/artifacts")
async def nlm_list_artifacts(
    nlm_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import list_nlm_artifacts
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        return await list_nlm_artifacts(nlm_id)
    except Exception as e:
        raise _nlm_error(e)


@router.post("/notebooklm/nlm/notebooks/{nlm_id}/artifacts/generate", status_code=202)
async def nlm_generate_artifact(
    nlm_id: str,
    body: NLMArtifactGenerate,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import generate_nlm_artifact
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    if body.artifact_type not in VALID_ARTIFACT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"artifact_type must be one of: {', '.join(sorted(VALID_ARTIFACT_TYPES))}",
        )
    try:
        return await generate_nlm_artifact(nlm_id, body.artifact_type, body.report_format, body.instructions)
    except Exception as e:
        raise _nlm_error(e)


@router.get("/notebooklm/nlm/notebooks/{nlm_id}/artifacts/{artifact_id}/preview-data")
async def nlm_artifact_preview_data(
    nlm_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return structured preview data for a completed artifact."""
    from app.services.notebooklm_service import (
        TEXT_ARTIFACT_TYPES,
        get_artifact_preview_data,
        list_nlm_artifacts,
    )

    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        artifacts = await list_nlm_artifacts(nlm_id)
    except Exception as exc:
        logger.exception("NLM artifact listing failed for notebook={}", nlm_id)
        raise HTTPException(status_code=502, detail="Could not fetch artifacts.") from exc

    artifact = next((a for a in artifacts if a["id"] == artifact_id), None)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact["status"] != 3:
        raise HTTPException(status_code=400, detail="Artifact is not completed yet")
    if artifact["kind"] not in TEXT_ARTIFACT_TYPES:
        raise HTTPException(status_code=400, detail=f"Only text artifacts have structured preview (got {artifact['kind']})")

    try:
        data = await get_artifact_preview_data(nlm_id, artifact_id, artifact["kind"])
    except Exception as exc:
        logger.exception("NLM artifact preview failed for notebook={} artifact={}", nlm_id, artifact_id)
        raise HTTPException(status_code=502, detail="Could not load the artifact preview.") from exc

    if not data:
        raise HTTPException(status_code=502, detail="No preview data available")

    return data


@router.post("/notebooklm/nlm/notebooks/{nlm_id}/artifacts/{artifact_id}/ingest", status_code=202)
async def nlm_ingest_artifact(
    nlm_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = require_permission("doc:create"),
):
    """Fetch artifact from NLM and create an Arkon Source + enqueue ingestion pipeline.

    Text artifacts (report, quiz, flashcards, data_table): extract text → MRP pipeline.
    Binary PDF artifacts (slide_deck): download → MinIO → file ingestion pipeline.
    """
    from app.services.notebooklm_service import (
        ARTIFACT_EXT,
        ARTIFACT_MIME,
        TEXT_ARTIFACT_TYPES,
        get_artifact_bytes,
        get_artifact_text,
        list_nlm_artifacts,
    )
    from app.worker import get_arq_pool

    INGESTABLE_TYPES = TEXT_ARTIFACT_TYPES | {"slide_deck"}

    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        artifacts = await list_nlm_artifacts(nlm_id)
    except Exception as exc:
        logger.exception("NLM artifact listing failed for notebook={}", nlm_id)
        raise HTTPException(status_code=502, detail="Could not fetch artifacts.") from exc

    artifact = next((a for a in artifacts if a["id"] == artifact_id), None)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found in this notebook")

    kind = artifact["kind"]
    if kind not in INGESTABLE_TYPES:
        raise HTTPException(status_code=400, detail=f"Artifact type '{kind}' cannot be added to wiki")
    if artifact["status"] != 3:
        raise HTTPException(status_code=400, detail="Artifact is not completed yet")

    title = artifact.get("title") or f"{kind.replace('_', ' ').title()} from NotebookLM"

    if kind == "slide_deck":
        # PDF: download bytes → MinIO → file source → ingest_file_task → MRP pipeline
        # ingest_file_task uses Tesseract OCR fallback for image-based PDFs.
        from app.services.storage_service import storage_service

        try:
            data = await get_artifact_bytes(nlm_id, artifact_id, kind)
        except Exception as exc:
            logger.exception("NLM slide-deck download failed for artifact={}", artifact_id)
            raise HTTPException(status_code=502, detail="Could not download the slide deck.") from exc

        if not data:
            raise HTTPException(status_code=502, detail="Slide deck produced no data")

        ext = ARTIFACT_EXT.get(kind, "pdf")
        mime = ARTIFACT_MIME.get(kind, "application/pdf")
        file_name = f"{title}.{ext}".replace("/", "-")
        minio_key = f"notebooklm/{artifact_id}/{file_name}"
        # minio's client is synchronous sockets, so calling it directly from an async
        # handler blocks the whole event loop for the length of a multi-megabyte PDF
        # upload — every other request on this worker stalls behind it.
        await storage_service.upload_file_async(minio_key, data, mime)

        source = Source(
            title=title,
            source_type="file",
            file_name=file_name,
            minio_key=minio_key,
            file_size=len(data),
            status="processing",
            progress=0,
            scope_type="global",
            contributed_by_employee_id=current_user.id,
        )
        db.add(source)
        await db.flush()
        await log_audit(db, current_user, "create", "source", str(source.id), reason="notebooklm artifact ingest")
        await db.commit()
        await db.refresh(source)

        pool = await get_arq_pool()
        await pool.enqueue_job("ingest_file_task", str(source.id))
    else:
        # Text artifact: extract text → text source → MRP pipeline
        try:
            text = await get_artifact_text(nlm_id, artifact_id, kind)
        except Exception as exc:
            logger.exception("NLM artifact text extraction failed for artifact={}", artifact_id)
            raise HTTPException(status_code=502, detail="Could not extract the artifact text.") from exc

        if not text:
            raise HTTPException(status_code=502, detail="Artifact produced no extractable text")

        source = Source(
            title=title,
            source_type="text",
            full_text=text,
            status="processing",
            progress=55,
            progress_message="Queuing wiki compilation...",
            scope_type="global",
            contributed_by_employee_id=current_user.id,
        )
        db.add(source)
        await db.flush()
        await log_audit(db, current_user, "create", "source", str(source.id), reason="notebooklm artifact ingest")
        await db.commit()
        await db.refresh(source)

        pool = await get_arq_pool()
        await pool.enqueue_job("ingest_map_reduce_task", str(source.id))

    return {"status": "queued", "source_id": str(source.id), "message": "Artifact is being ingested into the wiki"}


@router.get("/notebooklm/nlm/notebooks/{nlm_id}/artifacts/{artifact_id}/download")
async def nlm_download_artifact(
    nlm_id: str,
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import (
        ARTIFACT_EXT,
        ARTIFACT_MIME,
        BINARY_ARTIFACT_TYPES,
        get_artifact_bytes,
        list_nlm_artifacts,
    )
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    try:
        artifacts = await list_nlm_artifacts(nlm_id)
    except Exception as exc:
        logger.exception("NLM artifact listing failed for notebook={}", nlm_id)
        raise HTTPException(status_code=502, detail="Could not fetch artifacts.") from exc

    artifact = next((a for a in artifacts if a["id"] == artifact_id), None)
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    if artifact["status"] != 3:
        raise HTTPException(status_code=400, detail="Artifact is not ready yet")

    kind = artifact["kind"]
    if kind not in BINARY_ARTIFACT_TYPES:
        raise HTTPException(status_code=400, detail="Only binary artifacts can be downloaded this way")

    try:
        data = await get_artifact_bytes(nlm_id, artifact_id, kind)
    except Exception as exc:
        logger.exception("NLM artifact download failed for artifact={}", artifact_id)
        raise HTTPException(status_code=502, detail="Artifact download failed.") from exc

    if not data:
        raise HTTPException(status_code=502, detail="Empty artifact data")

    ext = ARTIFACT_EXT.get(kind, "bin")
    mime = ARTIFACT_MIME.get(kind, "application/octet-stream")
    return Response(
        content=data,
        media_type=mime,
        headers={
            "Content-Disposition": _content_disposition(
                artifact.get("title") or kind, ext
            )
        },
    )


@router.post("/notebooklm/nlm/notebooks/{nlm_id}/chat")
async def nlm_chat(
    nlm_id: str,
    body: NLMChatAsk,
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    from app.services.notebooklm_service import nlm_chat_ask
    logger.info(f"NLM chat: notebook={nlm_id} question={body.question[:60]!r}")
    # NotebookLM answers from the notebook's own sources, so an unguarded chat is a full
    # read of someone else's documents laundered through Google's RAG.
    await _resolve_nlm_notebook(db, current_user, nlm_id)
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="question is required")
    try:
        result = await nlm_chat_ask(nlm_id, body.question, body.conversation_id)
        logger.info(f"NLM chat OK: answer_len={len(result.get('answer',''))}")
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        logger.error(f"NLM chat error: {e}")
        raise _nlm_error(e)


class ChatIngestRequest(BaseModel):
    title: str
    content: str


@router.post("/notebooklm/nlm/notebooks/{nlm_id}/chat/ingest", status_code=202)
async def nlm_ingest_chat(
    nlm_id: str,
    body: ChatIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Employee = require_permission("doc:create"),
):
    """Save a NotebookLM chat conversation as a wiki source."""
    from app.worker import get_arq_pool

    # The body is client-supplied, so the guard does not protect the content itself — it
    # stops a foreign notebook id being laundered into the wiki as provenance.
    await _resolve_nlm_notebook(db, current_user, nlm_id)

    if not body.content.strip():
        raise HTTPException(status_code=400, detail="Chat content is empty")

    source = Source(
        title=body.title,
        source_type="text",
        full_text=body.content,
        status="processing",
        progress=55,
        progress_message="Queuing wiki compilation...",
        scope_type="global",
        contributed_by_employee_id=current_user.id,
    )
    db.add(source)
    await db.flush()
    await log_audit(db, current_user, "create", "source", str(source.id), reason="notebooklm chat ingest")
    await db.commit()
    await db.refresh(source)

    pool = await get_arq_pool()
    await pool.enqueue_job("ingest_map_reduce_task", str(source.id))

    return {"status": "queued", "source_id": str(source.id), "message": "Chat is being compiled to wiki pages"}
