"""Router-layer hygiene for issue #87: caps, 4xx-not-500, and unleaked internals.

The pagination and UUID assertions are driven through a real httpx ASGI client against
`app.main.app`, not by calling handlers. That is load-bearing twice over:

  * `Query(le=200)` is enforced by FastAPI's validation layer. A direct call passes whatever
    the test hands over, so `page_size=100000` would "pass" a handler-level test against an
    endpoint with no cap at all.
  * FastAPI 0.141 includes routers lazily. `app.openapi()` is what materialises the routes,
    and the client would otherwise be driving three placeholder entries.

Everything that is not a validation rule (leaked exception text, swallowed failures, query
counts) is asserted on the handler or on the emitted statement, where it is observable.
"""

import io
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.database import get_db
from app.database.models import KnowledgeType, Source, WikiPage, WikiPageContribution
from app.main import app
from app.routers import admin_settings as settings_router
from app.routers import chat as chat_router
from app.routers import knowledge_types as kt_router
from app.routers import sources as sources_router
from app.services.auth_service import get_current_user

# Materialise the lazily-included routers before anything indexes or drives them.
app.openapi()

DEPT = uuid.uuid4()


def _admin():
    return SimpleNamespace(
        id=uuid.uuid4(), name="Admin", role="admin", department_id=DEPT,
        custom_role=None, custom_role_id=None, is_active=True,
        mcp_token_hash=None, last_connected=None,
    )


class _EmptySession:
    """Answers every query with nothing. The routes under test only need to reach their
    validation layer, and a 422 must be produced before any of this is touched."""

    def __init__(self):
        self.statements: list[object] = []

    async def execute(self, statement):
        self.statements.append(statement)
        empty = SimpleNamespace(all=lambda: [], first=lambda: None)
        empty.unique = lambda: empty
        return SimpleNamespace(
            scalars=lambda: empty,
            all=lambda: [],
            scalar=lambda: 0,
            scalar_one=lambda: 0,
            scalar_one_or_none=lambda: None,
        )

    async def get(self, *_a, **_k):
        return None

    def add(self, _row):
        pass

    async def flush(self):
        pass

    async def commit(self):
        pass

    async def refresh(self, _row):
        pass

    async def delete(self, _row):
        pass


@pytest.fixture
def captured_logs():
    """Collect loguru records.

    `caplog` cannot see these: the app logs through loguru, which does not propagate to the
    stdlib logging tree pytest hooks. Asserting on caplog here would make every "the failure
    is now recorded" test vacuously green.
    """
    from loguru import logger

    records: list[str] = []
    sink_id = logger.add(lambda message: records.append(str(message)), level="DEBUG")
    yield records
    logger.remove(sink_id)


@pytest.fixture
def client():
    """An authenticated ASGI client with the database stubbed out."""
    session = _EmptySession()
    app.dependency_overrides[get_current_user] = _admin
    app.dependency_overrides[get_db] = lambda: session
    transport = httpx.ASGITransport(app=app)
    yield httpx.AsyncClient(transport=transport, base_url="http://hygiene.test")
    app.dependency_overrides.clear()


# --------------------------------------------------------------------------- #
# 1. Pagination caps
#
# Each of these took any integer the caller named. The cap value matches the capped
# siblings the issue cites: audit.py le=200, sources.py le=500, wiki.py le=100.
# --------------------------------------------------------------------------- #

