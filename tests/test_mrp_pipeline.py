"""MRP orchestration: phase sequencing, resume, and what a failed phase leaves behind.

`app/ai/mrp/pipeline.py` is 557 lines that decide, for every ingested document, which
phases run and what status the source ends on. It was imported by no test (issue #61).

The failure mode this file is mostly about is not a crash. It is a **silent success**:
run_commit_phase sets status="ready", progress=100 and explicitly clears error_message, so
a page synthesised from four of forty chunks is byte-for-byte indistinguishable in the UI
from a complete ingest. Nothing downstream re-checks it. So the tests below care less
about which exception surfaces than about one question — can a source reach `ready` with
part of the document missing, and can it reach `ready` with part of the wiki write done?
"""

import uuid
from types import SimpleNamespace

import pytest

from app.ai.mrp import pipeline
from app.ai.mrp.mapper import MIN_MAP_COVERAGE, MapCoverageError
from app.ai.mrp.mapper import run_map_phase as real_run_map_phase
from app.ai.mrp.writer import PageWriteResult

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _Source(SimpleNamespace):
    """A Source stand-in that remembers every status it was ever assigned.

    Asserting on the final value alone cannot distinguish "never claimed to be ready"
    from "was marked ready and then corrected", and the first is the property that
    matters: any observer polling mid-run would have believed the ready.
    """

    def __init__(self, **fields):
        super().__init__(status_history=[], **fields)

    def __setattr__(self, name, value):
        if name == "status":
            self.status_history.append(value)
        super().__setattr__(name, value)


def _source(**overrides):
    fields = dict(
        id=uuid.uuid4(),
        title="Employee handbook",
        file_name="handbook.pdf",
        status="processing",
        progress=56,
        progress_message=None,
        error_message=None,
        full_text="Body text",
        outline_json=None,
        page_offsets=None,
        job_id=None,
        knowledge_type_id=None,
        scope_type="global",
        scope_id=None,
        pipeline_phase=None,
        pipeline_strategy=None,
    )
    fields.update(overrides)
    return _Source(**fields)


def _plan(source_id, status="pending_review", pages=()):
    return SimpleNamespace(
        id=uuid.uuid4(),
        source_id=source_id,
        status=status,
        review_note=None,
        reviewed_at=None,
        plan_json={"pages": list(pages), "_claims": []},
    )


class _Tracker:
    def __init__(self):
        self.updates: list[tuple[int, str]] = []

    async def update(self, progress, message):
        self.updates.append((progress, message))


class _FakeLLM:
    config = SimpleNamespace(model_id="test-model")

    async def generate(self, prompt, **_kwargs):
        return "# Page\n\nEnough content to pass the writer's validation checks."


class _FakeEmbedding:
    async def embed(self, _text):
        return [0.1, 0.2, 0.3]

    async def embed_batch(self, inputs):
        return [[0.1, 0.2, 0.3] for _ in inputs]


class _FakeRegistry:
    def __init__(self, llm=None, embedding=None):
        self.llm = llm or _FakeLLM()
        self.embedding = embedding

    async def get_ingestion_llm(self):
        return self.llm

    async def get_embedding(self, task="document", spec_id=None):
        if self.embedding is None:
            raise RuntimeError("no embedding provider configured")
        return self.embedding

    async def get_active_embedding_spec_id(self):
        return None


class _FakePool:
    def __init__(self):
        self.enqueued: list[tuple] = []

    async def enqueue_job(self, name, *args):
        self.enqueued.append((name, args))
        return SimpleNamespace(job_id=f"arq-job-{len(self.enqueued)}")


def _patch_pool(monkeypatch):
    import app.worker as worker

    pool = _FakePool()

    async def _get_pool():
        return pool

    monkeypatch.setattr(worker, "get_arq_pool", _get_pool)
    return pool


