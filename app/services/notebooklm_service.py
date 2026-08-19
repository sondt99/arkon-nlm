"""NotebookLM service — wraps the notebooklm-py client for use inside Arkon.

All methods that call the NotebookLM API are async and expect to be called
from FastAPI route handlers or arq worker tasks.

Session management: the notebooklm-py library stores Google auth cookies on
disk. The storage path is configured via NOTEBOOKLM_STORAGE_PATH in .env.
Before any API call can succeed, the user must have logged in once with:
    notebooklm session login
(or via the /api/notebooklm/auth/status + /auth/login-url endpoints).
"""

from pathlib import Path
from typing import Optional

from loguru import logger

from app.config import settings

# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _storage_path() -> Optional[Path]:
    """Return the configured notebooklm session storage path, or None for default."""
    raw = (settings.notebooklm_storage_path or "").strip()
    if not raw:
        return None
    p = Path(raw)
    p.mkdir(parents=True, exist_ok=True)
    return p


async def get_client():
    """Return an authenticated NotebookLMClient context manager.

    Usage:
        async with await get_client() as client:
            notebooks = await client.notebooks.list()

    Raises:
        ImportError: if notebooklm-py is not installed
        notebooklm.AuthError: if no valid session exists
    """
    try:
        from notebooklm import NotebookLMClient
    except ImportError as e:
        raise ImportError(
            "notebooklm-py is not installed. "
            "Run: pip install -e 'E:/AI-CLAUDE/notebooklm-py[playwright]'"
        ) from e

    storage = _storage_path()
    # from_storage() expects the path to storage_state.json, not the directory
    state_file = (storage / "storage_state.json") if storage is not None else None
    return await NotebookLMClient.from_storage(path=state_file)


async def is_authenticated() -> bool:
    """Return True if a valid NotebookLM session exists.

    ImportError propagates. A missing notebooklm-py package is a deployment fault, not an
    auth state: reporting it as "not authenticated" sent admins to redo a Google login that
    could never fix it, however many times they tried.
    """
    try:
        async with await get_client() as client:
            await client.notebooks.list()
        return True
    except ImportError:
        raise
    except Exception as exc:
        logger.warning(f"NLM session check failed: {exc}")
        return False


# ---------------------------------------------------------------------------
# Notebook helpers
# ---------------------------------------------------------------------------

async def create_notebook_with_text(
    title: str,
    text_content: str,
    source_title: str,
) -> tuple[str, str]:
    """Create a new NLM notebook and add text content as a source.

    Returns:
        (notebook_id, nlm_source_id)
    """
    async with await get_client() as client:
        notebook = await client.notebooks.create(title=title)
        source = await client.sources.add_text(
            notebook.id,
            title=source_title,
            content=text_content,
            wait=False,
        )
        return notebook.id, source.id


async def delete_notebook(notebook_nlm_id: str) -> bool:
    """Delete a notebook from NotebookLM."""
    try:
        async with await get_client() as client:
            return await client.notebooks.delete(notebook_nlm_id)
    except Exception as e:
        logger.warning(f"NLM delete notebook {notebook_nlm_id}: {e}")
        return False


# ---------------------------------------------------------------------------
# Artifact content extraction (for "Add to Wiki")
# ---------------------------------------------------------------------------

