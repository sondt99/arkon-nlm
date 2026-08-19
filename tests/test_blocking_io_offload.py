"""Blocking work must not run on the event loop (issue #30).

The MinIO SDK is urllib3, bcrypt at cost 12 is ~250 ms of CPU, and PyMuPDF/OCR is
CPU-bound inside a C extension. All three used to be called inline from coroutines, so a
single 300 MB upload held the loop for the whole transfer — every other request in the
process, `/health` included, stalled behind it — and one 300-page scanned PDF put the arq
worker into minutes of uninterruptible OCR, during which its `health_check_interval = 30`
heartbeat could not write its Redis key. The container was then marked unhealthy after
~90 s, which breaks `depends_on: service_healthy` for everything behind it.

Two kinds of assertion below:

  * behavioural — a 1 ms ticker must keep getting scheduled while a deliberately slow
    storage call is in flight. This is what actually distinguishes "offloaded" from
    "looks awaitable but isn't", which was the exact shape of the old `ensure_bucket`.
  * structural — no `async def` under app/services or in app/worker.py may call a known
    blocking helper directly. This is the half that catches a *new* call site rather than
    a reverted one.
"""

import ast
import asyncio
import inspect
import pathlib
import time

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

# Long enough that a blocked loop is unambiguous, short enough to keep the suite fast.
_SLOW = 0.2


# --------------------------------------------------------------------------- #
# Behavioural: the loop must keep turning while storage I/O is in flight
# --------------------------------------------------------------------------- #

async def _ticks_during(coro):
    """Await `coro` while counting how often a 1 ms sleeper gets scheduled.

    A coroutine that blocks the loop yields zero ticks, because the ticker task never
    gets a chance to run between `create_task` and the end of the blocking body.
    """
    ticks = 0
    stop = False

    async def ticker():
        nonlocal ticks
        while not stop:
            await asyncio.sleep(0.001)
            ticks += 1

    task = asyncio.create_task(ticker())
    result = await coro
    stop = True
    await task
    return result, ticks


class _Obj:
    def __init__(self, name):
        self.object_name = name
        self.etag = "etag"


class _SlowClient:
    """Stand-in for the MinIO client where every operation blocks the calling thread."""

    def put_object(self, **kwargs):
        time.sleep(_SLOW)

    def get_object(self, bucket, key):
        class _Resp:
            def read(self):
                # get_object only sends the request; the body is streamed by read(). An
                # offload that covers only the former moves nothing off the loop.
                time.sleep(_SLOW)
                return b"payload"

            def close(self):
                pass

            def release_conn(self):
                pass

        return _Resp()

    def list_objects(self, bucket, prefix=None, recursive=True):
        # minio's list_objects is lazy: the paginated requests happen while the generator
        # is drained, not when it is created.
        def _gen():
            for i in range(3):
                time.sleep(_SLOW / 3)
                yield _Obj(f"{prefix}f{i}")

        return _gen()


def _service_with_slow_client():
    from app.services.storage_service import StorageService

    svc = StorageService.__new__(StorageService)  # no MinIO connection needed
    object.__setattr__(svc, "_client", _SlowClient())
    return svc


@pytest.mark.asyncio
async def test_upload_file_async_keeps_the_loop_free():
    svc = _service_with_slow_client()
    key, ticks = await _ticks_during(svc.upload_file_async("k", b"data"))
    assert key == "k"
    assert ticks > 10, f"put_object ran on the event loop (only {ticks} ticks)"


@pytest.mark.asyncio
async def test_download_file_async_offloads_the_response_read():
    svc = _service_with_slow_client()
    data, ticks = await _ticks_during(svc.download_file_async("k"))
    assert data == b"payload"
    assert ticks > 10, f"the response body was read on the event loop (only {ticks} ticks)"


