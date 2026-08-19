"""app/routers/admin_embeddings.py — catalog, status, and the re-embed switch.

Listed in issue #60 with zero tests. The switch endpoint is the destructive one: it
enqueues a job that re-embeds every wiki page against a new model, so the guards that
matter are "refuse a second concurrent job", "refuse a provider with no key", and "do not
leave a job the UI will poll forever". All three are asserted here.

Config reads go through the real ConfigService/ProviderRegistry with only
``ConfigService.get``/``set`` stubbed, so spec resolution and the dimension-to-table
lookup run for real; the session is a stub.
"""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.database.models import AuditLog, EmbeddingJob
from app.routers import admin_embeddings as embeddings_router
from app.services.config_service import (
    ACTIVE_EMBEDDING_MODEL_KEY,
    ConfigService,
)

CATALOG_SPEC = "openai/text-embedding-3-small"  # 1536-dim, has a schema table
CREATED_AT = datetime(2026, 8, 18, 8, 0, tzinfo=timezone.utc)


def _user(*perms: str, role: str = "employee"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        name="Admin",
        role=role,
        department_id=uuid.uuid4(),
        custom_role=SimpleNamespace(permissions=list(perms)) if perms else None,
    )


class _FakeSession:
    """Dispatches on the compiled SQL: page counts, embedding counts, the job query."""

    def __init__(self, *, job=None, current_job=None, pages=0, embedded=0):
        self._jobs = {job.id: job for job in ([job] if job else [])}
        self.current_job = current_job
        self.pages = pages
        self.embedded = embedded
        self.added: list[object] = []
        self.statements: list[object] = []
        self.commits = 0

    async def get(self, _model, ident):
        return self._jobs.get(ident)

    async def execute(self, statement):
        self.statements.append(statement)
        sql = str(statement).lower()
        if "count(" in sql:
            value = self.embedded if "wiki_page_embeddings" in sql else self.pages
            return SimpleNamespace(scalar_one=lambda: value)
        if "embedding_jobs" in sql:
            job = self.current_job
            return SimpleNamespace(scalar_one_or_none=lambda: job, rowcount=1)
        return SimpleNamespace(scalar_one_or_none=lambda: None, rowcount=1)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        # Python-side uuid defaults only fire on a real INSERT; the handler reads job.id
        # straight after flush, so the stub has to assign it here.
        for obj in self.added:
            if getattr(obj, "id", None) is None:
                obj.id = uuid.uuid4()

    async def commit(self):
        self.commits += 1

    def begin(self):
        session = self

        class _Txn:
            async def __aenter__(self):
                return session

            async def __aexit__(self, *_exc):
                return False

        return _Txn()

    def added_of(self, model) -> list:
        return [o for o in self.added if isinstance(o, model)]

    def updates(self) -> list[dict]:
        return [
            s.compile().params
            for s in self.statements
            if s.__class__.__name__ == "Update"
        ]


def _job(status: str = "running", spec_id: str = CATALOG_SPEC) -> EmbeddingJob:
    job = EmbeddingJob(model_spec_id=spec_id, status=status)
    job.id = uuid.uuid4()
    job.total_pages = 120
    job.done_pages = 40
    job.error_message = None
    job.started_at = None
    job.finished_at = None
    job.created_at = CREATED_AT
    return job


@pytest.fixture
def config(monkeypatch):
    """Back ConfigService with a dict so provider/key resolution runs for real."""
    store: dict[str, str] = {}

    async def _get(_self, key):
        return store.get(key)

    async def _set(_self, key, value):
        store[key] = value

    monkeypatch.setattr(ConfigService, "get", _get)
    monkeypatch.setattr(ConfigService, "set", _set)
    return store


@pytest.fixture
def queue(monkeypatch):
    """Capture arq enqueue calls instead of opening a Redis connection."""
    calls: list[tuple] = []

    class _Pool:
        async def enqueue_job(self, *args):
            calls.append(args)

    async def _get_pool():
        return _Pool()

    from app import worker

    monkeypatch.setattr(worker, "get_arq_pool", _get_pool)
    return calls


