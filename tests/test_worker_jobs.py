"""Behaviour of the arq entry points in app/worker.py (issue #61).

Every job here is driven for real against the in-memory session factory from
conftest.py (`fake_db`). The invariant under test throughout is the one the four
worker bugs violated: **a job that fails must leave a terminal status behind.** A source
or artifact still reading `processing` after the job returned is unrecoverable from the
UI — nothing retries it and nothing reports it — so a stranded `processing` is worse than
a loud failure.

tests/test_map_coverage.py covers some of the same ground with `inspect.getsource`
assertions. Those catch a reverted edit but cannot tell a working handler from one whose
body was commented out; these run the handler.
"""

import ast
import asyncio
import inspect
import os
import pathlib
import time
import uuid
import zipfile
from types import SimpleNamespace

import pytest

import app.worker as worker
from app.services.image_service import ImageInfo

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _source(**overrides):
    """A Source stand-in carrying every column the ingestion jobs touch."""
    row = SimpleNamespace(
        id=uuid.uuid4(),
        title="Employee handbook",
        file_name="handbook.pdf",
        minio_key="sources/handbook.pdf",
        url=None,
        source_type="file",
        status="pending",
        progress=0,
        progress_message=None,
        error_message=None,
        outline_json=None,
        full_text=None,
        page_offsets=None,
        job_id=None,
        knowledge_type_id=None,
        scope_type="global",
        scope_id=None,
        pipeline_phase=None,
        pipeline_strategy=None,
    )
    for key, value in overrides.items():
        setattr(row, key, value)
    return row


class _FakePool:
    def __init__(self):
        self.enqueued: list[tuple] = []

    async def enqueue_job(self, name, *args):
        self.enqueued.append((name, args))
        return SimpleNamespace(job_id=f"arq-job-{len(self.enqueued)}")


def _patch_pool(monkeypatch) -> _FakePool:
    pool = _FakePool()

    async def _get_pool():
        return pool

    monkeypatch.setattr(worker, "get_arq_pool", _get_pool)
    return pool


_PAGES = [
    {"content": "# Handbook\n\nLeave policy is 12 days.", "page_number": 1},
    {"content": "## Expenses\n\nReceipts within 30 days.", "page_number": 2},
]


def _patch_file_ingest(monkeypatch, *, download=None, pages=_PAGES, image_count=0):
    """Wire up the external services ingest_file_task calls, leaving its own logic real."""
    from app.services import image_service, kb_service
    from app.services.storage_service import storage_service

    async def _download(key):
        if download is not None:
            return download(key)
        return b"%PDF-1.7 fake"

    async def _extract(_data, _name):
        return [dict(page) for page in pages]

    def _extract_images(_data, _name, _source_id):
        # Fresh objects per call: the task mutates .image_id on what it gets back, and a
        # retry must not see the previous attempt's ids.
        return [
            ImageInfo(
                minio_key=f"images/{index}.png",
                page_number=1,
                image_index=index,
                content_type="image/png",
                size_bytes=100 + index,
                caption=f"Diagram {index}",
            )
            for index in range(image_count)
        ]

    monkeypatch.setattr(storage_service, "download_file_async", _download)
    monkeypatch.setattr(kb_service, "_extract_text_from_file", _extract)
    monkeypatch.setattr(image_service, "extract_images", _extract_images)


# ---------------------------------------------------------------------------
# ingest_file_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "detail"),
    [
        (RuntimeError("MinIO connection reset"), "MinIO connection reset"),
        # `str(CancelledError())` is empty, so an arq worker_job_timeout — the exact case
        # `except BaseException` exists for — used to record status="error" with a blank
        # reason and "Error: " as the progress message, which reaches the client verbatim.
        # The class name is the fallback, matching what ingest_refine_task already did.
        (asyncio.CancelledError(), "CancelledError"),
    ],
    ids=["exception", "cancelled"],
)
async def test_file_ingest_failure_leaves_a_terminal_error_status(
    fake_db, monkeypatch, failure, detail
):
    """A failed pre-processing run must not leave the source at `processing`.

    The CancelledError case is the reason the handler catches BaseException: arq cancels a
    job that exceeds worker_job_timeout, and `except Exception` let that cancellation walk
    straight past the recovery block with `status = "processing"` already committed. The
    re-raise matters just as much — returning normally makes arq record the job COMPLETE,
    so max_tries=3 never engages and no dead-letter record exists.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())

    def _boom(_key):
        raise failure

    _patch_file_ingest(monkeypatch, download=_boom)
    _patch_pool(monkeypatch)

    with pytest.raises(type(failure)):
        await worker.ingest_file_task({}, str(src.id))

    assert src.status == "error"
    assert src.progress == 0
    assert src.error_message == detail
    assert (src.progress_message or "").startswith("Error:")


@pytest.mark.asyncio
async def test_file_ingest_marks_error_when_no_text_survives_extraction(fake_db, monkeypatch):
    """A scanned PDF that OCRs to nothing is a terminal outcome, not a retry.

    This branch returns rather than raising, so the status write is the only signal the
    UI gets.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    _patch_file_ingest(monkeypatch, pages=[{"content": "   ", "page_number": 1}])
    _patch_pool(monkeypatch)

    result = await worker.ingest_file_task({}, str(src.id))

    assert result == {"status": "error", "message": "No text content"}
    assert src.status == "error"
    assert src.error_message == "Unable to extract text content"


