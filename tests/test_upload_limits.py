"""Every byte a client can send is now capped somewhere (issue #31).

Three layers, tested separately because each one closes a hole the others cannot reach:

* `BodySizeLimitMiddleware` — route-level guards only run *after* the ASGI server has
  received the whole body, so without this the effective limit on every endpoint was
  nginx's 500 MB. Driven through a real httpx ASGI client rather than by calling the
  middleware directly: a body-size middleware is easy to write in a way that 413s
  everything, or that buffers the body it claims not to buffer, and neither shows up in a
  unit test of the counter.
* `zip_service.extract_zip` — the aggregate 500 MB cap *was* 500 MB of resident memory,
  because every inflated entry was appended to a list. Ten zeroed 50 MB files compress to
  well under 1 MB on the wire, so one small request drove ~500 MB RSS and two concurrent
  requests OOM-killed the container. Nothing router-side could reduce that peak.
* the skill-contribution write paths — the text PUT takes an unbounded JSON string, and
  per-file caps bound one request rather than the workspace.
"""

import ast
import inspect
import io
import tempfile
import textwrap
import tracemalloc
import zipfile
from tempfile import SpooledTemporaryFile
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI, File, HTTPException, Request, UploadFile

from app.config import Settings, settings
from app.database.models import SkillContributionStatus
from app.services.upload_guard import BodySizeLimitMiddleware
from app.services.zip_service import ZipExtractionError, extract_zip

# 1 MiB, so the rejection message reads in whole MB exactly as it does in production.
_LIMIT = 1024 * 1024


# --------------------------------------------------------------------------- #
# Request body size limit — driven through a real ASGI client
# --------------------------------------------------------------------------- #

def _build_app(max_bytes: int = _LIMIT) -> tuple[FastAPI, list]:
    """A minimal app behind the real middleware, plus a log of what its routes saw.

    The log is what distinguishes "rejected" from "rejected after the route already read
    the body", which is the whole point of the middleware.
    """
    seen: list = []
    app = FastAPI()
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)

    @app.post("/echo")
    async def echo(request: Request):
        body = await request.body()
        seen.append(("echo", len(body)))
        return {"received": len(body)}

    @app.post("/upload")
    async def upload(file: UploadFile = File(...)):
        data = await file.read()
        seen.append(("upload", len(data)))
        return {"received": len(data), "filename": file.filename}

    @app.get("/ping")
    async def ping():
        seen.append(("ping", 0))
        return {"ok": True}

    return app, seen


def _client(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://limits.test"
    )


@pytest.mark.asyncio
async def test_body_under_the_cap_reaches_the_route_intact():
    app, seen = _build_app()
    async with _client(app) as client:
        response = await client.post("/echo", content=b"x" * 65_536)

    assert response.status_code == 200
    assert response.json() == {"received": 65_536}
    assert seen == [("echo", 65_536)]


@pytest.mark.asyncio
async def test_body_exactly_at_the_cap_is_allowed():
    """An off-by-one here would reject the largest upload the routes advertise."""
    app, seen = _build_app()
    async with _client(app) as client:
        response = await client.post("/echo", content=b"x" * _LIMIT)

    assert response.status_code == 200
    assert seen == [("echo", _LIMIT)]


@pytest.mark.asyncio
async def test_oversized_content_length_is_rejected_before_the_route_runs():
    app, seen = _build_app()
    async with _client(app) as client:
        request = client.build_request("POST", "/echo", content=b"x" * (2 * _LIMIT))
        assert request.headers["content-length"] == str(2 * _LIMIT)
        response = await client.send(request)

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body exceeds the 1 MB limit."}
    assert seen == [], "the route ran on a body that should never have reached it"