@pytest.mark.asyncio
async def test_list_objects_async_drains_the_generator_inside_the_thread():
    """Wrapping only the call that *returns* the generator moves nothing off the loop."""
    svc = _service_with_slow_client()
    objects, ticks = await _ticks_during(svc.list_objects_async("prefix/"))

    assert isinstance(objects, list), (
        "list_objects_async handed back a lazy generator — draining it would block "
        "whatever loop the caller is on"
    )
    assert len(objects) == 3
    assert ticks > 10, f"the listing was drained on the event loop (only {ticks} ticks)"


@pytest.mark.asyncio
async def test_ensure_bucket_actually_awaits_something():
    """This one was an `async def` with a fully blocking body — the worst variant."""
    from app.services.storage_service import StorageService

    svc = StorageService.__new__(StorageService)

    class _BucketClient:
        def bucket_exists(self, bucket):
            time.sleep(_SLOW)
            return True

    object.__setattr__(svc, "_client", _BucketClient())

    _, ticks = await _ticks_during(svc.ensure_bucket())
    assert ticks > 10, f"ensure_bucket still blocks the loop it runs on ({ticks} ticks)"


@pytest.mark.asyncio
async def test_verify_password_async_keeps_the_loop_free(monkeypatch):
    from app.services import auth_service

    def _slow_verify(password, password_hash):
        time.sleep(_SLOW)
        return True

    monkeypatch.setattr(auth_service, "verify_password", _slow_verify)

    ok, ticks = await _ticks_during(auth_service.verify_password_async("pw", "hash"))
    assert ok is True
    assert ticks > 10, f"bcrypt ran on the event loop (only {ticks} ticks)"


@pytest.mark.asyncio
async def test_async_password_helpers_agree_with_the_sync_ones():
    import bcrypt

    from app.services.auth_service import verify_password, verify_password_async

    # Cost 4 keeps this fast. The wrapper is under test here, not bcrypt's work factor.
    hashed = bcrypt.hashpw(b"correct horse", bcrypt.gensalt(4)).decode("utf-8")

    assert await verify_password_async("correct horse", hashed) is True
    assert verify_password("correct horse", hashed) is True
    assert await verify_password_async("wrong", hashed) is False


def test_sync_password_helpers_are_still_available():
    """The worker, startup seeding and scripts are sync contexts and still need these."""
    from app.services import auth_service

    assert not inspect.iscoroutinefunction(auth_service.hash_password)
    assert not inspect.iscoroutinefunction(auth_service.verify_password)
    assert inspect.iscoroutinefunction(auth_service.hash_password_async)
    assert inspect.iscoroutinefunction(auth_service.verify_password_async)


def test_authenticate_employee_uses_the_async_bcrypt_path():
    from app.services import auth_service

    src = inspect.getsource(auth_service.authenticate_employee)
    assert "await verify_password_async(" in src, (
        "the login path calls bcrypt inline again — ~4 concurrent logins saturate the loop"
    )


# --------------------------------------------------------------------------- #
# CPU-bound extraction: the worker's health heartbeat depends on this
# --------------------------------------------------------------------------- #

def test_pdf_extraction_runs_off_the_loop():
    from app.services import kb_service

    src = inspect.getsource(kb_service._extract_text_from_file)
    assert "asyncio.to_thread(_extract_pdf_pages" in src
    assert "asyncio.to_thread(_extract_docx_pages" in src
    assert "fitz" not in src, "PyMuPDF is back inside the coroutine body"
    assert "mammoth" not in src, "mammoth is back inside the coroutine body"

    # The 300-dpi OCR must still be there — just in the thread-side helper.
    assert "get_textpage_ocr" in inspect.getsource(kb_service._extract_pdf_pages)


def test_worker_file_ingest_offloads_download_and_image_extraction():
    """A picture-heavy document must not starve the loop arq's heartbeat runs on."""
    import app.worker as worker

    src = inspect.getsource(worker.ingest_file_task)
    assert "download_file_async(" in src
    assert "storage_service.download_file(" not in src
    assert "asyncio.to_thread(" in src
    assert "images = extract_images(" not in src, (
        "extract_images is being called inline again — PyMuPDF decode plus one blocking "
        "put_object per image, on the loop"
    )