@pytest.mark.asyncio
async def test_file_ingest_is_idempotent_across_a_retry(fake_db, monkeypatch):
    """A retry of a partially-completed ingest must be able to succeed.

    source_images carries UniqueConstraint(source_id, image_index) and the worker runs
    with max_tries=3. Re-inserting image_index=0 raised IntegrityError on every attempt,
    the handler pinned status="error", and all three retries failed identically — leaving
    the source permanently unprocessable without hand-deleting rows.
    """
    from app.database.models import Source, SourceImage

    src = fake_db.seed(Source, _source())
    other = fake_db.insert(
        SourceImage(
            source_id=uuid.uuid4(),
            minio_key="images/other.png",
            page_number=1,
            image_index=0,
            content_type="image/png",
            size_bytes=1,
        )
    )
    _patch_file_ingest(monkeypatch, image_count=2)
    pool = _patch_pool(monkeypatch)

    first = await worker.ingest_file_task({}, str(src.id))
    second = await worker.ingest_file_task({}, str(src.id))

    assert first == second == {"status": "processing", "images": 2}
    assert src.status == "processing"
    assert src.error_message is None

    mine = [r for r in fake_db.table_rows("source_images") if r.source_id == src.id]
    assert [r.image_index for r in sorted(mine, key=lambda r: r.image_index)] == [0, 1]
    assert other in fake_db.table_rows("source_images"), (
        "the retry cleanup deleted another source's images — the DELETE must be scoped"
    )
    assert [name for name, _ in pool.enqueued] == [
        "ingest_map_reduce_task", "caption_images_task",
        "ingest_map_reduce_task", "caption_images_task",
    ]