class _PhaseRecorder:
    """Replaces the phase functions so the orchestration itself is what is under test.

    `phase_at_call` snapshots source.pipeline_phase as each phase is entered — the resume
    marker is only correct if it names the phase about to run, and a marker written one
    phase too early is invisible if you only look at the order the phases ran in.
    """

    def __init__(self, source=None, plan=None, page_results=(), fail_at=None):
        self.source = source
        self.plan = plan
        self.page_results = list(page_results)
        self.fail_at = fail_at
        self.calls: list[str] = []
        self.phase_at_call: dict[str, object] = {}

    def _enter(self, name):
        self.calls.append(name)
        if self.source is not None:
            self.phase_at_call[name] = self.source.pipeline_phase
        if self.fail_at == name:
            raise RuntimeError(f"{name} exploded")

    def install(self, monkeypatch):
        recorder = self

        async def _map(session, source_id, full_text, outline_json, tracker, llm, domain_hints=None):
            recorder._enter("map")
            return "standard", [SimpleNamespace(chunk_index=0, extract_json={})]

        async def _reduce(session, source, chunk_extracts, llm, embedding_provider,
                          query_embedding_provider, kt_name, kt_desc, tracker,
                          kt_extraction_hints=None):
            recorder._enter("reduce")
            return recorder.plan

        async def _refine(session, source, plan, chunk_extracts, full_text, llm,
                          embedding_provider, kt_slug, tracker, kt_extraction_hints=None):
            recorder._enter("refine")
            return recorder.page_results

        async def _verify(session, source, page_results, chunk_extracts, full_text, llm,
                          embedding_provider, tracker):
            recorder._enter("verify")
            return page_results

        async def _commit(session, source, page_results, plan, embedding_provider,
                          embedding_spec, kt_slug, tracker):
            recorder._enter("commit")
            return {"pages_created": len(page_results), "pages_updated": 0}

        monkeypatch.setattr(pipeline, "run_map_phase", _map)
        monkeypatch.setattr(pipeline, "run_reduce_phase", _reduce)
        monkeypatch.setattr(pipeline, "run_refine_phase", _refine)
        monkeypatch.setattr(pipeline, "run_verify_phase", _verify)
        monkeypatch.setattr(pipeline, "run_commit_phase", _commit)
        return self


# ---------------------------------------------------------------------------
# Phase 0-2: run_mrp_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_map_runs_before_reduce_and_the_plan_is_parked_for_review(fake_db, monkeypatch):
    """REDUCE consumes MAP's chunk extracts, and pipeline_phase is the resume marker.

    If `pipeline_phase = "reduce"` were written before MAP instead of between the two, a
    crash during MAP would resume into REDUCE with no extracts to reduce.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    plan = _plan(src.id)
    recorder = _PhaseRecorder(src, plan=plan).install(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_mrp_pipeline(
            session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
            registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
        )

    assert recorder.calls == ["map", "reduce"]
    # Not just "reduce by the time REDUCE runs" — it must still be unset while MAP runs,
    # or a crash inside MAP resumes into REDUCE with nothing to reduce.
    assert recorder.phase_at_call == {"map": None, "reduce": "reduce"}
    assert result == {"status": "plan_ready", "plan_id": str(plan.id)}
    assert "ready" not in src.status_history


@pytest.mark.asyncio
async def test_a_source_already_at_plan_review_does_not_redo_map(fake_db, monkeypatch):
    """MAP is the expensive phase — one LLM call per 12 kB chunk of the document.

    A retry after a crash in REDUCE must reuse the extracts already persisted, or every
    arq retry re-bills the whole document.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source(pipeline_phase="plan_review"))
    plan = _plan(src.id)
    fake_db.select_rows["source_compilation_plans"] = [plan]
    recorder = _PhaseRecorder(src, plan=plan).install(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_mrp_pipeline(
            session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
            registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
        )

    assert recorder.calls == []
    assert result == {"status": "plan_ready", "plan_id": str(plan.id)}


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["refine", "verify", "commit"])
async def test_a_source_past_reduce_is_left_alone(fake_db, monkeypatch, phase):
    """Re-running MAP over a source whose pages are being written would fight REFINE.

    Both halves share the source row; re-planning underneath an in-flight REFINE would
    replace the plan it is reading from.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source(pipeline_phase=phase))
    recorder = _PhaseRecorder(src).install(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_mrp_pipeline(
            session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
            registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
        )

    assert result == {"status": f"already_in_{phase}"}
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_zero_surviving_chunks_stops_before_reduce(fake_db, monkeypatch):
    """Planning from an empty extract set produces a confident page about nothing.

    REDUCE would happily emit a plan from zero entities, REFINE would write it, and COMMIT
    would mark the source `ready`.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    recorder = _PhaseRecorder(src).install(monkeypatch)

    async def _empty_map(*_args, **_kwargs):
        recorder.calls.append("map")
        return "standard", []

    monkeypatch.setattr(pipeline, "run_map_phase", _empty_map)

    async with fake_db.factory() as session:
        with pytest.raises(ValueError, match="no successful chunks"):
            await pipeline.run_mrp_pipeline(
                session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
                registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
            )

    assert recorder.calls == ["map"]
    assert "ready" not in src.status_history


