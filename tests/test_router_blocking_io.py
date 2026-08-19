"""No synchronous I/O may be reached directly from a router coroutine (issue #30).

Per-call-site tests would not have caught this: the bug was ~25 individual calls spread
over eight files, and the codebase already had the right pattern
(`storage_service.upload_stream_async`, `skill_service`'s two `asyncio.to_thread` calls)
and simply did not apply it in the routers — `grep asyncio.to_thread app/routers/` returned
nothing. So this walks the AST of every router instead, which means a new handler is
covered the moment it is written and a reverted offload fails here rather than as a
production outage.

The failure mode being guarded: the MinIO SDK is urllib3-based and fully synchronous, so a
single 300 MB `put_object` held the event loop for the whole transfer and every other
request in the process — `/health` included — stalled behind it. bcrypt at cost 12 is
~250 ms of uninterruptible CPU, and `socket.getaddrinfo` blocks in C with no timeout of
its own.
"""

import ast
import importlib
import inspect
import textwrap
from pathlib import Path
from tempfile import SpooledTemporaryFile

import pytest
from fastapi import HTTPException, UploadFile

REPO_ROOT = Path(__file__).resolve().parent.parent
ROUTER_DIR = REPO_ROOT / "app" / "routers"

ROUTER_FILES = sorted(p for p in ROUTER_DIR.glob("*.py") if p.name != "__init__.py")

# Synchronous helpers that block the event loop when invoked inline. Names are matched on
# the final attribute, so `storage_service.upload_file` and `self.upload_file` both count.
BLOCKING_CALLS = frozenset({
    # MinIO SDK — urllib3, no async transport. Each of these has an `*_async` twin on
    # StorageService; a router must call that instead.
    "ensure_bucket_sync",
    "upload_file",
    "upload_stream",
    "download_file",
    "get_presigned_url",
    "presigned_get_object",
    "delete_object",
    "delete_prefix",
    "copy_object",
    "copy_prefix",
    "move_prefix",
    "calculate_prefix_hash",
    "stat_object",
    "put_object",
    "get_object",
    "bucket_exists",
    "make_bucket",
    "remove_object",
    # bcrypt at cost 12
    "hash_password",
    "verify_password",
    # blocking DNS
    "getaddrinfo",
})

# Calls that return a lazy iterator: they perform no I/O until something drains them, so it
# is the *drain* that has to be offloaded, not the call. Draining a list_objects generator
# on the loop was the half of this bug that a naive "wrap the call" fix would have missed.
LAZY_PRODUCERS = frozenset({"list_objects"})

# Callables that move work off the loop. `to_thread` is the codebase's idiom.
OFFLOADERS = frozenset({"to_thread", "run_in_executor", "run_in_threadpool"})


def _callee_name(call: ast.Call) -> "str | None":
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _violations_in(source: str, label: str) -> list[str]:
    """Blocking calls in `source` that still run on the event loop."""
    found: list[str] = []

    def walk(node: ast.AST, inside_offloader: bool) -> None:
        offloaded_children = inside_offloader
        if isinstance(node, ast.Call):
            name = _callee_name(node)
            if name in LAZY_PRODUCERS:
                # Legal only when the surrounding drain is what got offloaded.
                if not inside_offloader:
                    found.append(f"{label}:{node.lineno} {name}() drained on the loop")
            elif name in BLOCKING_CALLS:
                # A reference handed to to_thread is an attribute, not a call, so any call
                # node at all means this one executes inline. Arguments to to_thread are
                # evaluated on the loop before it is invoked, which is why being "inside"
                # an offloader does not excuse a non-lazy call.
                found.append(f"{label}:{node.lineno} {name}() called on the loop")
            elif name in OFFLOADERS:
                offloaded_children = True

        for child in ast.iter_child_nodes(node):
            walk(child, offloaded_children)

    walk(ast.parse(source), False)
    return found


def _violations(path: Path) -> list[str]:
    return _violations_in(path.read_text(encoding="utf-8"), path.name)


@pytest.mark.parametrize("router", ROUTER_FILES, ids=lambda p: p.name)
def test_router_never_blocks_the_event_loop(router):
    offenders = _violations(router)
    assert not offenders, (
        "blocking I/O reached from a coroutine — wrap it in asyncio.to_thread "
        "(or await the *_async wrapper):\n  " + "\n  ".join(offenders)
    )


def test_the_scan_actually_reaches_the_routers_that_had_the_bug():
    """Stop the check passing because a path or filter change left it with nothing to scan."""
    scanned = {p.name for p in ROUTER_FILES}
    for name in (
        "sources.py",
        "skills.py",
        "skill_contributions.py",
        "projects.py",
        "wiki_images.py",
        "auth.py",
        "rbac.py",
        "admin_settings.py",
    ):
        assert name in scanned, f"{name} is no longer being scanned"


def test_the_scan_flags_the_code_it_replaced():
    """A detector that cannot fail proves nothing, so run it against the reverted form."""
    reverted = (
        "async def handler(storage_service, prefix, password):\n"
        "    storage_service.upload_file('k', b'')\n"
        "    for obj in storage_service.list_objects(prefix):\n"
        "        storage_service.download_file(obj.object_name)\n"
        "    return hash_password(password)\n"
    )
    flagged = _violations_in(reverted, "reverted")
    assert len(flagged) == 4, flagged