@pytest.mark.asyncio
async def test_file_ingest_inlines_the_persisted_image_ids(fake_db, monkeypatch):
    """The markers must carry the flushed row ids, not placeholders.

    `img.image_id = str(row.id)` only works because the row is flushed first; without a
    real id the compiler would emit `image://None` markers that the marker validator then
    strips, silently dropping every figure in the document.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    _patch_file_ingest(monkeypatch, image_count=2)
    _patch_pool(monkeypatch)

    await worker.ingest_file_task({}, str(src.id))

    ids = [str(r.id) for r in fake_db.table_rows("source_images")]
    assert len(ids) == 2
    for image_id in ids:
        assert f"image://{image_id}" in src.full_text
    assert src.progress == 55


# ---------------------------------------------------------------------------
# ingest_url_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("403 from origin"), asyncio.CancelledError()],
    ids=["exception", "cancelled"],
)
async def test_url_ingest_failure_leaves_a_terminal_error_status(fake_db, monkeypatch, failure):
    """Same stranded-`processing` risk as the file path, via a different fetcher."""
    from app.database.models import Source
    from app.services import kb_service

    src = fake_db.seed(Source, _source(source_type="url", url="https://example.test/policy"))

    async def _fetch(_url):
        raise failure

    monkeypatch.setattr(kb_service, "_extract_text_from_url", _fetch)

    with pytest.raises(type(failure)):
        await worker.ingest_url_task({}, str(src.id))

    assert src.status == "error"
    assert src.progress == 0


@pytest.mark.asyncio
async def test_url_ingest_marks_error_when_the_source_has_no_url(fake_db):
    """Reached whenever a url source was created without one; must not stay `processing`."""
    from app.database.models import Source

    src = fake_db.seed(Source, _source(source_type="url", url=None))

    assert await worker.ingest_url_task({}, str(src.id)) == {"status": "error"}
    assert src.status == "error"
    assert src.error_message == "Source has no URL"


@pytest.mark.asyncio
async def test_url_ingest_marks_error_when_the_page_yields_no_text(fake_db, monkeypatch):
    """A JS-only page fetches fine and extracts to nothing — still terminal."""
    from app.database.models import Source
    from app.services import kb_service

    src = fake_db.seed(Source, _source(source_type="url", url="https://example.test/spa"))

    async def _fetch(_url):
        return [{"content": "", "page_number": 1}]

    monkeypatch.setattr(kb_service, "_extract_text_from_url", _fetch)

    assert await worker.ingest_url_task({}, str(src.id)) == {"status": "error"}
    assert src.status == "error"
    assert src.error_message == "Unable to fetch content from URL"


@pytest.mark.asyncio
async def test_url_ingest_hands_off_to_the_mrp_pipeline(fake_db, monkeypatch):
    """Positive control: the success path must record the job id it enqueued.

    Without source.job_id the API cannot report progress for the second half of the
    pipeline, which is where the source spends most of its time.
    """
    from app.database.models import Source
    from app.services import kb_service

    src = fake_db.seed(Source, _source(source_type="url", url="https://example.test/policy"))

    async def _fetch(_url):
        return [{"content": "# Policy\n\nBody text.", "page_number": 1}]

    monkeypatch.setattr(kb_service, "_extract_text_from_url", _fetch)
    pool = _patch_pool(monkeypatch)

    assert await worker.ingest_url_task({}, str(src.id)) == {"status": "processing"}
    assert src.status == "processing"
    assert src.progress == 55
    assert src.job_id == "arq-job-1"
    assert pool.enqueued == [("ingest_map_reduce_task", (str(src.id),))]


# ---------------------------------------------------------------------------
# ingest_skill_task / delete_skill_task
# ---------------------------------------------------------------------------


def _skill_pair(fake_db):
    from app.database.models import Skill, SkillVersion

    skill = fake_db.seed(
        Skill,
        SimpleNamespace(
            id=uuid.uuid4(), status="pending", version_hash=None,
            current_version=None, storage_path=None,
        ),
    )
    version = fake_db.seed(
        SkillVersion,
        SimpleNamespace(
            id=uuid.uuid4(), version_number=1, version_hash=None, storage_path=None,
        ),
    )
    return skill, version


class _StorageRecorder:
    def __init__(self):
        self.uploads: list[str] = []
        self.deleted_prefixes: list[str] = []

    def install(self, monkeypatch, *, upload_fails=False):
        from app.services.storage_service import storage_service

        async def _upload_file(object_name, data=None, content_type=None):
            if upload_fails:
                raise RuntimeError("MinIO 503")
            self.uploads.append(object_name)

        async def _upload_stream(object_name, stream, size, content_type):
            if upload_fails:
                raise RuntimeError("MinIO 503")
            self.uploads.append(object_name)

        async def _hash(prefix):
            return "deadbeef"

        async def _delete_prefix(prefix):
            self.deleted_prefixes.append(prefix)

        monkeypatch.setattr(storage_service, "upload_file_async", _upload_file)
        monkeypatch.setattr(storage_service, "upload_stream_async", _upload_stream)
        monkeypatch.setattr(storage_service, "calculate_prefix_hash_async", _hash)
        monkeypatch.setattr(storage_service, "delete_prefix_async", _delete_prefix)
        return self


def _write_skill_zip(path, members):
    with zipfile.ZipFile(path, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    return str(path)


@pytest.mark.asyncio
async def test_skill_ingest_marks_error_reraises_and_cleans_up(fake_db, monkeypatch, tmp_path):
    """The skill handler has three jobs on failure and used to do only the first.

    It must (1) mark the skill `error`, (2) delete the partial upload — otherwise the next
    attempt hashes a mix of stale and new objects and produces a version_hash for content
    that was never a coherent package — and (3) re-raise, because swallowing meant arq
    recorded the job COMPLETE and max_tries=3 never engaged.
    """
    skill, version = _skill_pair(fake_db)
    storage = _StorageRecorder().install(monkeypatch)
    zip_path = _write_skill_zip(tmp_path / "pkg.zip", {"../escape.md": "x", "pkg/SKILL.md": "y"})

    with pytest.raises(ValueError, match="Zip Slip"):
        await worker.ingest_skill_task(
            {}, str(skill.id), str(version.id), zip_path, "pkg.zip"
        )

    assert skill.status == "error"
    assert skill.storage_path is None, "a failed ingest must not publish a storage_path"
    assert storage.deleted_prefixes == [f"skills/{skill.id}/versions/1/"]
    assert not os.path.exists(zip_path), "the disk buffer outlived the job"


@pytest.mark.asyncio
async def test_skill_ingest_upload_failure_is_also_terminal_and_retryable(fake_db, monkeypatch, tmp_path):
    """A MinIO outage mid-upload takes the same path as a rejected package."""
    skill, version = _skill_pair(fake_db)
    storage = _StorageRecorder().install(monkeypatch, upload_fails=True)
    zip_path = _write_skill_zip(tmp_path / "pkg.zip", {"pkg/SKILL.md": "# Skill"})

    with pytest.raises(RuntimeError, match="MinIO 503"):
        await worker.ingest_skill_task(
            {}, str(skill.id), str(version.id), zip_path, "pkg.zip"
        )

    assert skill.status == "error"
    assert skill.version_hash is None
    assert storage.deleted_prefixes == [f"skills/{skill.id}/versions/1/"]


@pytest.mark.asyncio
async def test_skill_ingest_marks_error_when_the_disk_buffer_vanished(fake_db, monkeypatch, tmp_path):
    """Server crash between upload and dequeue: the row must not stay `processing`."""
    skill, version = _skill_pair(fake_db)
    _StorageRecorder().install(monkeypatch)

    await worker.ingest_skill_task(
        {}, str(skill.id), str(version.id), str(tmp_path / "gone.zip"), "gone.zip"
    )

    assert skill.status == "error"


@pytest.mark.asyncio
async def test_skill_ingest_publishes_hash_and_path_on_success(fake_db, monkeypatch, tmp_path):
    """Positive control for the two assertions above: `active` is reachable."""
    skill, version = _skill_pair(fake_db)
    storage = _StorageRecorder().install(monkeypatch)
    zip_path = _write_skill_zip(
        tmp_path / "pkg.zip", {"pkg/SKILL.md": "# Skill", "pkg/scripts/run.py": "print(1)"}
    )

    await worker.ingest_skill_task({}, str(skill.id), str(version.id), zip_path, "pkg.zip")

    assert skill.status == "active"
    assert skill.version_hash == version.version_hash == "deadbeef"
    assert skill.storage_path == f"skills/{skill.id}/versions/1/content/"
    assert sorted(storage.uploads) == [
        f"skills/{skill.id}/versions/1/content/pkg/SKILL.md",
        f"skills/{skill.id}/versions/1/content/pkg/scripts/run.py",
    ]
    assert storage.deleted_prefixes == []


@pytest.mark.asyncio
async def test_delete_skill_task_reraises_instead_of_dropping_the_row(fake_db, monkeypatch):
    """If MinIO deletion fails the DB row must survive, or the objects are orphaned.

    Committing the row deletion anyway would leave storage occupied by files nothing
    references — unreachable and unbillable to any skill.
    """
    from app.database.models import Skill
    from app.services.storage_service import storage_service

    skill = SimpleNamespace(id=uuid.uuid4(), status="active", contributions=[])
    fake_db.seed(Skill, skill)
    fake_db.select_rows["skills"] = [skill]

    async def _delete_prefix(_prefix):
        raise RuntimeError("MinIO unreachable")

    monkeypatch.setattr(storage_service, "delete_prefix_async", _delete_prefix)

    with pytest.raises(RuntimeError, match="MinIO unreachable"):
        await worker.delete_skill_task({}, str(skill.id))

    assert skill not in fake_db.deleted
    assert fake_db.commits == 0


# ---------------------------------------------------------------------------
# ingest_map_reduce_task / ingest_refine_task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [RuntimeError("provider 429"), asyncio.CancelledError()],
    ids=["exception", "cancelled"],
)
async def test_map_reduce_failure_leaves_a_terminal_error_status(fake_db, monkeypatch, failure):
    """MAP+REDUCE is the longest phase, so it is the one arq actually times out."""
    from app.ai.mrp import pipeline
    from app.database.models import Source

    src = fake_db.seed(Source, _source(full_text="Body text", status="processing", progress=55))

    async def _pipeline(**_kwargs):
        raise failure

    monkeypatch.setattr(pipeline, "run_mrp_pipeline", _pipeline)

    with pytest.raises(type(failure)):
        await worker.ingest_map_reduce_task({}, str(src.id))

    assert src.status == "error"
    assert src.progress == 0
    assert (src.progress_message or "").startswith("Error:")


@pytest.mark.asyncio
async def test_map_reduce_without_full_text_fails_loudly(fake_db, monkeypatch):
    """Enqueued out of order (before pre-processing), this must not compile an empty doc."""
    from app.database.models import Source

    src = fake_db.seed(Source, _source(full_text=None))

    with pytest.raises(ValueError, match="no full_text"):
        await worker.ingest_map_reduce_task({}, str(src.id))


@pytest.mark.asyncio
async def test_map_reduce_promotes_a_finished_plan_to_plan_ready(fake_db, monkeypatch):
    """Positive control: the non-auto-approve path parks the source for human review.

    `plan_ready` at progress 80 is a *waiting* state, not a stuck one — the distinction
    the error tests above rely on.
    """
    from app.ai.mrp import pipeline
    from app.database.models import Source

    src = fake_db.seed(Source, _source(full_text="Body text"))
    plan_id = uuid.uuid4()

    async def _pipeline(**_kwargs):
        return {"status": "plan_ready", "plan_id": str(plan_id)}

    monkeypatch.setattr(pipeline, "run_mrp_pipeline", _pipeline)

    result = await worker.ingest_map_reduce_task({}, str(src.id))

    assert result == {"status": "plan_ready", "plan_id": str(plan_id)}
    assert src.status == "plan_ready"
    assert src.progress == 80


@pytest.mark.asyncio
async def test_refine_failure_leaves_a_terminal_error_status(fake_db, monkeypatch):
    """REFINE writes the wiki pages; a failure here must not read as success."""
    from app.ai.mrp import pipeline
    from app.database.models import Source

    src = fake_db.seed(Source, _source(full_text="Body text", status="processing", progress=78))

    async def _pipeline(**_kwargs):
        raise RuntimeError("writer gateway 502")

    monkeypatch.setattr(pipeline, "run_refine_pipeline", _pipeline)

    with pytest.raises(RuntimeError):
        await worker.ingest_refine_task({}, str(src.id))

    assert src.status == "error"
    assert src.error_message == "writer gateway 502"
    assert src.progress == 0


@pytest.mark.asyncio
async def test_refine_records_a_detail_for_an_exception_with_no_message(fake_db, monkeypatch):
    """`str(CancelledError())` is empty, and an empty error_message renders as no error.

    The UI shows "error" with a blank reason, which is indistinguishable from a display
    bug; the fallback to the exception class name is what makes a timeout diagnosable.
    """
    from app.ai.mrp import pipeline
    from app.database.models import Source

    src = fake_db.seed(Source, _source(full_text="Body text", status="processing"))

    async def _pipeline(**_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(pipeline, "run_refine_pipeline", _pipeline)

    with pytest.raises(asyncio.CancelledError):
        await worker.ingest_refine_task({}, str(src.id))

    assert src.status == "error"
    assert src.error_message == "CancelledError"
    assert src.progress_message == "Error: CancelledError"


# ---------------------------------------------------------------------------
# reembed_all_pages_task
# ---------------------------------------------------------------------------

_SPEC_ID = "openai/text-embedding-3-small"


def _wiki_page(title="Leave policy"):
    return SimpleNamespace(
        id=uuid.uuid4(), slug="concept/leave-policy", title=title,
        summary="Summary", content_md="Body",
    )


class _FakeRegistry:
    """Stands in for ProviderRegistry inside the re-embed job."""

    def __init__(self, _session, *, init_error=None, embed_error=None):
        self._init_error = init_error
        self._embed_error = embed_error

    async def get_embedding(self, task="document", spec_id=None):
        if self._init_error:
            raise self._init_error
        return self

    async def embed_batch(self, inputs):
        if self._embed_error:
            raise self._embed_error
        return [[0.1] * 1536 for _ in inputs]


def _patch_reembed(monkeypatch, *, init_error=None, embed_error=None):
    """Isolate the job from the provider, the embedding tables and app_config."""
    from app.ai import registry as registry_module
    from app.services import config_service, embedding_storage

    monkeypatch.setattr(
        registry_module,
        "ProviderRegistry",
        lambda session: _FakeRegistry(session, init_error=init_error, embed_error=embed_error),
    )

    upserts: list[uuid.UUID] = []
    flips: list[tuple[str, str]] = []

    async def _upsert(_session, page_id, spec, vector, content_hash):
        upserts.append(page_id)

    async def _cleanup(_session, keep_spec_id):
        return 7

    class _FakeConfigService:
        def __init__(self, _session):
            pass

        async def set(self, key, value):
            flips.append((key, value))

    monkeypatch.setattr(embedding_storage, "upsert_page_embedding", _upsert)
    monkeypatch.setattr(embedding_storage, "cleanup_stale_embeddings", _cleanup)
    monkeypatch.setattr(config_service, "ConfigService", _FakeConfigService)
    return upserts, flips


def _embedding_job(fake_db, **overrides):
    from app.database.models import EmbeddingJob

    job = SimpleNamespace(
        id=uuid.uuid4(), model_spec_id=_SPEC_ID, status="pending",
        total_pages=0, done_pages=0, error_message=None,
        started_at=None, finished_at=None,
    )
    for key, value in overrides.items():
        setattr(job, key, value)
    return fake_db.seed(EmbeddingJob, job)


@pytest.mark.asyncio
async def test_reembed_unknown_spec_fails_the_job_instead_of_hanging(fake_db, monkeypatch):
    """A spec id removed from the catalog must terminate the job, not leave it `pending`.

    The admin UI polls this row; `pending` forever is the migration equivalent of a
    stranded `processing`.
    """
    job = _embedding_job(fake_db, model_spec_id="vendor/deleted-model")
    _, flips = _patch_reembed(monkeypatch)

    await worker.reembed_all_pages_task({}, str(job.id))

    assert job.status == "failed"
    assert "Unknown model spec" in job.error_message
    assert job.finished_at is not None
    assert flips == [], "the active model must not move when the job never ran"


@pytest.mark.asyncio
async def test_reembed_provider_init_failure_fails_the_job(fake_db, monkeypatch):
    """A missing API key for the new provider is a job failure, not a silent no-op."""
    job = _embedding_job(fake_db)
    _, flips = _patch_reembed(monkeypatch, init_error=RuntimeError("no api key configured"))

    await worker.reembed_all_pages_task({}, str(job.id))

    assert job.status == "failed"
    assert "Provider init failed" in job.error_message
    assert flips == []


@pytest.mark.asyncio
async def test_reembed_embedding_failure_does_not_flip_the_active_model(fake_db, monkeypatch):
    """The flip is what makes search read the new vectors — it must be all-or-nothing.

    Flipping after a partial run would point search at a table holding vectors for only
    the pages that made it, and every other page would return zero results.
    """
    job = _embedding_job(fake_db)
    fake_db.select_rows["wiki_pages"] = [_wiki_page(), _wiki_page("Expenses")]
    upserts, flips = _patch_reembed(monkeypatch, embed_error=RuntimeError("provider 500"))

    await worker.reembed_all_pages_task({}, str(job.id))

    assert job.status == "failed"
    assert "Embedding API failed" in job.error_message
    assert job.total_pages == 2
    assert upserts == []
    assert flips == []


@pytest.mark.asyncio
async def test_reembed_flips_the_active_model_only_after_every_page(fake_db, monkeypatch):
    """Positive control for the three tests above."""
    from app.services.config_service import ACTIVE_EMBEDDING_MODEL_KEY

    job = _embedding_job(fake_db)
    pages = [_wiki_page(), _wiki_page("Expenses")]
    fake_db.select_rows["wiki_pages"] = pages
    upserts, flips = _patch_reembed(monkeypatch)

    await worker.reembed_all_pages_task({}, str(job.id))

    assert job.status == "completed"
    assert job.done_pages == job.total_pages == 2
    assert sorted(map(str, upserts)) == sorted(str(page.id) for page in pages)
    assert flips == [(ACTIVE_EMBEDDING_MODEL_KEY, _SPEC_ID)]


@pytest.mark.asyncio
async def test_reembed_ignores_a_job_that_is_not_pending(fake_db, monkeypatch):
    """arq retries deliver the same job id twice; a completed migration must not re-run."""
    job = _embedding_job(fake_db, status="completed")
    _, flips = _patch_reembed(monkeypatch)

    await worker.reembed_all_pages_task({}, str(job.id))

    assert job.status == "completed"
    assert flips == []


# ---------------------------------------------------------------------------
# caption_images_task
# ---------------------------------------------------------------------------


class _FakeVision:
    def __init__(self, fail_keys=(), blank_keys=()):
        self.fail_keys = set(fail_keys)
        # A provider that SUCCEEDS but returns nothing usable. Distinct from fail_keys
        # because it is the case that used to be persisted as a real caption.
        self.blank_keys = set(blank_keys)
        self.calls: list[str] = []

    async def analyze_image(self, data, content_type, prompt=None):
        key = data.decode()
        self.calls.append(key)
        if key in self.fail_keys:
            raise RuntimeError("vision provider 500")
        if key in self.blank_keys:
            return "   "
        return f"caption for {key}"


def _patch_captioning(monkeypatch, vision):
    from app.ai import registry as registry_module
    from app.services.storage_service import storage_service

    class _Registry:
        def __init__(self, _session):
            pass

        async def get_vision(self):
            return vision

    async def _download(key):
        return key.encode()

    monkeypatch.setattr(registry_module, "ProviderRegistry", _Registry)
    monkeypatch.setattr(storage_service, "download_file_async", _download)


def _seed_images(fake_db, source_id, count):
    from app.database.models import SourceImage

    return [
        fake_db.insert(
            SourceImage(
                source_id=source_id, minio_key=f"images/{i}.png", page_number=1,
                image_index=i, content_type="image/png", size_bytes=10,
            )
        )
        for i in range(count)
    ]


@pytest.mark.asyncio
async def test_one_failing_image_does_not_lose_the_other_captions(fake_db, monkeypatch):
    """Captioning is per-image and best-effort; one 500 must not cost a whole document.

    Aborting the gather would leave every later image uncaptioned, and nothing re-runs
    this job — the wiki pages get written by the next phase either way.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    rows = _seed_images(fake_db, src.id, 3)
    vision = _FakeVision(fail_keys={"images/1.png"})
    _patch_captioning(monkeypatch, vision)

    await worker.caption_images_task({}, str(src.id))

    assert [row.caption for row in rows] == [
        "caption for images/0.png", None, "caption for images/2.png",
    ]
    assert len(vision.calls) == 3