def test_worker_skill_ingest_offloads_storage_calls():
    import app.worker as worker

    for task in (worker.ingest_skill_task, worker.delete_skill_task):
        src = inspect.getsource(task)
        for call in ("upload_file(", "delete_prefix(", "calculate_prefix_hash("):
            assert f"storage_service.{call}" not in src, (
                f"{task.__name__} calls the blocking storage_service.{call.rstrip('(')} "
                "directly"
            )


# --------------------------------------------------------------------------- #
# Structural: no coroutine may reach a blocking helper directly
# --------------------------------------------------------------------------- #

# Every sync method on StorageService. Each has an `*_async` twin.
_BLOCKING_STORAGE_METHODS = frozenset({
    "ensure_bucket_sync",
    "upload_file",
    "download_file",
    "upload_stream",
    "get_presigned_url",
    "delete_object",
    "list_objects",
    "delete_prefix",
    "copy_object",
    "copy_prefix",
    "move_prefix",
    "calculate_prefix_hash",
})

_BLOCKING_FUNCTIONS = frozenset({"hash_password", "verify_password"})

# app/routers/ and app/main.py are the other half of issue #30 and are converted
# separately; add them here once they are.
_SCANNED = ["app/worker.py"] + sorted(
    str(p.relative_to(REPO_ROOT)) for p in (REPO_ROOT / "app" / "services").glob("*.py")
)

# Coroutines still known to block, tracked on issue #30. This is a ratchet, not an
# exemption: a *new* offender anywhere in _SCANNED fails the test, and an entry that has
# since been fixed also fails it, so the set cannot rot.
# Known offenders not yet converted. Empty, and it must stay that way: the two assertions
# below are symmetric, so adding an entry is only ever a temporary, visible concession and
# fixing one without removing it here fails the suite.
_PENDING: frozenset[str] = frozenset()


def _offending_calls(tree: ast.AST) -> list[str]:
    """Blocking calls that would execute on the event loop, with their line numbers.

    Descent stops at nested sync `def` and `lambda` boundaries: code there does not run on
    the loop — that is the whole point of handing it to `asyncio.to_thread`. A bare
    reference passed as an argument (`to_thread(storage_service.copy_prefix, ...)`) is an
    ast.Attribute rather than an ast.Call, so it is not a call site and is not flagged.
    """
    found: list[str] = []

    def walk(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Call):
                func = child.func
                if isinstance(func, ast.Attribute) and func.attr in _BLOCKING_STORAGE_METHODS:
                    receiver = getattr(func.value, "id", None)
                    if receiver in ("storage_service", "self"):
                        found.append(f"line {child.lineno}: {receiver}.{func.attr}()")
                elif isinstance(func, ast.Name) and func.id in _BLOCKING_FUNCTIONS:
                    found.append(f"line {child.lineno}: {func.id}()")
            walk(child)

    walk(tree)
    return found


def test_no_coroutine_calls_a_blocking_helper_directly():
    offenders: dict[str, list[str]] = {}

    for rel in _SCANNED:
        path = REPO_ROOT / rel
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef):
                hits = _offending_calls(node)
                if hits:
                    offenders.setdefault(f"{rel}::{node.name}", []).extend(hits)

    new = sorted(f"{key} {hit}" for key, hits in offenders.items()
                 for hit in hits if key not in _PENDING)
    assert not new, (
        "these coroutines block the event loop; use the *_async twin or "
        "asyncio.to_thread:\n  " + "\n  ".join(new)
    )

    fixed = sorted(_PENDING - offenders.keys())
    assert not fixed, (
        "these no longer block — remove them from _PENDING so the ratchet keeps "
        "holding:\n  " + "\n  ".join(fixed)
    )