_UNCAPPED = [
    ("/api/employees", "page_size", 100_000, 200),
    ("/api/skills", "limit", 100_000, 200),
    ("/api/notes", "limit", 100_000, 500),
    ("/api/wiki/drafts", "limit", 100_000, 200),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,param,over,ceiling", _UNCAPPED)
async def test_an_over_the_cap_page_is_refused(client, path, param, over, ceiling):
    async with client as http:
        response = await http.get(f"{path}?{param}={over}")

    assert response.status_code == 422, (
        f"{path} accepted {param}={over}; one request can dump the whole table"
    )
    detail = response.json()["detail"][0]
    assert detail["loc"] == ["query", param]
    assert str(ceiling) in detail["msg"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,param,over,ceiling", _UNCAPPED)
async def test_the_ceiling_itself_is_still_allowed(client, path, param, over, ceiling):
    """An off-by-one in the cap would reject the largest page the endpoint advertises."""
    async with client as http:
        response = await http.get(f"{path}?{param}={ceiling}")
    assert response.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path,param", [(p, q) for p, q, _, _ in _UNCAPPED])
async def test_a_zero_or_negative_page_is_refused(client, path, param):
    """`ge=1` matters as much as `le`: LIMIT -1 is unbounded in Postgres."""
    async with client as http:
        assert (await http.get(f"{path}?{param}=0")).status_code == 422
        assert (await http.get(f"{path}?{param}=-1")).status_code == 422


@pytest.mark.asyncio
async def test_the_workspace_wiki_limit_is_bounded_but_still_admits_the_portals_2000(client):
    """The portal requests `?limit=2000` explicitly (wiki/[...slug], project-detail), so the
    ceiling is 2000 rather than a sibling's 100/500 — lowering it would 422 that page."""
    workspace = uuid.uuid4()
    async with client as http:
        over = await http.get(f"/api/projects/{workspace}/wiki?limit=2001")
        # 404 (no such project) rather than 422 proves the value passed validation.
        allowed = await http.get(f"/api/projects/{workspace}/wiki?limit=2000")

    assert over.status_code == 422
    assert allowed.status_code == 404


# --------------------------------------------------------------------------- #
# 2. A malformed UUID is a 422, never a 500
# --------------------------------------------------------------------------- #

_UUID_PARAMS = [
    ("GET", "/api/audit/log?principal_id=not-a-uuid"),
    ("GET", "/api/employees?department_id=not-a-uuid"),
    ("PUT", "/api/departments/not-a-uuid"),
    ("DELETE", "/api/departments/not-a-uuid"),
    ("PUT", "/api/employees/not-a-uuid"),
    ("DELETE", "/api/employees/not-a-uuid"),
    ("PATCH", "/api/employees/not-a-uuid/toggle"),
    ("POST", "/api/employees/not-a-uuid/token"),
    ("DELETE", "/api/employees/not-a-uuid/token"),
    ("PUT", "/api/knowledge-types/not-a-uuid"),
    ("DELETE", "/api/knowledge-types/not-a-uuid"),
    ("GET", "/api/projects/not-a-uuid/members"),
    ("GET", "/api/projects/not-a-uuid/sources"),
    ("GET", "/api/projects/not-a-uuid/wiki"),
    ("GET", "/api/projects/not-a-uuid/wiki/index"),
    ("GET", "/api/projects/not-a-uuid/wiki/graph"),
    ("PUT", "/api/projects/not-a-uuid"),
    ("DELETE", "/api/projects/not-a-uuid"),
    ("GET", "/api/wiki/pages/runbook?scope_id=not-a-uuid"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", _UUID_PARAMS)
async def test_a_malformed_uuid_never_reaches_the_driver(client, method, path):
    """~40 bare `uuid.UUID(<str>)` calls raised ValueError, and main.py registers no
    ValueError handler (only AnthropicError), so every one of them was a 500."""
    async with client as http:
        response = await http.request(method, path, json={})

    assert response.status_code == 422, (
        f"{method} {path} answered {response.status_code}; a client typo must be a 4xx"
    )
    body = response.text
    assert "Traceback" not in body and "badly formed" not in body


@pytest.mark.asyncio
async def test_a_well_formed_uuid_is_not_rejected_by_the_same_check(client):
    """Otherwise the assertions above would pass on an endpoint that 422s everything."""
    async with client as http:
        response = await http.get(f"/api/audit/log?principal_id={uuid.uuid4()}")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_a_malformed_form_uuid_on_upload_is_a_422(client):
    """`knowledge_type_id` arrived as a Form(str) and was parsed with a bare uuid.UUID()."""
    async with client as http:
        response = await http.post(
            "/api/sources/upload",
            files={"file": ("note.md", io.BytesIO(b"# body"), "text/markdown")},
            data={"knowledge_type_id": "not-a-uuid"},
        )
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 3. Internal exception text does not reach the client
# --------------------------------------------------------------------------- #

_SECRET = "postgresql://arkon:sup3rs3cr3t@10.0.0.9:5432/arkon"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler,capability",
    [
        ("test_embedding", "get_embedding"),
        ("test_llm", "get_llm"),
        ("test_vision", "get_vision"),
        ("test_chatbot", "get_chatbot_llm"),
        ("test_gateway", "get_gateway_llm"),
    ],
)
async def test_a_provider_test_never_echoes_the_exception(monkeypatch, handler, capability):
    """These returned `str(e)` in a 200 OK body, which for a provider SDK routinely carries
    the request URL — API key included — or a DSN with the password in it."""
    class _Registry:
        def __init__(self, _db):
            pass

        def __getattr__(self, _name):
            async def _boom(*_a, **_k):
                raise RuntimeError(f"connection refused: {_SECRET}")
            return _boom

    monkeypatch.setattr("app.ai.registry.ProviderRegistry", _Registry)

    result = await getattr(settings_router, handler)(db=_EmptySession(), _user=_admin())

    assert result.success is False
    assert _SECRET not in result.message
    assert "connection refused" not in result.message
    assert "Settings" in result.message, "the message must still tell the admin what to do"


@pytest.mark.asyncio
async def test_a_working_provider_message_is_still_passed_through(monkeypatch):
    """The provider's own (ok, msg) is written for the operator, so it must not be
    generalised away — otherwise the fix above would just blank the endpoint out."""
    class _Provider:
        async def test_connection(self):
            return True, "text-embedding-3-small reachable, 1536 dims"

    class _Registry:
        def __init__(self, _db):
            pass

        async def get_embedding(self, **_k):
            return _Provider()

    monkeypatch.setattr("app.ai.registry.ProviderRegistry", _Registry)

    result = await settings_router.test_embedding(db=_EmptySession(), _user=_admin())
    assert result.success is True
    assert result.message == "text-embedding-3-small reachable, 1536 dims"


@pytest.mark.asyncio
async def test_conversation_to_wiki_does_not_echo_the_llm_error(monkeypatch):
    """`detail=f"LLM synthesis failed: {exc}. ..."` handed the provider's message straight to
    whoever clicked "save to wiki"."""
    conv = SimpleNamespace(
        id=uuid.uuid4(), employee_id=uuid.uuid4(), scope_type="global", scope_id=None,
    )

    async def _owned(*_a, **_k):
        return conv

    async def _scope(*_a, **_k):
        return None

    class _Registry:
        def __init__(self, _db):
            pass

        async def get_chatbot_llm(self):
            raise RuntimeError(f"401 from https://api.example/v1?key={_SECRET}")

    monkeypatch.setattr(chat_router, "_get_owned_conversation", _owned)
    monkeypatch.setattr(chat_router, "assert_conversation_scope", _scope)
    monkeypatch.setattr(chat_router, "ProviderRegistry", _Registry)

    class _Messages(_EmptySession):
        async def execute(self, statement):
            self.statements.append(statement)
            row = SimpleNamespace(role="user", content="hello")
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))

    user = _admin()
    with pytest.raises(HTTPException) as exc:
        await chat_router.conversation_to_wiki(
            conv.id,
            chat_router.ToWikiRequest(title="Notes"),
            db=_Messages(),
            current_user=user,
        )

    assert exc.value.status_code == 503
    assert _SECRET not in exc.value.detail
    assert "401" not in exc.value.detail


@pytest.mark.asyncio
async def test_the_reembed_enqueue_failure_leaks_neither_exit(monkeypatch):
    """The Redis DSN had two ways out: the HTTP detail, and EmbeddingJob.error_message, which
    EmbeddingJobOut returns to the client verbatim."""
    from app.routers import admin_embeddings as embeddings_router

    stored: list[dict] = []

    class _Session(_EmptySession):
        def begin(self):
            class _Txn:
                async def __aenter__(_self):
                    return None

                async def __aexit__(_self, *_exc):
                    return False

            return _Txn()

        async def execute(self, statement):
            self.statements.append(statement)
            values = getattr(statement, "_values", None)
            if values:
                stored.append({
                    c.name: getattr(b, "value", b) for c, b in values.items()
                })
            return await super().execute(statement)

    async def _no_redis():
        raise ConnectionError(f"Error connecting to {_SECRET}")

    async def _spec(*_a, **_k):
        return None

    from app import worker

    monkeypatch.setattr(worker, "get_arq_pool", _no_redis)
    monkeypatch.setattr(embeddings_router, "_active_spec_id", _spec, raising=False)

    class _Config:
        def __init__(self, _db):
            pass

        async def get(self, key):
            return "sk-live" if "api_key" in key else None

        async def set(self, *_a, **_k):
            return None

    monkeypatch.setattr("app.services.config_service.ConfigService", _Config)

    with pytest.raises(HTTPException) as exc:
        await embeddings_router.switch_embedding_model(
            embeddings_router.EmbeddingSwitchBody(model_spec_id=_first_catalog_spec()),
            db=_Session(),
            _user=_admin(),
        )

    assert _SECRET not in exc.value.detail
    failed = [row for row in stored if row.get("status") == "failed"]
    assert failed, "the orphaned job must still be marked failed"
    assert _SECRET not in (failed[0]["error_message"] or "")


def _first_catalog_spec() -> str:
    from app.ai.embedding_catalog import list_specs

    return list_specs()[0].id


# --------------------------------------------------------------------------- #
# 4. N+1 query patterns
# --------------------------------------------------------------------------- #

class _CountingSession:
    """Counts SELECTs so a per-row query pattern is observable rather than inferred."""

    def __init__(self, pages=(), source=None):
        self.pages = list(pages)
        self.source = source
        self.selects: list[object] = []

    async def get(self, _model, _ident):
        return self.source

    async def execute(self, statement):
        self.selects.append(statement)
        pages = self.pages
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(all=lambda: pages),
            all=lambda: [],
            scalar_one=lambda: 0,
            scalar_one_or_none=lambda: None,
        )

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_knowledge_impact_does_not_query_per_affected_page(monkeypatch):
    """Two queries per page, on an unbounded page list: ~601 round trips for a 300-page
    source, on an endpoint the delete dialog calls synchronously."""
    source = Source(title="Handbook", source_type="file", status="ready")
    source.id = uuid.uuid4()

    pages = []
    for n in range(40):
        page = WikiPage(
            slug=f"p{n}", title=f"P{n}", page_type="concept", content_md="x", summary="",
        )
        page.id = uuid.uuid4()
        page.provenance_complete = True
        pages.append(page)

    db = _CountingSession(pages=pages, source=source)

    async def _access(_db, _user, source_id, _action):
        assert source_id == source.id
        return source

    monkeypatch.setattr(sources_router, "_require_source_access", _access)

    result = await sources_router.get_source_knowledge_impact(
        source.id, db=db, _user=_admin()
    )

    assert result["affected_pages"] == 40
    assert len(db.selects) <= 3, (
        f"{len(db.selects)} queries for 40 pages — the per-page lookups are back"
    )
    assert WikiPageContribution.__tablename__ in str(db.selects[-1]).lower()