# --------------------------------------------------------------------------- #
# POST /api/settings/embeddings/switch
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_switch_rejects_an_uncatalogued_model_with_no_dimension(config):
    """Without a dimension there is no table to write vectors into, so accepting this
    would queue a job that can only fail in the worker."""
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await embeddings_router.switch_embedding_model(
            embeddings_router.EmbeddingSwitchBody(model_spec_id="acme/mystery-embed"),
            db=db,
            _user=_user(role="admin"),
        )
    assert exc.value.status_code == 400
    assert db.added_of(EmbeddingJob) == []


@pytest.mark.asyncio
async def test_switch_rejects_a_dimension_with_no_backing_table(config):
    """768 / 1536 / 3072 are the dimensions migration 015 created tables for."""
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await embeddings_router.switch_embedding_model(
            embeddings_router.EmbeddingSwitchBody(
                model_spec_id="acme/mystery-embed", dimension=999
            ),
            db=db,
            _user=_user(role="admin"),
        )
    assert exc.value.status_code == 400
    assert db.added_of(EmbeddingJob) == []


@pytest.mark.asyncio
async def test_switch_refuses_while_another_job_is_in_flight(config, queue):
    """Two concurrent re-embed jobs write different models' vectors into the same table,
    leaving a mix that search silently ranks against."""
    config["embedding_api_key__openai"] = "sk-live"
    db = _FakeSession(current_job=_job("running"))

    with pytest.raises(HTTPException) as exc:
        await embeddings_router.switch_embedding_model(
            embeddings_router.EmbeddingSwitchBody(model_spec_id=CATALOG_SPEC),
            db=db,
            _user=_user(role="admin"),
        )

    assert exc.value.status_code == 409
    assert db.added_of(EmbeddingJob) == []
    assert queue == []


@pytest.mark.asyncio
async def test_switch_refuses_a_provider_with_no_api_key(config, queue):
    """The job would start, fail on the first embed call, and leave the wiki half
    re-embedded against a model that never answered."""
    db = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        await embeddings_router.switch_embedding_model(
            embeddings_router.EmbeddingSwitchBody(model_spec_id=CATALOG_SPEC),
            db=db,
            _user=_user(role="admin"),
        )

    assert exc.value.status_code == 400
    assert "openai" in exc.value.detail
    assert db.added_of(EmbeddingJob) == []
    assert queue == []


@pytest.mark.asyncio
async def test_switch_enqueues_a_pending_job_for_the_chosen_model(config, queue):
    config["embedding_api_key__openai"] = "sk-live"
    db = _FakeSession()

    result = await embeddings_router.switch_embedding_model(
        embeddings_router.EmbeddingSwitchBody(model_spec_id=CATALOG_SPEC),
        db=db,
        _user=_user(role="admin"),
    )

    jobs = db.added_of(EmbeddingJob)
    assert len(jobs) == 1
    assert jobs[0].model_spec_id == CATALOG_SPEC
    assert jobs[0].status == "pending"
    assert result.job_id == jobs[0].id
    assert queue == [("reembed_all_pages_task", str(jobs[0].id))]
    assert db.commits == 1, "the job row must be committed before the worker picks it up"


@pytest.mark.asyncio
async def test_switch_audits_who_changed_the_embedding_model(config, queue):
    config["embedding_api_key__openai"] = "sk-live"
    db = _FakeSession()
    actor = _user(role="admin")

    await embeddings_router.switch_embedding_model(
        embeddings_router.EmbeddingSwitchBody(model_spec_id=CATALOG_SPEC),
        db=db,
        _user=actor,
    )

    entries = db.added_of(AuditLog)
    assert len(entries) == 1
    assert entries[0].action == "switch_embedding_model"
    assert entries[0].principal_id == actor.id
    assert CATALOG_SPEC in entries[0].reason