@pytest.mark.asyncio
async def test_caption_failures_fail_the_job_but_never_the_source(fake_db, monkeypatch):
    """Two requirements that pull in opposite directions, both of which must hold.

    The job must FAIL so arq retries it: returning normally when every vision call errored
    reported a success that produced no captions, so arq recorded the job COMPLETE and
    `max_tries` never engaged (#88).

    But it must not mark the SOURCE. caption_images_task is enqueued in parallel with
    ingest_map_reduce_task, so a caption failure that wrote status="error" would clobber a
    source that went on to compile perfectly well. Captions are an enhancement; their
    absence is not an ingestion failure.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source(status="ready", progress=100))
    _seed_images(fake_db, src.id, 2)
    _patch_captioning(monkeypatch, _FakeVision(fail_keys={"images/0.png", "images/1.png"}))

    with pytest.raises(RuntimeError, match="vision call"):
        await worker.caption_images_task({}, str(src.id))

    assert src.status == "ready"
    assert src.progress == 100
    assert src.error_message is None


@pytest.mark.asyncio
async def test_partial_caption_failure_does_not_fail_the_job(fake_db, monkeypatch):
    """Only a total failure is a job failure.

    If some images captioned, the run produced real value and retrying would redo the
    successful ones; the raise is reserved for "nothing worked", which is the signal that
    the provider or config is broken rather than one image being awkward.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source(status="ready", progress=100))
    _seed_images(fake_db, src.id, 2)
    _patch_captioning(monkeypatch, _FakeVision(fail_keys={"images/0.png"}))

    await worker.caption_images_task({}, str(src.id))

    assert src.status == "ready"
    assert src.error_message is None