@pytest.mark.asyncio
async def test_reorder_loads_every_knowledge_type_in_one_query():
    """`db.get` per id, on a client-supplied unbounded list: N round trips chosen by the
    caller."""
    ids = [uuid.uuid4() for _ in range(25)]
    rows = []
    for n, kt_id in enumerate(ids):
        kt = KnowledgeType(slug=f"t{n}", name=f"T{n}", color="#fff", sort_order=99)
        kt.id = kt_id
        rows.append(kt)

    class _Session(_CountingSession):
        async def execute(self, statement):
            self.selects.append(statement)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))

    db = _Session()
    result = await kt_router.reorder_knowledge_types(ids, db=db, _user=_admin())

    assert result == {"reordered": 25}
    assert len(db.selects) == 1, f"{len(db.selects)} queries to reorder 25 rows"
    assert [kt.sort_order for kt in rows] == list(range(25))


@pytest.mark.asyncio
async def test_reorder_does_not_claim_to_have_moved_ids_that_do_not_exist():
    """It returned `len(order)` unconditionally — a success count for rows it never saw."""
    class _Session(_CountingSession):
        async def execute(self, statement):
            self.selects.append(statement)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    result = await kt_router.reorder_knowledge_types(
        [uuid.uuid4(), uuid.uuid4()], db=_Session(), _user=_admin()
    )
    assert result == {"reordered": 0}