@pytest.mark.asyncio
async def test_content_length_rejection_reads_no_body_at_all():
    """The header check earns its place only by refusing before a byte is read.

    Without it the counter still returns 413, but the client has already been asked for —
    and the process has already handled — a megabyte of body it was always going to refuse.
    """
    app, seen = _build_app()
    consumed = []

    async def chunks():
        for _ in range(8):
            consumed.append(1)
            yield b"y" * 262_144

    async with _client(app) as client:
        # An explicit Content-Length suppresses httpx's chunked framing, so the header is
        # present while the body stays a stream we can observe.
        request = client.build_request(
            "POST", "/echo", content=chunks(), headers={"content-length": str(2 * _LIMIT)}
        )
        assert "transfer-encoding" not in request.headers
        response = await client.send(request)

    assert response.status_code == 413
    assert consumed == [], "the body was read despite an oversized Content-Length"
    assert seen == []


@pytest.mark.asyncio
async def test_oversized_body_without_content_length_is_cut_off_mid_stream():
    """Chunked transfer sends no Content-Length, so the header check cannot fire at all.

    A middleware that only reads the header is trivially bypassed by omitting it, which is
    why the running counter over delivered `http.request` messages is the real enforcement.
    """
    app, seen = _build_app()

    async def chunks():
        for _ in range(8):
            yield b"y" * 262_144  # 2 MiB in total

    async with _client(app) as client:
        request = client.build_request("POST", "/echo", content=chunks())
        assert "content-length" not in request.headers
        assert request.headers["transfer-encoding"] == "chunked"
        response = await client.send(request)

    assert response.status_code == 413
    assert response.json() == {"detail": "Request body exceeds the 1 MB limit."}
    assert seen == []


@pytest.mark.asyncio
async def test_large_but_allowed_multipart_upload_still_succeeds():
    """The case a naive "does it 413?" test would miss: one that rejects everything.

    The ZIP endpoint legitimately accepts 200 MB, so a large multipart body has to reach
    Starlette's parser with every byte intact.
    """
    app, seen = _build_app()
    payload = b"z" * 700_000

    async with _client(app) as client:
        response = await client.post("/upload", files={"file": ("big.bin", payload)})

    assert response.status_code == 200
    assert response.json() == {"received": 700_000, "filename": "big.bin"}
    assert seen == [("upload", 700_000)]


@pytest.mark.asyncio
async def test_oversized_multipart_upload_is_a_413_not_a_500():
    """Cutting the body off mid-parse must not surface as a server error.

    The parser is stopped with a synthetic http.disconnect, which Starlette raises as
    ClientDisconnect; left unhandled that would become a 500 for a request we deliberately
    refused. Streamed with no Content-Length so the counter, not the header, does the work.
    """
    app, seen = _build_app()
    boundary = "----arkonuploadlimits"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="bomb.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()

    async def chunks():
        yield head
        for _ in range(8):
            yield b"z" * 262_144
        yield tail

    async with _client(app) as client:
        request = client.build_request(
            "POST",
            "/upload",
            content=chunks(),
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )
        response = await client.send(request)

    assert response.status_code == 413
    assert seen == []


@pytest.mark.asyncio
async def test_request_without_a_body_is_untouched():
    app, seen = _build_app()
    async with _client(app) as client:
        response = await client.get("/ping")

    assert response.status_code == 200
    assert seen == [("ping", 0)]


@pytest.mark.asyncio
async def test_non_http_scopes_bypass_the_middleware():
    """A lifespan scope carries no headers; reading them would break startup."""
    reached = []

    async def stub(scope, _receive, _send):
        reached.append(scope["type"])

    async def _receive():  # pragma: no cover - never called for a lifespan pass-through
        return {"type": "lifespan.startup"}

    async def _send(_message):  # pragma: no cover - same
        return None

    middleware = BodySizeLimitMiddleware(stub, max_bytes=_LIMIT)
    await middleware({"type": "lifespan"}, _receive, _send)

    assert reached == ["lifespan"]


def test_the_production_app_is_behind_the_body_size_limit():
    """A middleware that is not registered protects nothing."""
    from fastapi.middleware.cors import CORSMiddleware

    import app.main as main

    classes = [mw.cls for mw in main.app.user_middleware]
    assert BodySizeLimitMiddleware in classes

    # user_middleware is ordered outermost-first. CORS has to stay outside the limiter or
    # a browser sees the 413 as an opaque network error instead of a readable status.
    assert classes.index(CORSMiddleware) < classes.index(BodySizeLimitMiddleware)