@pytest.mark.asyncio
async def test_captioning_is_skipped_without_a_vision_provider(fake_db, monkeypatch):
    """Vision is optional configuration; its absence is not an ingestion failure."""
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    rows = _seed_images(fake_db, src.id, 1)
    _patch_captioning(monkeypatch, None)

    await worker.caption_images_task({}, str(src.id))

    assert rows[0].caption is None
    assert src.status == "pending"


# ---------------------------------------------------------------------------
# NotebookLM tasks
# ---------------------------------------------------------------------------


def _artifact(fake_db, notebook_ref_id, **overrides):
    from app.database.models import NotebookLMArtifact

    artifact = SimpleNamespace(
        id=uuid.uuid4(), notebook_ref_id=notebook_ref_id, artifact_type="report",
        report_format="briefing_doc", artifact_id="nlm-artifact-1", task_id=None,
        status="pending", error_message=None, title="Briefing", download_url=None,
        minio_key=None, ingest_source_id=None,
    )
    for key, value in overrides.items():
        setattr(artifact, key, value)
    return fake_db.seed(NotebookLMArtifact, artifact)


def _notebook(fake_db, **source_overrides):
    from app.database.models import NotebookLMNotebook

    parent = _source(**source_overrides) if source_overrides else None
    notebook = SimpleNamespace(
        id=uuid.uuid4(), notebook_id="nlm-notebook-1", title="Q3 research",
        created_by_employee_id=uuid.uuid4(), source=parent,
    )
    fake_db.seed(NotebookLMNotebook, notebook)
    fake_db.select_rows["notebooklm_notebooks"] = [notebook]
    return notebook