# --------------------------------------------------------------------------- #
# 5. The upload filename cannot escape the source's object prefix
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "hostile",
    [
        "../../x.pdf",
        "../x.pdf",
        "/etc/passwd.pdf",
        "..\\..\\x.pdf",
        "sub/dir/x.pdf",
    ],
)
def test_a_traversing_upload_name_cannot_leave_the_prefix(hostile):
    """`sources/{id}/original/../../x.pdf` writes outside the prefix, so the
    `delete_prefix("sources/{id}/")` in delete_source_completely misses it forever after the
    row is gone — a retention failure, not a cosmetic key problem."""
    safe = sources_router._storage_basename(hostile)

    assert "/" not in safe and "\\" not in safe
    assert ".." not in safe.split("/")
    key = f"sources/{uuid.uuid4()}/original/{safe}"
    assert key.count("/") == 3, key


@pytest.mark.parametrize("degenerate", ["..", ".", "", "/", "../..", None])
def test_a_name_with_nothing_usable_left_is_a_400(degenerate):
    with pytest.raises(HTTPException) as exc:
        sources_router._storage_basename(degenerate)
    assert exc.value.status_code == 400


def test_an_ordinary_name_is_untouched():
    assert sources_router._storage_basename("Q3 report (final).pdf") == "Q3 report (final).pdf"