@pytest.mark.asyncio
async def test_an_auto_approved_plan_hands_straight_off_to_refine(fake_db, monkeypatch):
    """Auto-approve must both flip the plan and enqueue the job.

    Flipping the plan without enqueueing leaves an approved plan that nothing will ever
    compile — the source sits at `processing` with a "compiling wiki pages" message and no
    job behind it.
    """
    from app.database.models import Source, SourceCompilationPlan

    src = fake_db.seed(Source, _source())
    plan = _plan(src.id)
    fake_db.seed(SourceCompilationPlan, plan)
    _PhaseRecorder(src, plan=plan).install(monkeypatch)
    pool = _patch_pool(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_mrp_pipeline(
            session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
            registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
            auto_approve=True,
        )

    assert result == {"status": "plan_auto_approved", "job_id": "arq-job-1"}
    assert plan.status == "approved"
    assert plan.review_note == "Auto-approved"
    assert src.status == "processing"
    assert pool.enqueued == [("ingest_refine_task", (str(src.id),))]


# ---------------------------------------------------------------------------
# The MAP coverage floor, end to end through the worker
# ---------------------------------------------------------------------------


def _outlined_document(section_count: int):
    """A document with one oversized markdown section per chunk, plus its outline."""
    from app.services.source_outline import assemble_full_text, build_outline

    pages = [
        {
            "content": f"# Section {index} MARK{index}\n\n" + f"s{index} " * 3_000,
            "page_number": index + 1,
        }
        for index in range(section_count)
    ]
    full_text, page_offsets = assemble_full_text(pages)
    return full_text, build_outline(pages), page_offsets


class _FlakyLLM:
    """Fails extraction for the chunks whose section markers are listed in `fail_marks`."""

    config = SimpleNamespace(model_id="test-model")

    def __init__(self, fail_marks):
        self.fail_marks = list(fail_marks)
        self.calls = 0

    async def generate(self, prompt, **_kwargs):
        self.calls += 1
        for mark in self.fail_marks:
            if f"# Section {mark} MARK{mark}" in prompt:
                raise RuntimeError("provider 429: rate limited")
        return (
            '{"entities": [{"name": "Acme", "local_offset": 0}], '
            '"concepts": [], "claims": [], "summary": "Section summary"}'
        )


@pytest.mark.asyncio
async def test_a_document_that_mostly_failed_to_extract_never_becomes_ready(fake_db, monkeypatch):
    """The silent-success case, driven from the arq entry point down.

    A rate-limit storm that kills most of a document used to produce a confidently-worded
    page from the surviving fraction, and run_commit_phase then set status="ready",
    progress=100 and cleared error_message — so nothing in the UI or the activity log
    showed that data had been lost. verifier.check_coverage cannot catch it either,
    because it counts entities from the extracts that *did* survive.

    Only raising out of MAP reaches the worker's handler. This asserts the whole chain:
    coverage below the floor -> MapCoverageError -> REDUCE never runs -> the source ends
    `error` and was never once marked `ready`.
    """
    import app.worker as worker
    from app.ai import registry as registry_module
    from app.database.models import Source

    full_text, outline, offsets = _outlined_document(5)
    src = fake_db.seed(
        Source, _source(full_text=full_text, outline_json=outline, page_offsets=offsets)
    )
    # 1 of 5 chunks survives — 20%, well below the floor.
    llm = _FlakyLLM(fail_marks=[1, 2, 3, 4])
    recorder = _PhaseRecorder(src).install(monkeypatch)
    monkeypatch.setattr(pipeline, "run_map_phase", real_run_map_phase)
    monkeypatch.setattr(registry_module, "ProviderRegistry", lambda _s: _FakeRegistry(llm))

    with pytest.raises(MapCoverageError):
        await worker.ingest_map_reduce_task({}, str(src.id))

    assert recorder.calls == [], "REDUCE ran on a document that mostly failed to extract"
    assert src.status == "error"
    assert "ready" not in src.status_history
    assert src.progress == 0
    assert f"below the {MIN_MAP_COVERAGE:.0%} minimum" in src.error_message
    assert "1/5 chunks" in src.error_message
    assert "429" in src.error_message, "the underlying provider failure must be reported"


@pytest.mark.asyncio
async def test_a_document_that_mostly_extracted_is_allowed_through(fake_db, monkeypatch):
    """Positive control: the floor tolerates a couple of unparseable chunks.

    A floor that rejected any single failed chunk would make large documents impossible to
    ingest, which is why MIN_MAP_COVERAGE is 0.70 and not 1.0.
    """
    import app.worker as worker
    from app.ai import registry as registry_module
    from app.database.models import Source

    full_text, outline, offsets = _outlined_document(5)
    src = fake_db.seed(
        Source, _source(full_text=full_text, outline_json=outline, page_offsets=offsets)
    )
    llm = _FlakyLLM(fail_marks=[4])  # 4 of 5 = 80%
    plan = _plan(src.id)
    recorder = _PhaseRecorder(src, plan=plan).install(monkeypatch)
    monkeypatch.setattr(pipeline, "run_map_phase", real_run_map_phase)
    monkeypatch.setattr(registry_module, "ProviderRegistry", lambda _s: _FakeRegistry(llm))

    result = await worker.ingest_map_reduce_task({}, str(src.id))

    assert recorder.calls == ["reduce"]
    assert result["status"] == "plan_ready"
    assert src.status == "plan_ready"
    done = [r for r in fake_db.table_rows("source_chunk_extracts") if r.status == "done"]
    assert len(done) == 4


# ---------------------------------------------------------------------------
# Phase 3-5: run_refine_pipeline
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refine_requires_a_plan_that_exists(fake_db, monkeypatch):
    """Approval races deletion; compiling without a plan would write arbitrary pages."""
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    recorder = _PhaseRecorder(src).install(monkeypatch)

    async with fake_db.factory() as session:
        with pytest.raises(ValueError, match="No compilation plan"):
            await pipeline.run_refine_pipeline(
                session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
                registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
            )

    assert recorder.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending_review", "rejected"])
async def test_refine_refuses_an_unapproved_plan(fake_db, monkeypatch, status):
    """Human review is the only gate on what reaches the wiki.

    Anything that can enqueue ingest_refine_task — an arq retry, a stale job, a direct
    call — would otherwise bypass approval entirely.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    fake_db.select_rows["source_compilation_plans"] = [_plan(src.id, status=status)]
    recorder = _PhaseRecorder(src).install(monkeypatch)

    async with fake_db.factory() as session:
        with pytest.raises(ValueError, match="not approved"):
            await pipeline.run_refine_pipeline(
                session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
                registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
            )

    assert recorder.calls == []
    assert "ready" not in src.status_history


@pytest.mark.asyncio
async def test_refine_verify_commit_run_in_order_with_phase_markers(fake_db, monkeypatch):
    """pipeline_phase is what a retry resumes from, so it must lead each phase, not trail.

    A marker written after the phase finished would make a crash mid-REFINE resume into
    REFINE again from the start — acceptable — but a marker written too early would skip
    it, which is not.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    plan = _plan(src.id, status="approved")
    fake_db.select_rows["source_compilation_plans"] = [plan]
    recorder = _PhaseRecorder(
        src, plan=plan, page_results=[_page_write_result()]
    ).install(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_refine_pipeline(
            session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
            registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
        )

    assert recorder.calls == ["refine", "verify", "commit"]
    assert recorder.phase_at_call == {
        "refine": "refine", "verify": "verify", "commit": "commit",
    }
    assert plan.status == "in_progress"
    assert result == {"pages_created": 1, "pages_updated": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_phase", ["refine", "verify"])
async def test_a_failing_phase_never_reaches_commit(fake_db, monkeypatch, failing_phase):
    """COMMIT is the only writer of `ready`; it must not run after a phase failed.

    The pages REFINE produced live in memory only, so an exception before COMMIT leaves
    the wiki untouched — the correct outcome. What must not happen is COMMIT running
    anyway and marking a source complete from a partial page set.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    plan = _plan(src.id, status="approved")
    fake_db.select_rows["source_compilation_plans"] = [plan]
    recorder = _PhaseRecorder(src, plan=plan, fail_at=failing_phase).install(monkeypatch)

    async with fake_db.factory() as session:
        with pytest.raises(RuntimeError, match=failing_phase):
            await pipeline.run_refine_pipeline(
                session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
                registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
            )

    assert "commit" not in recorder.calls
    assert "ready" not in src.status_history
    assert src.progress != 100


@pytest.mark.asyncio
async def test_a_resumed_source_reruns_refine_because_pages_are_in_memory_only(fake_db, monkeypatch):
    """Resuming at `verify` must go back to REFINE, not verify an empty page list.

    PageWriteResults are never persisted. Skipping straight to VERIFY on a resume would
    hand COMMIT zero pages and still mark the source `ready` — a complete document
    replaced by nothing.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source(pipeline_phase="verify"))
    plan = _plan(src.id, status="approved")
    fake_db.select_rows["source_compilation_plans"] = [plan]
    recorder = _PhaseRecorder(
        src, plan=plan, page_results=[_page_write_result()]
    ).install(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_refine_pipeline(
            session=session, source=src, full_text=src.full_text, tracker=_Tracker(),
            registry=_FakeRegistry(), kt_slug=None, kt_name=None, kt_desc=None,
        )

    assert recorder.calls == ["refine", "verify", "commit"]
    assert result["pages_created"] == 1


# ---------------------------------------------------------------------------
# Phase 5: run_commit_phase
# ---------------------------------------------------------------------------


def _page_write_result(slug="concept/leave-policy", action="CREATE", title="Leave policy"):
    return PageWriteResult(
        slug=slug,
        title=title,
        page_type="concept",
        action=action,
        content_md=f"# {title}\n\nEmployees get 12 days of annual leave.",
        summary="Annual leave entitlement.",
        entity_names=[title],
    )


class _WikiRecorder:
    """Replaces the wiki_service writes COMMIT performs, keeping COMMIT's own logic real."""

    def __init__(self, fail_on_slug=None):
        self.created: list[str] = []
        self.contributions: list[str] = []
        self.written: dict[str, str] = {}  # slug -> content_md as COMMIT passed it on
        self.logs: list[str] = []
        self.index_rebuilds = 0
        self.fail_on_slug = fail_on_slug

    def install(self, monkeypatch):
        from app.ai import registry as registry_module
        from app.services import wiki_service

        recorder = self

        async def _list_pages(_session, **_kwargs):
            return []

        async def _get_page_by_slug(_session, _slug, **_kwargs):
            return None

        async def _apply_create(_session, *, slug, title, page_type, content_md, summary, **_kw):
            if slug == recorder.fail_on_slug:
                raise RuntimeError(f"apply_create failed for {slug}")
            recorder.created.append(slug)
            recorder.written[slug] = content_md
            return SimpleNamespace(
                id=uuid.uuid4(), slug=slug, title=title, page_type=page_type,
                content_md=content_md, summary=summary, provenance_complete=False,
            )

        async def _upsert_contribution(_session, page, _source_id, *_args, **_kwargs):
            recorder.contributions.append(page.slug)

        async def _regenerate_index(_session, **_kwargs):
            recorder.index_rebuilds += 1

        async def _append_log(_session, entry, **_kwargs):
            recorder.logs.append(entry)

        monkeypatch.setattr(wiki_service, "list_pages", _list_pages)
        monkeypatch.setattr(wiki_service, "get_page_by_slug", _get_page_by_slug)
        monkeypatch.setattr(wiki_service, "apply_create", _apply_create)
        monkeypatch.setattr(wiki_service, "upsert_source_contribution", _upsert_contribution)
        monkeypatch.setattr(wiki_service, "regenerate_index", _regenerate_index)
        monkeypatch.setattr(wiki_service, "append_log", _append_log)
        monkeypatch.setattr(registry_module, "ProviderRegistry", lambda _s: _FakeRegistry())
        return self


@pytest.mark.asyncio
async def test_commit_marks_the_source_ready_and_clears_the_error(fake_db, monkeypatch):
    """The definition of "silent success" — what every test above asserts must NOT happen.

    Kept as a positive control: `ready` + progress 100 + error_message cleared is reachable
    only from the end of COMMIT, after every page in the plan was written and the index
    regenerated. If this test breaks, the `"ready" not in status_history` assertions
    elsewhere stop meaning anything.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source(error_message="a previous attempt failed"))
    plan = _plan(src.id, status="approved")
    wiki = _WikiRecorder().install(monkeypatch)

    async with fake_db.factory() as session:
        result = await pipeline.run_commit_phase(
            session=session, source=src, page_results=[_page_write_result()], plan=plan,
            embedding_provider=None, embedding_spec=None, kt_slug="hr", tracker=_Tracker(),
        )

    assert result == {"pages_created": 1, "pages_updated": 0}
    assert src.status == "ready"
    assert src.progress == 100
    assert src.error_message is None
    assert src.pipeline_phase == "commit"
    assert plan.status == "done"
    assert wiki.created == ["concept/leave-policy"]
    assert wiki.index_rebuilds == 1
    assert wiki.logs and "+1 created" in wiki.logs[0]
    assert fake_db.commits == 1


@pytest.mark.asyncio
async def test_a_page_that_fails_to_write_aborts_the_whole_commit(fake_db, monkeypatch):
    """COMMIT is one transaction: a half-written plan must not be committed as complete.

    The earlier pages have been flushed by the time page 2 fails, so the only thing
    keeping them out of the wiki is that nothing commits — and the source must not be
    marked `ready` for a document whose second page does not exist.
    """
    from app.database.models import Source

    src = fake_db.seed(Source, _source())
    plan = _plan(src.id, status="approved")
    wiki = _WikiRecorder(fail_on_slug="concept/second").install(monkeypatch)
    pages = [
        _page_write_result(slug="concept/first", title="First"),
        _page_write_result(slug="concept/second", title="Second"),
    ]

    async with fake_db.factory() as session:
        with pytest.raises(RuntimeError, match="apply_create failed"):
            await pipeline.run_commit_phase(
                session=session, source=src, page_results=pages, plan=plan,
                embedding_provider=None, embedding_spec=None, kt_slug=None,
                tracker=_Tracker(),
            )

    assert wiki.created == ["concept/first"]
    assert wiki.index_rebuilds == 0, "the index was regenerated from a half-written plan"
    assert fake_db.commits == 0, "a partial commit reached the database"
    assert src.status != "ready"
    assert "ready" not in src.status_history
    assert plan.status == "approved"
    assert src.progress != 100


@pytest.mark.asyncio
async def test_commit_strips_image_markers_that_point_at_nothing(fake_db, monkeypatch):
    """A hallucinated `image://<uuid>` renders as a broken image on the live page.

    The writer is prompted with real ids but invents plausible ones; only the ids actually
    present in source_images may survive into content_md.
    """
    from app.database.models import Source, SourceImage

    src = fake_db.seed(Source, _source())
    real = fake_db.insert(
        SourceImage(
            source_id=src.id, minio_key="images/0.png", page_number=1, image_index=0,
            content_type="image/png", size_bytes=10,
        )
    )
    invented = uuid.uuid4()
    page = _page_write_result()
    page.content_md += f"\n\n![real](image://{real.id})\n\n![fake](image://{invented})"
    wiki = _WikiRecorder().install(monkeypatch)

    async with fake_db.factory() as session:
        await pipeline.run_commit_phase(
            session=session, source=src, page_results=[page], plan=None,
            embedding_provider=None, embedding_spec=None, kt_slug=None, tracker=_Tracker(),
        )

    committed = wiki.written["concept/leave-policy"]
    assert f"image://{real.id}" in committed
    assert str(invented) not in committed