@pytest.mark.asyncio
async def test_notebooklm_generate_cancellation_marks_the_artifact_failed(fake_db, monkeypatch):
    """The artifact-stuck-at-`processing` bug, driven rather than grepped.

    wait_for_completion used to be given a budget exactly equal to worker_job_timeout, so
    arq always won the race and delivered CancelledError — which `except Exception` did
    not catch. The status="processing" written before polling then stood forever and the
    UI spun with no error.
    """
    import notebooklm

    notebook = _notebook(fake_db)
    artifact = _artifact(fake_db, notebook.id, status="processing")

    class _Client:
        @staticmethod
        async def from_storage(path=None):
            raise asyncio.CancelledError()

    monkeypatch.setattr(notebooklm, "NotebookLMClient", _Client)

    with pytest.raises(asyncio.CancelledError):
        await worker.notebooklm_generate_task({}, str(artifact.id))

    assert artifact.status == "failed"
    assert artifact.error_message == "CancelledError"


@pytest.mark.asyncio
async def test_notebooklm_generate_rejects_an_unknown_artifact_type(fake_db, monkeypatch):
    """A type the dispatcher does not know must fail the artifact, not hang it."""
    import notebooklm

    notebook = _notebook(fake_db)
    artifact = _artifact(fake_db, notebook.id, artifact_type="hologram")

    class _Client:
        @staticmethod
        async def from_storage(path=None):
            class _Ctx:
                async def __aenter__(self):
                    return SimpleNamespace(artifacts=SimpleNamespace())

                async def __aexit__(self, *_exc):
                    return False

            return _Ctx()

    monkeypatch.setattr(notebooklm, "NotebookLMClient", _Client)

    with pytest.raises(ValueError, match="Unknown artifact type"):
        await worker.notebooklm_generate_task({}, str(artifact.id))

    assert artifact.status == "failed"
    assert "Unknown artifact type" in artifact.error_message


@pytest.mark.asyncio
async def test_notebooklm_generate_marks_failed_when_the_notebook_row_is_gone(fake_db):
    """Deleting the notebook while generation is queued must not strand the artifact."""
    artifact = _artifact(fake_db, uuid.uuid4())

    await worker.notebooklm_generate_task({}, str(artifact.id))

    assert artifact.status == "failed"
    assert artifact.error_message == "Notebook record not found"


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact_type", ["report", "audio"])
async def test_notebooklm_ingest_inherits_the_notebook_knowledge_type(fake_db, monkeypatch, artifact_type):
    """A derived source with no knowledge_type_id compiles into a world-readable page.

    The RBAC filters treat an empty knowledge_type_slugs array as unrestricted, so the
    binary branch omitting this field published NotebookLM output to every employee.
    """
    from app.services import notebooklm_service
    from app.services.storage_service import storage_service

    kt_id = uuid.uuid4()
    scope_id = uuid.uuid4()
    notebook = _notebook(
        fake_db, knowledge_type_id=kt_id, scope_type="workspace", scope_id=scope_id
    )
    artifact = _artifact(fake_db, notebook.id, artifact_type=artifact_type)

    async def _text(*_args, **_kwargs):
        return "# Briefing\n\nFindings."

    async def _bytes(*_args, **_kwargs):
        return b"audio-bytes"

    async def _upload(object_name, data=None, content_type=None):
        return object_name

    monkeypatch.setattr(notebooklm_service, "get_artifact_text", _text)
    monkeypatch.setattr(notebooklm_service, "get_artifact_bytes", _bytes)
    monkeypatch.setattr(storage_service, "upload_file_async", _upload)
    pool = _patch_pool(monkeypatch)

    await worker.notebooklm_ingest_artifact_task({}, str(artifact.id))

    created = fake_db.table_rows("sources")
    assert len(created) == 1
    assert created[0].knowledge_type_id == kt_id
    assert created[0].scope_type == "workspace"
    assert created[0].scope_id == scope_id
    assert artifact.ingest_source_id == created[0].id
    expected_job = "ingest_map_reduce_task" if artifact_type == "report" else "ingest_file_task"
    assert pool.enqueued == [(expected_job, (str(created[0].id),))]