class _UploadSession(_EmptySession):
    """Enough session for an upload to reach the object-key construction."""

    def __init__(self):
        super().__init__()
        self.rows: list[object] = []

    def add(self, row):
        # The Python-side uuid4/now() column defaults only fire during a real flush, and
        # _to_response reads created_at/updated_at off the row it is handed.
        if getattr(row, "id", None) is None:
            row.id = uuid.uuid4()
        for stamp in ("created_at", "updated_at"):
            if getattr(row, stamp, None) is None:
                setattr(row, stamp, _now())
        self.rows.append(row)

    async def execute(self, statement):
        self.statements.append(statement)
        row = self.rows[0] if self.rows else None
        empty = SimpleNamespace(all=lambda: [], first=lambda: None, one=lambda: row)
        empty.unique = lambda: empty
        return SimpleNamespace(
            scalars=lambda: empty, all=lambda: [], scalar=lambda: 0,
            scalar_one=lambda: row, scalar_one_or_none=lambda: row,
        )


@pytest.fixture
def captured_uploads(monkeypatch):
    """Record every object_name handed to MinIO, and stub out arq and the audit write."""
    keys: list[str] = []

    async def _upload_stream(*, object_name, **_kwargs):
        keys.append(object_name)

    async def _audit(*_a, **_k):
        return None

    async def _pool():
        return SimpleNamespace(
            enqueue_job=_enqueue_nothing,
        )

    from app.services.storage_service import storage_service

    monkeypatch.setattr(storage_service, "upload_stream_async", _upload_stream)
    monkeypatch.setattr(sources_router, "log_audit", _audit)
    monkeypatch.setattr(sources_router, "get_arq_pool", _pool)
    return keys


async def _enqueue_nothing(*_a, **_k):
    return None


@pytest.mark.asyncio
async def test_the_upload_handler_actually_uses_the_sanitised_name(captured_uploads):
    """Proves the *call site*, not just the helper.

    `_storage_basename` being correct is worth nothing if `upload_source` still interpolates
    `file.filename`, so this drives the handler and reads the key it handed to MinIO.
    """
    upload = _hostile_upload("../../escaped.md")
    db = _UploadSession()

    await sources_router.upload_source(
        file=upload, title=None, knowledge_type_id=None, department_ids=None,
        scope_type=None, scope_id=None, db=db, user=_admin(),
    )

    assert captured_uploads, "the handler never uploaded anything"
    key = captured_uploads[0]
    assert ".." not in key, f"the object key escaped its prefix: {key}"
    assert key.count("/") == 3 and key.startswith("sources/"), key
    assert key.endswith("/original/escaped.md"), key