@pytest.mark.asyncio
async def test_switch_records_a_custom_model_so_the_worker_can_rebuild_the_spec(
    config, queue
):
    """A custom spec is not in the catalog, so the worker can only reconstruct it from
    these four config keys. Losing any of them makes the queued job unrunnable."""
    config["embedding_api_key__acme"] = "sk-live"
    db = _FakeSession()

    await embeddings_router.switch_embedding_model(
        embeddings_router.EmbeddingSwitchBody(
            model_spec_id="acme/mystery-embed", dimension=1536
        ),
        db=db,
        _user=_user(role="admin"),
    )

    assert config["embedding_custom_spec_id"] == "acme/mystery-embed"
    assert config["embedding_custom_model_id"] == "mystery-embed"
    assert config["embedding_custom_dimension"] == "1536"
    assert config["embedding_custom_provider"] == "acme"


@pytest.mark.asyncio
async def test_switch_marks_the_job_failed_when_the_queue_is_unreachable(
    config, monkeypatch
):
    """A committed job that was never enqueued leaves the settings page polling a
    "pending" job forever, and blocks every later switch with the 409 above."""
    config["embedding_api_key__openai"] = "sk-live"

    async def _no_redis():
        raise ConnectionError("redis is down")

    from app import worker

    monkeypatch.setattr(worker, "get_arq_pool", _no_redis)
    db = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        await embeddings_router.switch_embedding_model(
            embeddings_router.EmbeddingSwitchBody(model_spec_id=CATALOG_SPEC),
            db=db,
            _user=_user(role="admin"),
        )

    assert exc.value.status_code == 500
    failed = [u for u in db.updates() if u.get("status") == "failed"]
    assert failed, "the orphaned job was left in 'pending'"
    assert "Enqueue failed" in failed[0]["error_message"]


# --------------------------------------------------------------------------- #
# GET /api/settings/embeddings/jobs/{id} and cancel
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_get_job_404s_for_an_unknown_id():
    with pytest.raises(HTTPException) as exc:
        await embeddings_router.get_job(
            uuid.uuid4(), db=_FakeSession(), _user=_user(role="admin")
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_job_reports_progress_counters():
    """The settings page renders done/total as a progress bar; both must survive the DTO."""
    job = _job("running")
    result = await embeddings_router.get_job(
        job.id, db=_FakeSession(job=job), _user=_user(role="admin")
    )
    assert (result.done_pages, result.total_pages) == (40, 120)
    assert result.status == "running"
    assert result.model_spec_id == CATALOG_SPEC


@pytest.mark.asyncio
async def test_cancel_job_404s_for_an_unknown_id():
    with pytest.raises(HTTPException) as exc:
        await embeddings_router.cancel_job(
            uuid.uuid4(), db=_FakeSession(), _user=_user(role="admin")
        )
    assert exc.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
async def test_cancel_refuses_an_already_finished_job(status):
    """Re-cancelling a completed job would rewrite finished_at and lose when the
    migration actually ended."""
    job = _job(status)
    with pytest.raises(HTTPException) as exc:
        await embeddings_router.cancel_job(
            job.id, db=_FakeSession(job=job), _user=_user(role="admin")
        )
    assert exc.value.status_code == 400
    assert job.status == status


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "running"])
async def test_cancel_stops_an_unfinished_job(status):
    job = _job(status)
    db = _FakeSession(job=job)

    result = await embeddings_router.cancel_job(job.id, db=db, _user=_user(role="admin"))

    assert job.status == "cancelled"
    assert job.finished_at is not None
    assert result.status == "cancelled"
    assert db.commits == 1


# --------------------------------------------------------------------------- #
# GET catalog / status
# --------------------------------------------------------------------------- #