async def get_artifact_text(
    notebook_nlm_id: str,
    artifact_nlm_id: str,
    artifact_type: str,
    report_format: Optional[str] = None,
) -> Optional[str]:
    """Extract text content from a completed artifact for ingestion into Arkon wiki.

    Returns markdown/text for text-type artifacts (report, quiz, flashcards, data_table).
    Returns None for binary artifacts (audio, video, slide_deck, infographic).
    Uses the download_* API (0.4.x compatible) — content is embedded in RPC responses,
    not served via artifact.url.
    """
    import os
    import tempfile

    async with await get_client() as client:
        if artifact_type == "quiz":
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    path = os.path.join(tmpdir, "quiz.md")
                    await client.artifacts.download_quiz(
                        notebook_nlm_id, path, artifact_id=artifact_nlm_id, output_format="markdown"
                    )
                    return open(path, encoding="utf-8").read()
            except Exception as e:
                logger.warning(f"NLM download_quiz {artifact_nlm_id}: {e}")
                return None

        if artifact_type == "flashcards":
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    path = os.path.join(tmpdir, "flashcards.md")
                    await client.artifacts.download_flashcards(
                        notebook_nlm_id, path, artifact_id=artifact_nlm_id, output_format="markdown"
                    )
                    return open(path, encoding="utf-8").read()
            except Exception as e:
                logger.warning(f"NLM download_flashcards {artifact_nlm_id}: {e}")
                return None

        if artifact_type == "data_table":
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    path = os.path.join(tmpdir, "table.csv")
                    await client.artifacts.download_data_table(
                        notebook_nlm_id, path, artifact_id=artifact_nlm_id
                    )
                    return open(path, encoding="utf-8-sig").read()
            except Exception as e:
                logger.warning(f"NLM download_data_table {artifact_nlm_id}: {e}")
                return None

        if artifact_type == "report":
            try:
                with tempfile.TemporaryDirectory() as tmpdir:
                    path = os.path.join(tmpdir, "report.md")
                    await client.artifacts.download_report(
                        notebook_nlm_id, path, artifact_id=artifact_nlm_id
                    )
                    return open(path, encoding="utf-8").read()
            except Exception as e:
                logger.warning(f"NLM download_report {artifact_nlm_id}: {e}")
                return None

    return None


async def get_artifact_bytes(
    notebook_nlm_id: str,
    artifact_nlm_id: str,
    artifact_type: str,
) -> Optional[bytes]:
    """Download binary artifact bytes (audio, video, infographic, slide_deck)."""
    import os
    import tempfile
    _ext = {"audio": "mp3", "video": "mp4", "infographic": "png", "slide_deck": "pdf"}
    ext = _ext.get(artifact_type, "bin")
    async with await get_client() as client:
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                path = os.path.join(tmpdir, f"artifact.{ext}")
                if artifact_type == "audio":
                    await client.artifacts.download_audio(notebook_nlm_id, path, artifact_id=artifact_nlm_id)
                elif artifact_type == "video":
                    await client.artifacts.download_video(notebook_nlm_id, path, artifact_id=artifact_nlm_id)
                elif artifact_type == "infographic":
                    await client.artifacts.download_infographic(notebook_nlm_id, path, artifact_id=artifact_nlm_id)
                elif artifact_type == "slide_deck":
                    await client.artifacts.download_slide_deck(notebook_nlm_id, path, artifact_id=artifact_nlm_id)
                else:
                    return None
                return open(path, "rb").read()
        except Exception as e:
            logger.warning(f"NLM get_artifact_bytes {artifact_nlm_id}: {e}")
    return None


# ---------------------------------------------------------------------------
# MIME type helpers
# ---------------------------------------------------------------------------

ARTIFACT_MIME: dict[str, str] = {
    "audio": "audio/mpeg",
    "video": "video/mp4",
    "infographic": "image/png",
    "slide_deck": "application/pdf",
}

ARTIFACT_EXT: dict[str, str] = {
    "audio": "mp3",
    "video": "mp4",
    "infographic": "png",
    "slide_deck": "pdf",
}

TEXT_ARTIFACT_TYPES = {"report", "quiz", "flashcards", "data_table"}
BINARY_ARTIFACT_TYPES = {"audio", "video", "infographic", "slide_deck"}


# ---------------------------------------------------------------------------
# Passthrough NLM API helpers (no Arkon DB involvement)
# ---------------------------------------------------------------------------

def _nb_to_dict(nb) -> dict:
    return {
        "id": nb.id,
        "title": nb.title,
        "sources_count": nb.sources_count,
        "created_at": nb.created_at.isoformat() if nb.created_at else None,
        "is_owner": nb.is_owner,
    }