@pytest.mark.asyncio
async def test_the_workspace_upload_handler_sanitises_too(captured_uploads, monkeypatch):
    """projects.py builds the identical key from the identical raw name."""
    from app.routers import projects as projects_router

    async def _project(*_a, **_k):
        return SimpleNamespace(id=uuid.uuid4(), name="WS", status="active")

    async def _role(*_a, **_k):
        return "editor"

    async def _pool():
        return SimpleNamespace(enqueue_job=_enqueue_nothing)

    monkeypatch.setattr(projects_router, "_get_project_or_404", _project)
    monkeypatch.setattr(projects_router, "_require_workspace_role", _role)
    monkeypatch.setattr(projects_router, "_get_arq_pool", _pool)

    await projects_router.upload_workspace_source(
        project_id=uuid.uuid4(),
        file=_hostile_upload("../../escaped.md"),
        title=None,
        knowledge_type_id=None,
        db=_UploadSession(),
        user=_admin(),
    )

    assert captured_uploads, "the handler never uploaded anything"
    key = captured_uploads[0]
    assert ".." not in key, f"the object key escaped its prefix: {key}"
    assert key.endswith("/original/escaped.md"), key


def _hostile_upload(filename: str):
    from fastapi import UploadFile

    return UploadFile(file=io.BytesIO(b"# body"), filename=filename)


# --------------------------------------------------------------------------- #
# 6. A broad except must not report success
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_a_skill_that_fails_to_serialize_is_not_counted_in_total(
    monkeypatch, captured_logs
):
    """`total` kept counting rows the loop dropped, so the response asserted
    len(items) == total while returning fewer — pagination arithmetic on a lie."""
    good = SimpleNamespace(
        id=uuid.uuid4(), name="Good", slug="good", current_version=1,
        version_hash="h", status="ready", scope_type="global", scope_id=None,
        is_system=False, created_at=_now(), updated_at=_now(), departments=[],
    )
    broken = SimpleNamespace(id=uuid.uuid4(), name=None, departments=[])

    async def _list(*_a, **_k):
        return [good, broken], 2

    from app.routers import skills as skills_router
    from app.services.skill_service import SkillService

    monkeypatch.setattr(SkillService, "list_skills", _list)

    result = await skills_router.list_skills(
        q=None, department_id=None, scope_type=None, scope_id=None, ids=None,
        cursor=None, limit=20, db=_EmptySession(), user=_admin(),
    )

    assert any("failed to serialize" in line for line in captured_logs), (
        "the drop was silent; logger.error(f\"...{e}\") recorded no traceback and a "
        "Pydantic ValidationError's str() does not name the failing field"
    )
    assert len(result["items"]) == 1
    assert result["total"] == 1, (
        f"total={result['total']} with 1 item returned — the count still includes the "
        "row that was silently dropped"
    )


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 8, 19, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_a_failed_embedding_upsert_is_recorded_rather_than_passed(
    monkeypatch, captured_logs
):
    """`except Exception: pass` and then 201. The page is genuinely usable — readable by
    slug, in the index — but it will never come back from a RAG query, and nothing anywhere
    said so."""
    conv = SimpleNamespace(
        id=uuid.uuid4(), employee_id=uuid.uuid4(), scope_type="global", scope_id=None,
    )
    page = WikiPage(
        slug="notes-abc123", title="Notes", page_type="synthesis",
        content_md="body", summary="s",
    )
    page.id = uuid.uuid4()

    async def _owned(*_a, **_k):
        return conv

    async def _scope(*_a, **_k):
        return None

    class _LLM:
        async def generate(self, *_a, **_k):
            return "## Body\ntext"

    class _Registry:
        def __init__(self, _db):
            pass

        async def get_chatbot_llm(self):
            return _LLM()

        async def get_active_embedding_spec_id(self):
            raise RuntimeError("embedding provider is not configured")

    async def _create(**_kwargs):
        return page

    monkeypatch.setattr(chat_router, "_get_owned_conversation", _owned)
    monkeypatch.setattr(chat_router, "assert_conversation_scope", _scope)
    monkeypatch.setattr(chat_router, "ProviderRegistry", _Registry)
    monkeypatch.setattr("app.services.wiki_service.apply_create", _create)

    class _Messages(_EmptySession):
        async def execute(self, statement):
            self.statements.append(statement)
            row = SimpleNamespace(role="user", content="hello")
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [row]))

    result = await chat_router.conversation_to_wiki(
        conv.id,
        chat_router.ToWikiRequest(title="Notes"),
        db=_Messages(),
        current_user=_admin(),
    )

    assert result.slug.startswith("notes-"), (
        "the page is still created — the embedding failure is non-fatal"
    )
    assert any("semantic search" in line for line in captured_logs), (
        "the embedding failure left no trace, so a page that cannot be retrieved is "
        "indistinguishable from one that can"
    )