@pytest.mark.asyncio
async def test_notebooklm_ingest_eager_loads_the_notebook_source(fake_db, monkeypatch):
    """`notebook.source` is default-lazy, and lazy-loading from a coroutine raises.

    Asserted on the statement object the task actually built rather than on its source
    text: a commented-out `.options(...)` still matches a string search, but it cannot
    put a selectin loader on the Select. Because notebooks are created by pushing an
    Arkon source, source_id is non-NULL in the normal case — so without the eager load,
    ingesting a NotebookLM artifact back into the wiki never worked at all.
    """
    from app.database.models import NotebookLMNotebook
    from app.services import notebooklm_service

    notebook = _notebook(fake_db, knowledge_type_id=uuid.uuid4())
    artifact = _artifact(fake_db, notebook.id)

    async def _text(*_args, **_kwargs):
        return "# Briefing\n\nFindings."

    monkeypatch.setattr(notebooklm_service, "get_artifact_text", _text)
    _patch_pool(monkeypatch)

    await worker.notebooklm_ingest_artifact_task({}, str(artifact.id))

    loaded = [
        str(element.path)
        for statement in fake_db.statements
        for option in getattr(statement, "_with_options", ())
        for element in getattr(option, "context", ())
        if ("lazy", "selectin") in (element.strategy or ())
    ]
    assert any(
        f"{NotebookLMNotebook.__name__}.source" in path for path in loaded
    ), "the notebook fetch no longer eager-loads .source — MissingGreenlet returns"


# ---------------------------------------------------------------------------
# Crons
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_temp_upload_cron_removes_only_the_stale_buffers(monkeypatch, tmp_path):
    """An in-flight upload must survive the sweep, or the ingest it belongs to dies.

    The cron runs hourly against the same directory the API is streaming uploads into, so
    the mtime cutoff is the only thing separating "crash debris" from "live buffer".
    """
    monkeypatch.chdir(tmp_path)
    temp_dir = tmp_path / "temp_uploads"
    temp_dir.mkdir()
    stale = temp_dir / "stale.zip"
    stale.write_bytes(b"old")
    os.utime(stale, (time.time() - 7200, time.time() - 7200))
    fresh = temp_dir / "in-flight.zip"
    fresh.write_bytes(b"new")

    await worker.cleanup_temp_uploads_cron({})

    assert not stale.exists()
    assert fresh.exists()


@pytest.mark.asyncio
async def test_temp_upload_cron_survives_a_file_it_cannot_delete(monkeypatch, tmp_path):
    """A cron that raises stops being scheduled; one locked file must not end the sweep."""
    monkeypatch.chdir(tmp_path)
    temp_dir = tmp_path / "temp_uploads"
    temp_dir.mkdir()
    for name in ("a.zip", "b.zip"):
        path = temp_dir / name
        path.write_bytes(b"x")
        os.utime(path, (time.time() - 7200, time.time() - 7200))

    removed: list[str] = []
    real_remove = os.remove

    def _remove(path):
        if path.endswith("a.zip"):
            raise PermissionError("in use")
        removed.append(path)
        real_remove(path)

    monkeypatch.setattr(os, "remove", _remove)

    await worker.cleanup_temp_uploads_cron({})

    assert [os.path.basename(p) for p in removed] == ["b.zip"]


@pytest.mark.asyncio
async def test_temp_upload_cron_is_a_no_op_without_the_directory(monkeypatch, tmp_path):
    """The API creates temp_uploads lazily; the cron must not crash before it exists."""
    monkeypatch.chdir(tmp_path)
    await worker.cleanup_temp_uploads_cron({})


@pytest.mark.asyncio
async def test_session_refresh_cron_swallows_client_failures(monkeypatch, tmp_path):
    """An expired NotebookLM session must not take the cron scheduler down with it.

    arq stops rescheduling a cron whose coroutine raised, so a propagating exception here
    would silently end all session refreshes until the worker restarted.
    """
    import notebooklm

    from app.services import notebooklm_service

    storage = tmp_path / "nlm"
    storage.mkdir()
    (storage / "storage_state.json").write_text("{}")
    monkeypatch.setattr(notebooklm_service, "_storage_path", lambda: storage)

    class _Client:
        @staticmethod
        async def from_storage(path=None):
            raise RuntimeError("cookies rejected")

    monkeypatch.setattr(notebooklm, "NotebookLMClient", _Client)

    await worker.notebooklm_refresh_session_cron({})


@pytest.mark.asyncio
async def test_session_refresh_cron_skips_when_no_session_was_ever_stored(monkeypatch, tmp_path):
    """A deploy with NotebookLM unconfigured must not log a failure every 30 minutes."""
    from app.services import notebooklm_service

    monkeypatch.setattr(notebooklm_service, "_storage_path", lambda: tmp_path / "missing")
    await worker.notebooklm_refresh_session_cron({})

    monkeypatch.setattr(notebooklm_service, "_storage_path", lambda: None)
    await worker.notebooklm_refresh_session_cron({})