def _source_to_dict(s) -> dict:
    return {
        "id": s.id,
        "title": s.title,
        "url": s.url,
        "kind": s.kind.value,
        "status": s.status,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


def _artifact_to_nlm_dict(a) -> dict:
    kind_str = a.kind.value
    return {
        "id": a.id,
        "title": a.title,
        "kind": kind_str,
        "status": a.status,
        "status_str": a.status_str,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "url": a.url,
        "report_subtype": a.report_subtype,
        "can_add_to_wiki": kind_str in TEXT_ARTIFACT_TYPES and a.status == 3,
        "is_binary": kind_str in BINARY_ARTIFACT_TYPES,
    }


async def list_nlm_notebooks() -> list[dict]:
    async with await get_client() as client:
        notebooks = await client.notebooks.list()
        return [_nb_to_dict(nb) for nb in notebooks]


async def create_nlm_notebook(title: str) -> dict:
    async with await get_client() as client:
        nb = await client.notebooks.create(title=title)
        return _nb_to_dict(nb)


async def delete_nlm_notebook(notebook_id: str) -> bool:
    """Delete a notebook from the shared NotebookLM account.

    Errors propagate. This used to catch Exception and return False, which collapsed an
    expired Google session, a network failure and a genuine refusal into one
    indistinguishable falsey value — and the route answered 204 regardless, so the UI
    removed the notebook from the list while it still existed in Google. The caller needs
    the exception to tell 401-reconnect apart from 502-upstream-failed.
    """
    async with await get_client() as client:
        return await client.notebooks.delete(notebook_id)


async def list_nlm_sources(notebook_id: str) -> list[dict]:
    async with await get_client() as client:
        sources = await client.sources.list(notebook_id)
        return [_source_to_dict(s) for s in sources]


async def add_nlm_source_url(notebook_id: str, url: str) -> dict:
    async with await get_client() as client:
        source = await client.sources.add_url(notebook_id, url, wait=False)
        return _source_to_dict(source)


async def add_nlm_source_text(notebook_id: str, title: str, content: str) -> dict:
    async with await get_client() as client:
        source = await client.sources.add_text(notebook_id, title=title, content=content, wait=False)
        return _source_to_dict(source)


async def add_nlm_source_file(
    notebook_id: str,
    file_path,
    mime_type: Optional[str] = None,
    title: Optional[str] = None,
) -> dict:
    """Upload a local file (PDF, DOCX, markdown, etc.) as a notebook source."""
    from pathlib import Path as _Path
    async with await get_client() as client:
        source = await client.sources.add_file(
            notebook_id,
            _Path(file_path),
            mime_type=mime_type,
            wait=False,
            title=title,
        )
        return _source_to_dict(source)


async def add_nlm_source_drive(
    notebook_id: str,
    file_id: str,
    title: str,
    mime_type: str = "application/vnd.google-apps.document",
) -> dict:
    """Add a Google Drive document (Docs/Slides/Sheets) as a notebook source."""
    async with await get_client() as client:
        source = await client.sources.add_drive(
            notebook_id,
            file_id=file_id,
            title=title,
            mime_type=mime_type,
            wait=False,
        )
        return _source_to_dict(source)


async def get_artifact_preview_data(
    notebook_nlm_id: str,
    artifact_nlm_id: str,
    artifact_type: str,
) -> Optional[dict]:
    """Return structured preview data for a completed artifact."""
    import json
    import os
    import tempfile

    async with await get_client() as client:
        if artifact_type == "quiz":
            with tempfile.TemporaryDirectory() as tmpdir:
                path = os.path.join(tmpdir, "quiz.json")
                await client.artifacts.download_quiz(
                    notebook_nlm_id, path, artifact_id=artifact_nlm_id, output_format="json"
                )
                data = json.loads(open(path, encoding="utf-8").read())
            return {
                "kind": "quiz",
                "title": data.get("title", "Quiz"),
                "questions": [
                    {
                        "question": q.get("question", ""),
                        "options": [
                            {"text": opt.get("text", ""), "correct": bool(opt.get("isCorrect"))}
                            for opt in q.get("answerOptions", [])
                        ],
                        "hint": q.get("hint", ""),
                    }
                    for q in data.get("questions", [])
                ],
            }

        if artifact_type == "flashcards":
            with tempfile.TemporaryDirectory() as tmpdir:
                path = os.path.join(tmpdir, "flashcards.json")
                await client.artifacts.download_flashcards(
                    notebook_nlm_id, path, artifact_id=artifact_nlm_id, output_format="json"
                )
                data = json.loads(open(path, encoding="utf-8").read())
            return {
                "kind": "flashcards",
                "title": data.get("title", "Flashcards"),
                "cards": data.get("cards", []),  # already {"front": ..., "back": ...}
            }

        if artifact_type == "report":
            with tempfile.TemporaryDirectory() as tmpdir:
                path = os.path.join(tmpdir, "report.md")
                await client.artifacts.download_report(
                    notebook_nlm_id, path, artifact_id=artifact_nlm_id
                )
                markdown = open(path, encoding="utf-8").read()
            return {"kind": "report", "markdown": markdown}

        if artifact_type == "data_table":
            with tempfile.TemporaryDirectory() as tmpdir:
                path = os.path.join(tmpdir, "table.csv")
                await client.artifacts.download_data_table(
                    notebook_nlm_id, path, artifact_id=artifact_nlm_id
                )
                csv_content = open(path, encoding="utf-8-sig").read()
            return {"kind": "data_table", "csv": csv_content}

    return None


async def delete_nlm_source(notebook_id: str, source_id: str) -> bool:
    try:
        async with await get_client() as client:
            return await client.sources.delete(notebook_id, source_id)
    except Exception as e:
        logger.warning(f"NLM delete source {source_id}: {e}")
        return False


async def list_nlm_artifacts(notebook_id: str) -> list[dict]:
    async with await get_client() as client:
        artifacts = await client.artifacts.list(notebook_id)
        return [_artifact_to_nlm_dict(a) for a in artifacts]


async def generate_nlm_artifact(
    notebook_id: str,
    artifact_type: str,
    report_format: Optional[str] = None,
    instructions: Optional[str] = None,
) -> dict:
    from notebooklm.rpc import ReportFormat

    ins = instructions.strip() if instructions and instructions.strip() else None

    async with await get_client() as client:
        if artifact_type == "audio":
            gen = await client.artifacts.generate_audio(notebook_id, instructions=ins)
        elif artifact_type == "video":
            gen = await client.artifacts.generate_video(notebook_id, instructions=ins)
        elif artifact_type == "report":
            if report_format == "custom":
                gen = await client.artifacts.generate_report(
                    notebook_id,
                    report_format=ReportFormat.CUSTOM,
                    custom_prompt=ins,
                )
            else:
                fmt_map = {
                    "briefing_doc": ReportFormat.BRIEFING_DOC,
                    "study_guide": ReportFormat.STUDY_GUIDE,
                    "blog_post": ReportFormat.BLOG_POST,
                }
                fmt = fmt_map.get(report_format or "", ReportFormat.BRIEFING_DOC)
                gen = await client.artifacts.generate_report(
                    notebook_id,
                    report_format=fmt,
                    extra_instructions=ins,
                )
        elif artifact_type == "quiz":
            gen = await client.artifacts.generate_quiz(notebook_id, instructions=ins)
        elif artifact_type == "flashcards":
            gen = await client.artifacts.generate_flashcards(notebook_id, instructions=ins)
        elif artifact_type == "slide_deck":
            gen = await client.artifacts.generate_slide_deck(notebook_id, instructions=ins)
        elif artifact_type == "infographic":
            gen = await client.artifacts.generate_infographic(notebook_id, instructions=ins)
        elif artifact_type == "data_table":
            gen = await client.artifacts.generate_data_table(notebook_id, instructions=ins)
        else:
            raise ValueError(f"Unknown artifact type: {artifact_type}")
        return {"task_id": gen.task_id, "status": gen.status}


async def nlm_chat_ask(
    notebook_id: str,
    question: str,
    conversation_id: Optional[str] = None,
) -> dict:
    from notebooklm import NotebookLMClient
    storage = _storage_path()
    state_file = (storage / "storage_state.json") if storage is not None else None
    async with await NotebookLMClient.from_storage(path=state_file, timeout=120) as client:
        # NotebookLM's chat API expects an explicit source selection.  An empty
        # list is not consistently treated as "all sources" and can make the UI
        # ask the user to select a source before it will answer.  Arkon owns this
        # selection: use every source that has finished processing.
        sources = await client.sources.list(notebook_id)
        source_ids = [source.id for source in sources if source.status == 2]
        if not source_ids:
            raise ValueError(
                "This notebook has no ready sources yet. Add a source or wait for processing to finish."
            )

        result = await client.chat.ask(
            notebook_id, question,
            conversation_id=conversation_id,
            source_ids=source_ids,
        )
        return {
            "answer": result.answer,
            "conversation_id": result.conversation_id,
            "turn_number": result.turn_number,
            "is_follow_up": result.is_follow_up,
            "references": [
                {
                    "source_id": ref.source_id,
                    "citation_number": ref.citation_number,
                    "cited_text": ref.cited_text,
                }
                for ref in result.references
            ],
        }