def test_a_body_cap_below_a_route_cap_is_refused_by_config():
    """It would 413 uploads the endpoints are documented to accept."""
    with pytest.raises(ValueError, match="below the largest per-route upload cap"):
        Settings(max_request_body_mb=50, max_upload_mb=100, max_zip_upload_mb=200)


def test_startup_seeding_hashes_the_admin_password_off_the_loop():
    """bcrypt at cost 12 is ~250 ms of uninterruptible CPU, and this runs in lifespan."""
    import app.main as main

    source = inspect.getsource(main.seed_default_admin)
    tree = ast.parse(textwrap.dedent(source))
    inline = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "hash_password"
    ]
    assert not inline, "hash_password called inline in a coroutine"
    assert "hash_password_async" in source


# --------------------------------------------------------------------------- #
# Zip extraction: bounded peak, and no client error left as a 500
# --------------------------------------------------------------------------- #

def _archive(members, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buffer.getvalue()


def _spool_spy(monkeypatch) -> SimpleNamespace:
    """Record every byte handed to a spool, and every spool handed out.

    Byte count proves an over-budget archive is abandoned mid-inflate rather than inflated
    and then rejected; the handles prove a rejected archive does not leak its temp file.
    """
    spy = SimpleNamespace(written=0, spools=[])
    real_factory = tempfile.SpooledTemporaryFile

    def factory(*args, **kwargs):
        spool = real_factory(*args, **kwargs)
        real_write = spool.write

        def counting_write(chunk):
            spy.written += len(chunk)
            return real_write(chunk)

        spool.write = counting_write
        spy.spools.append(spool)
        return spool

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", factory)
    return spy


def test_members_round_trip_through_the_spool():
    payload = _archive([("a.txt", b"alpha"), ("b.md", b"beta"), ("skip.exe", b"nope")])

    with extract_zip(payload) as result:
        assert [(f.filename, f.size, f.data) for f in result.files] == [
            ("a.txt", 5, b"alpha"),
            ("b.md", 4, b"beta"),
        ]
        assert result.skipped == ["skip.exe"]
        # The zip router reads `.data` twice per entry (once for len, once for the upload),
        # so a one-shot handle would corrupt every file it stored.
        assert [len(f.data) for f in result.files] == [5, 4]
        assert result.files[0].data == b"alpha"


def test_extraction_does_not_hold_every_member_in_memory():
    """The aggregate cap used to be the RSS cost: `files.append(zf.read(...))` per entry."""
    member = b"\0" * (2 * 1024 * 1024)
    payload = _archive([(f"doc{i}.txt", member) for i in range(8)])  # 16 MiB inflated

    tracemalloc.start()
    try:
        result = extract_zip(payload)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    with result:
        assert sum(f.size for f in result.files) == 8 * len(member)
        assert result.files[3].data == member

    assert peak < 4 * 1024 * 1024, (
        f"peak heap was {peak} bytes for a 16 MiB archive — entries are being accumulated"
    )


def test_total_cap_abandons_the_archive_instead_of_inflating_it(monkeypatch):
    monkeypatch.setattr(settings, "max_zip_member_mb", 2)
    monkeypatch.setattr(settings, "max_zip_total_mb", 4)
    spy = _spool_spy(monkeypatch)
    payload = _archive([(f"doc{i}.txt", b"\0" * (2 * 1024 * 1024)) for i in range(3)])

    with pytest.raises(ZipExtractionError, match="Total uncompressed size"):
        extract_zip(payload)

    # 4 MiB of budget plus at most one chunk of slop, not the 6 MiB the archive holds.
    assert spy.written <= 4 * 1024 * 1024 + 256 * 1024
    assert all(spool.closed for spool in spy.spools), "rejected archive leaked its spool"


def test_oversized_member_is_skipped_and_the_rest_still_extract(monkeypatch):
    monkeypatch.setattr(settings, "max_zip_member_mb", 1)
    payload = _archive([("big.txt", b"\0" * (3 * 1024 * 1024)), ("small.txt", b"ok")])

    with extract_zip(payload) as result:
        assert [f.filename for f in result.files] == ["small.txt"]
        assert any("big.txt" in entry and "1 MB limit" in entry for entry in result.skipped)


def test_too_many_entries_is_a_client_error(monkeypatch):
    monkeypatch.setattr(settings, "max_zip_entries", 2)
    payload = _archive([(f"f{i}.txt", b"x") for i in range(3)])

    with pytest.raises(ZipExtractionError, match="maximum allowed is 2"):
        extract_zip(payload)


def test_a_corrupt_member_is_a_client_error_not_a_500():
    """`zf.read()` was bare, so BadZipFile('Bad CRC-32') escaped the router's handler.

    The router maps ZipExtractionError to a 4xx and nothing else, so a damaged archive
    came back as a 500.
    """
    payload = _archive([("doc.txt", b"A" * 4096)], compression=zipfile.ZIP_STORED)
    corrupted = payload.replace(b"A" * 4096, b"B" * 4096)
    assert corrupted != payload and len(corrupted) == len(payload)

    with pytest.raises(ZipExtractionError, match="doc.txt"):
        extract_zip(corrupted)


def test_a_non_zip_payload_is_a_client_error():
    with pytest.raises(ZipExtractionError, match="Invalid or corrupted"):
        extract_zip(b"not a zip archive at all")


# --------------------------------------------------------------------------- #
# Skill contribution workspaces: per-write cap plus a cumulative budget
# --------------------------------------------------------------------------- #

class _FakeDB:
    """Just enough AsyncSession for the two contribution write handlers."""

    def __init__(self, contribution):
        self._contribution = contribution
        self.commits = 0

    async def get(self, _model, _ident):
        return self._contribution

    async def commit(self):
        self.commits += 1


def _obj(object_name: str, size: int):
    return SimpleNamespace(object_name=object_name, size=size)


def _wire(monkeypatch, objects: list) -> SimpleNamespace:
    """A draft contribution owned by the caller, with `objects` already in storage."""
    from app.routers import skill_contributions as module

    contribution = SimpleNamespace(
        id=uuid4(),
        status=SkillContributionStatus.DRAFT.value,
        contributor_id=uuid4(),
        storage_path="skills/contrib/",
        skill_id=None,
        title="My Skill",
    )
    calls = SimpleNamespace(listed=0, stored=[])

    async def list_objects_async(prefix, recursive=True):
        calls.listed += 1
        return objects

    async def upload_file_async(object_name, data, content_type=None):
        calls.stored.append((object_name, len(data), content_type))

    async def upload_stream_async(object_name, stream, length, content_type=None):
        calls.stored.append((object_name, length, content_type))

    monkeypatch.setattr(module.storage_service, "list_objects_async", list_objects_async)
    monkeypatch.setattr(module.storage_service, "upload_file_async", upload_file_async)
    monkeypatch.setattr(module.storage_service, "upload_stream_async", upload_stream_async)

    return SimpleNamespace(
        module=module,
        contribution=contribution,
        db=_FakeDB(contribution),
        user=SimpleNamespace(id=contribution.contributor_id, role="employee"),
        calls=calls,
    )


def _upload(data: bytes, filename: str = "asset.bin") -> UploadFile:
    spool = SpooledTemporaryFile(max_size=1024)
    spool.write(data)
    spool.seek(0)
    return UploadFile(file=spool, size=len(data), filename=filename)


@pytest.mark.asyncio
async def test_text_put_rejects_content_over_the_cap(monkeypatch):
    """`content` is a JSON string, so none of the multipart guards ever applied to it."""
    ctx = _wire(monkeypatch, objects=[])
    request = ctx.module.PutFileRequest(
        path="SKILL.md", content="x" * (settings.max_contribution_text_kb * 1024 + 1)
    )

    with pytest.raises(HTTPException) as exc:
        await ctx.module.put_skill_contribution_file(
            ctx.contribution.id, request, db=ctx.db, current_user=ctx.user
        )

    assert exc.value.status_code == 413
    assert ctx.calls.listed == 0, "an oversized body still cost a storage round-trip"


@pytest.mark.asyncio
async def test_text_put_within_budget_is_stored_once(monkeypatch):
    ctx = _wire(monkeypatch, objects=[])
    request = ctx.module.PutFileRequest(path="SKILL.md", content="hello")

    result = await ctx.module.put_skill_contribution_file(
        ctx.contribution.id, request, db=ctx.db, current_user=ctx.user
    )

    assert result["status"] == "ok"
    assert ctx.calls.stored == [("skills/contrib/my-skill/SKILL.md", 5, "text/plain")]
    assert ctx.calls.listed == 1, "the storage listing is fetched more than once per write"


@pytest.mark.asyncio
async def test_many_small_writes_cannot_exceed_the_contribution_budget(monkeypatch):
    """Individually-legal writes under fresh paths were unbounded in aggregate."""
    full = settings.max_contribution_total_mb * 1024 * 1024
    ctx = _wire(monkeypatch, objects=[_obj("skills/contrib/root/big.bin", full)])
    request = ctx.module.PutFileRequest(path="one-more.md", content="x")

    with pytest.raises(HTTPException) as exc:
        await ctx.module.put_skill_contribution_file(
            ctx.contribution.id, request, db=ctx.db, current_user=ctx.user
        )

    assert exc.value.status_code == 413
    assert ctx.calls.stored == []


@pytest.mark.asyncio
async def test_overwriting_a_file_is_not_charged_twice(monkeypatch):
    """Charging for both copies would leave a workspace at its limit uncorrectable."""
    full = settings.max_contribution_total_mb * 1024 * 1024
    ctx = _wire(monkeypatch, objects=[_obj("skills/contrib/root/big.bin", full)])
    request = ctx.module.PutFileRequest(path="big.bin", content="x")

    result = await ctx.module.put_skill_contribution_file(
        ctx.contribution.id, request, db=ctx.db, current_user=ctx.user
    )

    assert result["status"] == "ok"
    assert ctx.calls.stored == [("skills/contrib/root/big.bin", 1, "text/plain")]


@pytest.mark.asyncio
async def test_file_count_budget_blocks_a_new_path(monkeypatch):
    objects = [
        _obj(f"skills/contrib/root/f{i}.md", 1)
        for i in range(settings.max_contribution_files)
    ]
    ctx = _wire(monkeypatch, objects=objects)
    request = ctx.module.PutFileRequest(path="extra.md", content="x")

    with pytest.raises(HTTPException) as exc:
        await ctx.module.put_skill_contribution_file(
            ctx.contribution.id, request, db=ctx.db, current_user=ctx.user
        )

    assert exc.value.status_code == 400
    assert "maximum allowed" in exc.value.detail


@pytest.mark.asyncio
async def test_binary_upload_shares_the_contribution_budget(monkeypatch):
    """The per-file cap on this path is 100 MB, which alone says nothing about the total."""
    full = settings.max_contribution_total_mb * 1024 * 1024
    ctx = _wire(monkeypatch, objects=[_obj("skills/contrib/root/big.bin", full)])

    with pytest.raises(HTTPException) as exc:
        await ctx.module.upload_skill_contribution_file(
            ctx.contribution.id,
            file=_upload(b"x" * 2048),
            path=None,
            db=ctx.db,
            current_user=ctx.user,
        )

    assert exc.value.status_code == 413
    assert ctx.calls.stored == []


@pytest.mark.asyncio
async def test_binary_upload_within_budget_streams_to_storage(monkeypatch):
    ctx = _wire(monkeypatch, objects=[_obj("skills/contrib/root/big.bin", 1024)])

    result = await ctx.module.upload_skill_contribution_file(
        ctx.contribution.id,
        file=_upload(b"x" * 2048),
        path=None,
        db=ctx.db,
        current_user=ctx.user,
    )

    assert result["status"] == "ok"
    assert ctx.calls.stored == [
        ("skills/contrib/root/asset.bin", 2048, "application/octet-stream")
    ]