def test_the_scan_accepts_the_offloaded_form():
    """And must not flag the shape the routers now use, including the generator drain."""
    fixed = (
        "async def handler(storage_service, prefix, password):\n"
        "    await asyncio.to_thread(storage_service.upload_file, 'k', b'')\n"
        "    objects = await asyncio.to_thread(\n"
        "        list, storage_service.list_objects(prefix)\n"
        "    )\n"
        "    for obj in objects:\n"
        "        await asyncio.to_thread(storage_service.download_file, obj.object_name)\n"
        "    return await asyncio.to_thread(hash_password, password)\n"
    )
    assert _violations_in(fixed, "fixed") == []


# --------------------------------------------------------------------------- #
# Upload size cap (issue #31) — every entry point, not just the ones reviewed
# --------------------------------------------------------------------------- #

# Any of these means the handler is bounded. `_assert_archive_within_limit` is skills.py's
# thin wrapper for the three endpoints that hand the UploadFile on to SkillService.
GUARDS = (
    "read_upload_bounded",
    "spooled_upload",
    "assert_upload_within_limit",
    "_assert_archive_within_limit",
)


def _upload_handlers():
    """Every router coroutine that accepts an UploadFile."""
    for path in ROUTER_FILES:
        module = importlib.import_module(f"app.routers.{path.stem}")
        for name, obj in vars(module).items():
            if not inspect.iscoroutinefunction(obj):
                continue
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            annotations = getattr(obj, "__annotations__", {})
            if any("UploadFile" in str(a) for a in annotations.values()):
                yield f"{path.stem}.{name}", obj


def test_every_upload_entry_point_is_size_capped():
    unbounded = [
        name
        for name, fn in _upload_handlers()
        if not any(guard in inspect.getsource(fn) for guard in GUARDS)
    ]
    assert not unbounded, (
        "upload handler(s) read the body with no byte cap — nginx allows 500 MB and the "
        f"API is one uvicorn process: {unbounded}"
    )


def test_the_upload_scan_found_the_upload_handlers():
    found = {name for name, _ in _upload_handlers()}
    assert len(found) >= 6, f"expected the whole upload surface, found {sorted(found)}"
    assert "sources.upload_source" in found
    assert "sources.upload_zip_archive" in found


def test_upload_handlers_stream_rather_than_buffering_the_body_twice():
    """`await file.read()` was one full copy and upload_file's io.BytesIO(data) a second.

    Five concurrent 500 MB uploads therefore held ~2.5 GB of bytes plus ~2.5 GB of copies
    and OOM-killed the API container, dropping every in-flight request for every user.
    Starlette has already spooled the body to disk, so the handle goes straight to MinIO.
    """
    from app.routers.projects import upload_workspace_source
    from app.routers.skill_contributions import upload_skill_contribution_file
    from app.routers.sources import upload_source

    for fn in (upload_source, upload_workspace_source, upload_skill_contribution_file):
        src = inspect.getsource(fn)
        assert "upload_stream_async" in src, f"{fn.__name__} no longer streams to storage"
        assert "read_upload_bounded" not in src, (
            f"{fn.__name__} buffers the whole body again"
        )


def test_contribution_upload_does_not_store_the_client_content_type():
    """A contributor could set content_type: text/html on an uploaded asset."""
    from app.routers.skill_contributions import upload_skill_contribution_file

    src = inspect.getsource(upload_skill_contribution_file)
    # Read the AST rather than the text, so the comment explaining the fix — which names
    # the old expression — cannot satisfy or break the assertion.
    tree = ast.parse(textwrap.dedent(src))
    reads_client_type = any(
        isinstance(node, ast.Attribute)
        and node.attr == "content_type"
        and isinstance(node.value, ast.Name)
        and node.value.id == "file"
        for node in ast.walk(tree)
    )
    assert not reads_client_type, "the uploader's declared content type is stored again"
    assert "content_type_from_extension" in src


# --------------------------------------------------------------------------- #
# upload_guard behaviour
# --------------------------------------------------------------------------- #

def _upload(data: bytes, *, filename: str = "doc.pdf", declare_size: bool = True):
    """An UploadFile shaped the way Starlette's multipart parser builds one."""
    spool = SpooledTemporaryFile(max_size=1024)
    spool.write(data)
    spool.seek(0)
    return UploadFile(
        file=spool,
        size=len(data) if declare_size else None,
        filename=filename,
    )


def test_oversized_upload_is_rejected_with_413():
    from app.services.upload_guard import assert_upload_within_limit

    with pytest.raises(HTTPException) as exc:
        assert_upload_within_limit(_upload(b"a" * (2 * 1024 * 1024)), limit_mb=1)
    assert exc.value.status_code == 413


def test_upload_length_is_measured_without_reading_the_body():
    """The guard must work for handlers that pass the UploadFile further down untouched."""
    from app.services.upload_guard import assert_upload_within_limit

    upload = _upload(b"a" * 4096, declare_size=False)
    assert assert_upload_within_limit(upload, limit_mb=1) == 4096
    assert upload.file.read() == b"a" * 4096, "the guard consumed the stream"


@pytest.mark.asyncio
async def test_spooled_upload_returns_starlettes_own_handle():
    from app.services.upload_guard import spooled_upload

    upload = _upload(b"b" * 4096)
    stream, length = await spooled_upload(upload)

    assert stream is upload.file, "a copy was made instead of reusing the spooled file"
    assert length == 4096
    assert stream.read() == b"b" * 4096, "handle was not rewound before hand-off"


def test_stored_content_type_comes_from_the_extension_allowlist():
    from app.services.upload_guard import content_type_from_extension

    assert content_type_from_extension("report.pdf") == "application/pdf"
    # Not on the allowlist: an uploader must not be able to get text/html stored.
    assert content_type_from_extension("payload.html") == "application/octet-stream"
    assert content_type_from_extension("no-extension") == "application/octet-stream"