@pytest.mark.asyncio
async def test_a_failed_presigned_url_is_logged_rather_than_swallowed(
    monkeypatch, captured_logs
):
    """A null download_url is a valid degraded response, but `pass` made a broken MinIO
    config look identical to a source that has no blob."""
    source = Source(title="Handbook", source_type="file", status="ready")
    source.id = uuid.uuid4()
    source.minio_key = f"sources/{source.id}/original/handbook.pdf"
    source.full_text = None
    source.outline_json = None
    source.page_offsets = []
    source.knowledge_type = None
    source.departments = []
    source.contributor = None
    source.scope_type = "global"
    source.scope_id = None
    source.created_at = _now()
    source.updated_at = _now()
    source.error_message = None
    source.progress = 100
    source.progress_message = None
    source.job_id = None
    source.knowledge_type_id = None
    source.contributed_by_employee_id = None
    source.file_name = "handbook.pdf"
    source.url = None

    class _Session(_EmptySession):
        async def execute(self, statement):
            self.statements.append(statement)
            return SimpleNamespace(
                scalar_one_or_none=lambda: source,
                scalar_one=lambda: 0,
                scalars=lambda: SimpleNamespace(all=lambda: []),
                all=lambda: [],
            )

    async def _boom(_key):
        raise RuntimeError(f"S3 endpoint unreachable: {_SECRET}")

    from app.services.storage_service import storage_service

    monkeypatch.setattr(storage_service, "get_presigned_url_async", _boom)

    result = await sources_router.get_source(source.id, db=_Session(), user=_admin())

    assert result.download_url is None
    assert any("Presigned URL" in line for line in captured_logs), (
        "`except Exception: pass` made a broken MinIO config indistinguishable from a "
        "source that legitimately has no blob"
    )


@pytest.mark.asyncio
async def test_the_master_token_is_not_left_behind_when_it_cannot_be_secured(
    monkeypatch, tmp_path
):
    """The docstring promises 0600. `except OSError: pass` on the chmod meant a failure left
    a full-account, long-lived Google credential readable under the default umask while the
    response still said it was saved."""
    from app.routers import notebooklm as nlm_router

    storage = tmp_path / "nlm"
    monkeypatch.setattr(
        "app.services.notebooklm_service._storage_path", lambda: storage
    )

    # The handler does its own `import os`, so this has to be patched on the module.
    import os as os_module

    real_chmod = os_module.chmod

    def _refuse(path, mode, *args, **kwargs):
        if str(path).endswith("master_token.json"):
            raise OSError("read-only filesystem")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os_module, "chmod", _refuse)

    body = nlm_router.MasterTokenImport(
        master_token="aas_et/" + "x" * 40, email="a@b.test", android_id="1234",
    )

    with pytest.raises(HTTPException) as exc:
        await nlm_router.import_master_token(body, current_user=_admin())

    assert exc.value.status_code == 500
    assert not (storage / "master_token.json").exists(), (
        "a credential we could not restrict to the owner was left on disk"
    )
