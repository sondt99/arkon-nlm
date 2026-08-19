"""Broad excepts must not report success or "unconfigured" for real failures (issue #89).

Three call sites turned a hard failure into a benign-looking value:

  * `ConfigService._decrypt` was annotated `-> str` and returned None after a SECRET_KEY
    rotation, so every provider silently read as "no API key configured" — indistinguishable
    from a fresh install, with nothing in the logs an admin would connect to it.
  * `notebooklm_service.is_authenticated` swallowed the ImportError from a missing
    notebooklm-py package and reported "not authenticated", sending admins to redo a Google
    login that could never fix it.
  * `PolicyEngine._audit` swallowed audit-log write failures. That whole module was dead
    (issue #89 item 7) and is deleted, so the security event has nowhere left to vanish.

`delete_nlm_notebook` was fixed earlier; the check here is behavioural rather than the
`inspect.getsource` string assertion in test_notebooklm_ownership.py, which a commented-out
`except Exception` would still satisfy.
"""

import ast
import pathlib
import uuid

import pytest
from cryptography.fernet import Fernet

from app.database.models import AppConfig
from app.services import notebooklm_service
from app.services.config_service import (
    ConfigDecryptionError,
    ConfigService,
    _derive_fernet_key,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

SENSITIVE_KEY = "llm_api_key__openai"


def _encrypted_under_another_secret(plaintext: str = "sk-live-123") -> str:
    """A value this process's SECRET_KEY cannot decrypt — i.e. a rotated key."""
    other = Fernet(_derive_fernet_key("a-completely-different-secret"))
    return other.encrypt(plaintext.encode()).decode()


# --------------------------------------------------------------------------- #
# ConfigService
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_an_undecryptable_secret_raises_instead_of_reading_as_absent(fake_db):
    fake_db.insert(AppConfig(key=SENSITIVE_KEY, value=_encrypted_under_another_secret()))
    svc = ConfigService(fake_db.factory())

    with pytest.raises(ConfigDecryptionError) as exc:
        await svc.get(SENSITIVE_KEY)

    assert SENSITIVE_KEY in str(exc.value), "the error does not say which key is broken"
    assert "SECRET_KEY" in str(exc.value)


@pytest.mark.asyncio
async def test_a_readable_secret_still_round_trips(fake_db):
    svc = ConfigService(fake_db.factory())
    await svc.set(SENSITIVE_KEY, "sk-live-123")

    stored = fake_db.table_rows("app_config")[0].value
    assert stored != "sk-live-123", "the API key is being stored in plaintext"
    assert await svc.get(SENSITIVE_KEY) == "sk-live-123"


@pytest.mark.asyncio
async def test_the_settings_page_still_renders_and_names_the_broken_key(fake_db):
    """The UI is the only place the key can be repaired, so it must not 500."""
    fake_db.insert(AppConfig(key=SENSITIVE_KEY, value=_encrypted_under_another_secret()))
    svc = ConfigService(fake_db.factory())

    ui = await svc.get_all_for_ui()

    assert ui[SENSITIVE_KEY] is None
    assert ui[f"{SENSITIVE_KEY}_configured"] is True
    assert ui[f"{SENSITIVE_KEY}_decrypt_error"] is True
    # An unaffected key must still come through normally.
    assert "llm_provider" in ui


@pytest.mark.asyncio
async def test_a_healthy_secret_is_masked_and_not_flagged(fake_db):
    svc = ConfigService(fake_db.factory())
    await svc.set(SENSITIVE_KEY, "sk-live-abcdef123456")

    ui = await svc.get_all_for_ui()

    assert ui[SENSITIVE_KEY] == "•" * 8 + "3456"
    assert ui[f"{SENSITIVE_KEY}_configured"] is True
    assert f"{SENSITIVE_KEY}_decrypt_error" not in ui


@pytest.mark.asyncio
async def test_get_all_does_not_hide_the_failure(fake_db):
    """get_all() feeds providers, so an unreadable secret has to surface as an error."""
    fake_db.insert(AppConfig(key=SENSITIVE_KEY, value=_encrypted_under_another_secret()))
    svc = ConfigService(fake_db.factory())

    with pytest.raises(ConfigDecryptionError):
        await svc.get_all()


# --------------------------------------------------------------------------- #
# notebooklm_service
# --------------------------------------------------------------------------- #

class _Client:
    def __init__(self, notebooks):
        self.notebooks = notebooks

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _Notebooks:
    def __init__(self, on_list=None, on_delete=None):
        self._on_list = on_list
        self._on_delete = on_delete

    async def list(self):
        if self._on_list is not None:
            raise self._on_list
        return []

    async def delete(self, _nlm_id):
        if self._on_delete is not None:
            raise self._on_delete
        return True


def _patch_client(monkeypatch, notebooks=None, get_client_error=None):
    async def _get_client():
        if get_client_error is not None:
            raise get_client_error
        return _Client(notebooks or _Notebooks())

    monkeypatch.setattr(notebooklm_service, "get_client", _get_client)


@pytest.mark.asyncio
async def test_a_missing_package_is_not_reported_as_logged_out(monkeypatch):
    _patch_client(monkeypatch, get_client_error=ImportError("notebooklm-py is not installed"))

    with pytest.raises(ImportError):
        await notebooklm_service.is_authenticated()


@pytest.mark.asyncio
async def test_an_expired_session_is_still_reported_as_logged_out(monkeypatch):
    _patch_client(monkeypatch, notebooks=_Notebooks(on_list=RuntimeError("AuthError")))

    assert await notebooklm_service.is_authenticated() is False


@pytest.mark.asyncio
async def test_a_live_session_reports_authenticated(monkeypatch):
    _patch_client(monkeypatch)

    assert await notebooklm_service.is_authenticated() is True


@pytest.mark.asyncio
async def test_a_failed_remote_delete_is_not_reported_as_deleted(monkeypatch):
    """Returning False here made the route answer 204 while Google still had the notebook."""
    _patch_client(monkeypatch, notebooks=_Notebooks(on_delete=RuntimeError("upstream 500")))

    with pytest.raises(RuntimeError):
        await notebooklm_service.delete_nlm_notebook(str(uuid.uuid4()))


# --------------------------------------------------------------------------- #
# Dead service entry points
# --------------------------------------------------------------------------- #

def _python_sources() -> list[pathlib.Path]:
    return [
        p for p in (REPO_ROOT / "app").rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def test_the_legacy_policy_engine_is_gone():
    """It had no callers, and its _audit silently dropped audit-log write failures."""
    assert not (REPO_ROOT / "app" / "services" / "policy_engine.py").exists()

    with pytest.raises(ModuleNotFoundError):
        __import__("app.services.policy_engine")


def test_nothing_still_imports_the_policy_engine():
    """AST, so a mention inside a comment or docstring does not count either way."""
    offenders = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(
                "policy_engine"
            ):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
            elif isinstance(node, ast.Import):
                offenders.extend(
                    f"{path.relative_to(REPO_ROOT)}:{node.lineno}"
                    for alias in node.names
                    if alias.name.endswith("services.policy_engine")
                )
    assert offenders == []


def test_the_duplicate_ingestion_pipeline_is_gone():
    """app/worker.py's ingest_file_task is the only path a source takes."""
    from app.services import kb_service

    assert not hasattr(kb_service, "ingest_source")
    assert not hasattr(kb_service, "_parse_vision_response")

    # The helpers the worker imports must survive.
    for name in ("_extract_text_from_file", "_inline_image_markers", "_guess_content_type"):
        assert hasattr(kb_service, name), f"{name} is imported by app/worker.py"


def test_the_worker_still_imports_only_what_kb_service_kept():
    """A deletion that broke the worker's imports would only show up at task runtime."""
    import app.worker as worker

    tree = ast.parse(
        pathlib.Path(worker.__file__).read_text(encoding="utf-8"), filename=worker.__file__
    )
    from app.services import kb_service

    wanted = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and (node.module or "").endswith("services.kb_service")
        for alias in node.names
    }
    assert wanted, "the worker no longer imports kb_service at all — is this test stale?"
    missing = sorted(n for n in wanted if not hasattr(kb_service, n))
    assert missing == [], f"app/worker.py imports names kb_service no longer has: {missing}"