# ---------------------------------------------------------------------------
# Coverage ratchet
# ---------------------------------------------------------------------------

# Every arq entry point registered by either worker, including crons. A new job must be
# added here *and* given an error-path test above — that is the point of the assertion.
_COVERED_JOBS = frozenset({
    "ingest_file_task",
    "ingest_url_task",
    "ingest_skill_task",
    "delete_skill_task",
    "ingest_map_reduce_task",
    "ingest_refine_task",
    "caption_images_task",
    "reembed_all_pages_task",
    "notebooklm_generate_task",
    "notebooklm_ingest_artifact_task",
    "cleanup_temp_uploads_cron",
    "notebooklm_refresh_session_cron",
})


def _registered_job_names() -> set[str]:
    names: set[str] = set()
    for settings_class in (worker.WorkerSettings, worker.SkillWorkerSettings):
        for entry in settings_class.functions:
            # arq_func() wraps the coroutine in a Function, which carries .name instead.
            names.add(getattr(entry, "__name__", None) or entry.name)
        for entry in getattr(settings_class, "cron_jobs", ()):
            names.add(entry.name.removeprefix("cron:"))
    return names


def test_every_registered_arq_job_has_a_failure_test_here():
    """Issue #61 existed because 12 jobs shipped with zero tests; this stops #13.

    Symmetric on purpose: an entry left behind after a job is deleted fails too, so the
    list cannot rot into a list of jobs that no longer exist.
    """
    registered = _registered_job_names()
    assert registered - _COVERED_JOBS == set(), (
        "new arq job with no failure-path test in this module: "
        f"{sorted(registered - _COVERED_JOBS)}"
    )
    assert _COVERED_JOBS - registered == set(), (
        "these jobs are no longer registered with any worker; drop them from "
        f"_COVERED_JOBS: {sorted(_COVERED_JOBS - registered)}"
    )


# --------------------------------------------------------------------------- #
# #122 — vision failure reported as a successful caption
#
# `AnthropicVision` propagated its exception; `OpenAIVision` and `GoogleVision` both
# `return ""` after three failed attempts. That defeated TWO earlier fixes at once:
# `if captioned == 0: raise` could never fire, and the resume filter
# `SourceImage.caption.is_(None)` stopped matching because "" is not NULL.
# --------------------------------------------------------------------------- #

def test_no_vision_provider_returns_a_blank_caption_on_a_failure_path():
    """The invariant: `analyze_image` must never hand back `""` instead of failing.

    An empty caption is indistinguishable from a successful one to every consumer.
    `caption_images_task` counted it as captioned, so `if captioned == 0: raise` could never
    fire, and the resume filter `SourceImage.caption.is_(None)` stopped matching because ""
    is not NULL — the image stayed permanently uncaptioned with nothing recording why.

    Asserted on the AST, not on source text: a commented-out `return ""` still satisfies a
    substring search, which is the same trap one level up.
    """
    import app.ai.providers.anthropic_provider as a
    import app.ai.providers.google as g
    import app.ai.providers.openai_provider as o

    for mod in (a, g, o):
        tree = ast.parse(pathlib.Path(inspect.getfile(mod)).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not (isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and fn.name == "analyze_image"):
                continue
            blanks = [
                n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Return)
                and isinstance(n.value, ast.Constant)
                and n.value.value == ""
            ]
            assert not blanks, (
                f"{mod.__name__}.analyze_image returns an empty caption at line(s) {blanks}; "
                "the worker cannot tell that from a real caption"
            )


def test_a_provider_that_swallows_its_retries_must_end_by_raising():
    """Narrower companion to the above, aimed at the shape the bug actually had.

    AnthropicVision has no try/except and no retry loop, so an exception propagates on its
    own and its final `return caption` is correct. OpenAI and Google DO catch inside a retry
    loop, and a caught exception has to be re-raised at the end or it is simply lost.
    """
    import app.ai.providers.google as g
    import app.ai.providers.openai_provider as o

    for mod in (g, o):
        tree = ast.parse(pathlib.Path(inspect.getfile(mod)).read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not (isinstance(fn, (ast.AsyncFunctionDef, ast.FunctionDef))
                    and fn.name == "analyze_image"):
                continue
            assert any(isinstance(n, ast.ExceptHandler) for n in ast.walk(fn)), (
                f"{mod.__name__} no longer catches; update this test's premise"
            )
            assert isinstance(fn.body[-1], ast.Raise), (
                f"{mod.__name__}.analyze_image catches its retries but ends in "
                f"{type(fn.body[-1]).__name__} — the caught failure goes nowhere"
            )


@pytest.mark.asyncio
async def test_a_blank_caption_is_a_failure_not_a_silent_write(fake_db, monkeypatch):
    """An empty caption written to the row is invisible to the resume filter.

    `SourceImage.caption.is_(None)` is how a retry finds work to do, so persisting "" marks
    the image done forever. Leaving the column NULL is what makes the next run pick it up.
    """
    from app.database.models import Source, SourceImage

    src = fake_db.seed(Source, _source(status="ready", progress=100))
    _seed_images(fake_db, src.id, 2)
    _patch_captioning(monkeypatch, _FakeVision(blank_keys={"images/0.png"}))

    await worker.caption_images_task({}, str(src.id))

    rows = [r for r in fake_db.table_rows(SourceImage.__tablename__)
            if getattr(r, "source_id", None) == src.id]
    # `== ""` would be vacuous: the fake returns whitespace, which is just as useless as a
    # caption and just as invisible to `caption.is_(None)`. Assert on blankness, not equality.
    blanks = [r for r in rows if r.caption is not None and not r.caption.strip()]
    assert not blanks, (
        f"{len(blanks)} blank caption(s) persisted; `caption.is_(None)` can never "
        "re-select those rows, so the images stay uncaptioned forever"
    )