@pytest.mark.asyncio
async def test_catalog_reports_key_configuration_per_provider(config):
    config["embedding_api_key__openai"] = "sk-live"
    db = _FakeSession()

    result = await embeddings_router.get_catalog(db=db, _user=_user(role="admin"))

    by_id = {s.id: s for s in result.specs}
    assert by_id[CATALOG_SPEC].api_key_configured is True
    assert by_id["google/gemini-embedding-001"].api_key_configured is False
    assert by_id[CATALOG_SPEC].dimension == 1536


@pytest.mark.asyncio
async def test_catalog_never_returns_the_api_key_itself(config):
    """This route is reachable by any org:settings:manage holder; the response must carry
    a boolean, never the secret it was derived from."""
    config["embedding_api_key__openai"] = "sk-super-secret-value"
    db = _FakeSession()

    result = await embeddings_router.get_catalog(db=db, _user=_user(role="admin"))

    assert "sk-super-secret-value" not in result.model_dump_json()


@pytest.mark.asyncio
async def test_catalog_marks_the_active_spec(config):
    config[ACTIVE_EMBEDDING_MODEL_KEY] = CATALOG_SPEC
    db = _FakeSession()
    result = await embeddings_router.get_catalog(db=db, _user=_user(role="admin"))
    assert result.active_spec_id == CATALOG_SPEC


@pytest.mark.asyncio
async def test_status_counts_pages_against_the_active_specs_own_table(config):
    """embedded_pages must come from the table matching the active spec's dimension —
    counting a different dimension's table reports a finished migration that never ran."""
    config[ACTIVE_EMBEDDING_MODEL_KEY] = CATALOG_SPEC
    db = _FakeSession(pages=250, embedded=180, current_job=_job("running"))

    result = await embeddings_router.get_status(db=db, _user=_user(role="admin"))

    assert result.active_spec_id == CATALOG_SPEC
    assert result.total_pages == 250
    assert result.embedded_pages == 180
    assert result.current_job is not None and result.current_job.status == "running"
    embedding_counts = [
        s for s in db.statements if "wiki_page_embeddings_1536" in str(s).lower()
    ]
    assert embedding_counts, "the 1536-dim table was never queried"


@pytest.mark.asyncio
async def test_status_reports_zero_embedded_when_no_model_is_active(config):
    """A fresh install has no active spec; the route must answer rather than 500 on
    get_spec(None)."""
    db = _FakeSession(pages=12, embedded=99)

    result = await embeddings_router.get_status(db=db, _user=_user(role="admin"))

    assert result.active_spec_id is None
    assert result.total_pages == 12
    assert result.embedded_pages == 0
    assert result.current_job is None


# --------------------------------------------------------------------------- #
# Route wiring
# --------------------------------------------------------------------------- #

EMBEDDING_ROUTES = [
    ("GET", "/api/settings/embeddings/catalog"),
    ("GET", "/api/settings/embeddings/status"),
    ("POST", "/api/settings/embeddings/switch"),
    ("GET", "/api/settings/embeddings/jobs/{job_id}"),
    ("POST", "/api/settings/embeddings/jobs/{job_id}/cancel"),
]


@pytest.mark.parametrize(
    "method,path", EMBEDDING_ROUTES, ids=[f"{m} {p}" for m, p in EMBEDDING_ROUTES]
)
def test_embedding_route_requires_settings_manage(route_guards, method, path):
    assert [p for p, _ in route_guards(method, path)] == ["org:settings:manage"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path", EMBEDDING_ROUTES, ids=[f"{m} {p}" for m, p in EMBEDDING_ROUTES]
)
async def test_embedding_route_is_closed_to_read_only_settings_access(
    route_guards, method, path
):
    """org:settings:read must not reach these. Even the two GETs disclose which providers
    have keys configured, and the switch re-embeds the entire wiki."""
    (_, guard), = route_guards(method, path)
    with pytest.raises(HTTPException) as exc:
        await guard(_user("org:settings:read"))
    assert exc.value.status_code == 403
    assert await guard(_user("org:settings:manage")) is not None
