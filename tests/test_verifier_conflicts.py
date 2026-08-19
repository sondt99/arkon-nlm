"""Phase 4 (VERIFY) conflict and coverage detection (issue #61).

`verifier.py` was imported by exactly one test, which asserted that
CONFLICT_SIM_THRESHOLD equals the setting it is assigned from. That cannot fail for any
reason connected to conflict detection: the threshold could be compared with the wrong
operator, applied to the wrong side of the pair, or skipped entirely and the assertion
would still hold. These tests drive check_conflicts across the threshold instead.

The second theme here is that VERIFY is *advisory*. It runs after REFINE and before
COMMIT on a pipeline that has already spent minutes of LLM time, so a diagnostic that
raises would throw that work away and mark the source `error` — for a page that is
perfectly committable. Every failure mode below therefore has to degrade to "no conflict
reported" — but it must say so, which is the third theme: a swallowed 429 and a clean
verdict used to be the same observable outcome (#90).

The fourth theme is that a detected conflict has to leave the worker's stdout (#95). The
test that pinned the old behaviour — the conflict list computed and dropped — is now
test_a_detected_conflict_is_written_into_the_page_and_the_log.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest

from app.ai.mrp import verifier
from app.ai.mrp.writer import PageWriteResult

THRESHOLD = verifier.CONFLICT_SIM_THRESHOLD


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


def _page_result(slug="concept/leave-policy", title="Leave policy"):
    return PageWriteResult(
        slug=slug,
        title=title,
        page_type="concept",
        action="CREATE",
        content_md=f"# {title}\n\nEmployees get 12 days of annual leave.",
        summary="Annual leave entitlement.",
        entity_names=[title],
    )


def _kb_page(slug="concept/annual-leave"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        slug=slug,
        title="Annual leave",
        content_md="Employees get 15 days of annual leave.",
    )


class _FakeEmbedding:
    def __init__(self, fail_slugs=()):
        self.fail_slugs = set(fail_slugs)
        self.calls: list[str] = []

    async def embed(self, text):
        self.calls.append(text)
        for slug in self.fail_slugs:
            if slug in text:
                raise RuntimeError("embedding provider 500")
        return [0.1, 0.2, 0.3]


class _FakeLLM:
    """Returns a canned verdict; records every prompt so `never called` is assertable."""

    def __init__(self, reply=None, error=None):
        self.reply = reply if reply is not None else json.dumps(
            {"contradicts": True, "description": "12 days vs 15 days"}
        )
        self.error = error
        self.prompts: list[str] = []

    async def generate(self, prompt, system=None, temperature=0.0, **_kwargs):
        self.prompts.append(prompt)
        if self.error is not None:
            raise self.error
        return self.reply


class _Tracker:
    def __init__(self):
        self.updates: list[tuple[int, str]] = []

    async def update(self, progress, message):
        self.updates.append((progress, message))


def _patch_neighbours(monkeypatch, hits):
    """Stand in for the pgvector neighbour search. `hits` is [(page, similarity)]."""
    from app.services import wiki_service

    captured = {}

    async def _search(session, vector, top_k=10, scope_type="global", scope_id=None, **_kwargs):
        captured["scope_type"] = scope_type
        captured["scope_id"] = scope_id
        captured["top_k"] = top_k
        return list(hits)

    monkeypatch.setattr(wiki_service, "search_pages_semantic", _search)
    return captured


def _source(scope_type="global", scope_id=None):
    return SimpleNamespace(id=uuid.uuid4(), scope_type=scope_type, scope_id=scope_id)


# ---------------------------------------------------------------------------
# 4.2 — the threshold itself
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_similar_neighbour_is_checked_and_reported(monkeypatch):
    """Above the threshold, a contradicting neighbour has to come back as a conflict.

    This is the only path that ever surfaces "the new page disagrees with the KB", so if
    it silently returns nothing, contradictory pages land in the wiki unflagged.
    """
    neighbour = _kb_page()
    _patch_neighbours(monkeypatch, [(neighbour, THRESHOLD + 0.05)])
    llm = _FakeLLM()

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert len(conflicts) == 1
    assert conflicts[0]["new_slug"] == "concept/leave-policy"
    assert conflicts[0]["existing_slug"] == neighbour.slug
    assert conflicts[0]["similarity"] == THRESHOLD + 0.05
    assert conflicts[0]["description"] == "12 days vs 15 days"
    assert len(llm.prompts) == 1
    assert neighbour.content_md in llm.prompts[0]


@pytest.mark.asyncio
async def test_a_distant_neighbour_never_reaches_the_llm(monkeypatch):
    """Below the threshold the LLM must not be called at all.

    top_k=3 neighbours per page times every page in a plan is a per-ingest cost, and a
    fact-checking prompt carries 3 kB of content on both sides. The similarity gate is
    what keeps VERIFY from doubling the pipeline's token spend on unrelated pages.
    """
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD - 0.05)])
    llm = _FakeLLM()

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert conflicts == []
    assert llm.prompts == [], "a sub-threshold neighbour was sent for fact-checking"


@pytest.mark.asyncio
async def test_the_threshold_itself_counts_as_similar(monkeypatch):
    """The comparison is `>=`; a `>` would make the configured value unreachable."""
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD)])
    llm = _FakeLLM()

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert len(conflicts) == 1
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_a_page_is_never_compared_against_itself(monkeypatch):
    """An UPDATE's own KB row is its nearest neighbour by construction.

    Without the slug guard every re-ingest of an existing page would fact-check the page
    against its previous revision at ~1.0 similarity — an LLM call per page per run, and
    a conflict report for any edit that changed a number.
    """
    page_result = _page_result()
    same_slug = _kb_page(slug=page_result.slug)
    _patch_neighbours(monkeypatch, [(same_slug, 0.99)])
    llm = _FakeLLM()

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[page_result],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert conflicts == []
    assert llm.prompts == []


@pytest.mark.asyncio
async def test_neighbour_search_stays_inside_the_source_scope(monkeypatch):
    """Cross-scope neighbours would leak workspace content into a global diagnostic.

    The conflict description is logged and returned with both slugs in it, so searching
    outside the source's scope would put another workspace's page titles in front of
    whoever reads the ingest log.
    """
    workspace_id = uuid.uuid4()
    captured = _patch_neighbours(monkeypatch, [])

    await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=_FakeLLM(),
        source=_source(scope_type="workspace", scope_id=workspace_id),
    )

    assert captured == {"scope_type": "workspace", "scope_id": workspace_id, "top_k": 3}


@pytest.mark.asyncio
async def test_a_missing_scope_type_falls_back_to_global(monkeypatch):
    """`source.scope_type` is nullable; None would search no scope at all."""
    captured = _patch_neighbours(monkeypatch, [])

    await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=_FakeLLM(),
        source=_source(scope_type=None),
    )

    assert captured["scope_type"] == "global"


# ---------------------------------------------------------------------------
# 4.2 — verdict handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agreement_is_not_reported_as_a_conflict(monkeypatch):
    """Similarity alone is not disagreement — most near-duplicates are consistent.

    Reporting on similarity would flag every page that merely overlaps in topic, and the
    signal would be ignored within a week.
    """
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])
    llm = _FakeLLM(reply=json.dumps({"contradicts": False, "description": "consistent"}))

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert conflicts == []
    assert len(llm.prompts) == 1


@pytest.mark.asyncio
async def test_a_fenced_json_verdict_is_still_parsed(monkeypatch):
    """Every provider in the catalog wraps JSON in ``` fences at least sometimes.

    Treating a fenced reply as unparseable would make conflict detection silently
    provider-dependent.
    """
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])
    llm = _FakeLLM(
        reply='```json\n{"contradicts": true, "description": "days differ"}\n```'
    )

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert len(conflicts) == 1
    assert conflicts[0]["description"] == "days differ"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    ["I think they disagree, actually.", "", "{unterminated"],
    ids=["prose", "empty", "broken-json"],
)
async def test_an_unparseable_verdict_is_dropped_not_raised(monkeypatch, reply):
    """A chatty model must not fail the pipeline from an advisory check.

    Phase 4 runs after minutes of REFINE work; an exception here would discard all of it
    and mark the source `error` even though the pages were fine.
    """
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=_FakeLLM(reply=reply),
        source=_source(),
    )

    assert conflicts == []


@pytest.mark.asyncio
async def test_a_timing_out_fact_check_does_not_fail_the_phase(monkeypatch):
    """The verdict call is wrapped in wait_for; the timeout must stay swallowed."""
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=_FakeLLM(error=asyncio.TimeoutError()),
        source=_source(),
    )

    assert conflicts == []


@pytest.mark.asyncio
async def test_one_pages_embedding_failure_does_not_stop_the_others(monkeypatch):
    """Per-page isolation: a rate-limited embed on page 1 must not skip pages 2..n.

    A plan routinely contains 20+ pages and the embedding provider is the component most
    likely to rate-limit mid-run, so aborting the loop would mean the conflict check
    effectively never runs on a busy instance.
    """
    first = _page_result(slug="concept/first", title="First")
    second = _page_result(slug="concept/second", title="Second")
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[first, second],
        embedding_provider=_FakeEmbedding(fail_slugs=["First"]),
        llm=_FakeLLM(),
        source=_source(),
    )

    assert [c["new_slug"] for c in conflicts] == ["concept/second"]


@pytest.mark.asyncio
async def test_every_similar_neighbour_is_checked_not_just_the_closest(monkeypatch):
    """top_k=3, and the contradiction is as likely to be in the 3rd hit as the 1st."""
    neighbours = [
        (_kb_page(slug=f"concept/kb-{index}"), THRESHOLD + 0.01 * index)
        for index in range(3)
    ]
    _patch_neighbours(monkeypatch, neighbours)
    llm = _FakeLLM()

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert [c["existing_slug"] for c in conflicts] == [
        "concept/kb-0", "concept/kb-1", "concept/kb-2",
    ]
    assert len(llm.prompts) == 3


# ---------------------------------------------------------------------------
# 4.1 — coverage check
# ---------------------------------------------------------------------------


def _extract(*entity_names):
    return SimpleNamespace(
        extract_json={"entities": [{"name": name} for name in entity_names]}
    )


def test_a_repeatedly_mentioned_entity_with_no_page_is_flagged():
    """The signal that REDUCE dropped something the document kept talking about."""
    extracts = [_extract("Bảo hiểm y tế") for _ in range(3)]

    uncovered = verifier.check_coverage(extracts, [], min_mentions=3)

    assert uncovered == ["bảo hiểm y tế"]


def test_an_entity_mentioned_once_is_not_flagged():
    """Below min_mentions is noise: a document names dozens of things in passing."""
    assert verifier.check_coverage([_extract("Acme Corp")], [], min_mentions=3) == []


def test_an_entity_covered_by_a_page_title_is_not_flagged():
    """Coverage is matched case-insensitively against titles as well as entity_names.

    A page named for the entity covers it even when the writer did not echo the name into
    entity_names, and flagging it would train readers to ignore the warning.
    """
    extracts = [_extract("Leave Policy") for _ in range(4)]
    page = _page_result(title="Leave policy")
    page.entity_names = []

    assert verifier.check_coverage(extracts, [page], min_mentions=3) == []


def test_a_chunk_with_no_extract_json_is_skipped():
    """`extract_json` is NULL for rows that errored, and NULL is not iterable."""
    extracts = [SimpleNamespace(extract_json=None), _extract("Acme"), _extract("Acme")]

    assert verifier.check_coverage(extracts, [], min_mentions=2) == ["acme"]


# ---------------------------------------------------------------------------
# Phase orchestrator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_phase_returns_pages_untouched_when_the_check_explodes(monkeypatch):
    """VERIFY is a diagnostic; a crash inside it must not cost the whole ingest.

    run_verify_phase is what stands between an exception in conflict detection and
    ingest_refine_task's handler, which would mark the source `error` and discard pages
    that were already written.
    """
    async def _explode(*_args, **_kwargs):
        raise RuntimeError("pgvector index missing")

    monkeypatch.setattr(verifier, "check_conflicts", _explode)
    pages = [_page_result()]
    tracker = _Tracker()

    returned = await verifier.run_verify_phase(
        session=object(),
        source=_source(),
        page_results=pages,
        chunk_extracts=[],
        full_text="Body",
        llm=_FakeLLM(),
        embedding_provider=_FakeEmbedding(),
        tracker=tracker,
    )

    assert returned == pages
    assert [progress for progress, _ in tracker.updates] == [88, 91]


def _patch_activity_log(monkeypatch):
    """Capture wiki_service.append_log; VERIFY writes the conflict summary through it."""
    from app.services import wiki_service

    entries: list[str] = []

    async def _append(_session, entry, **_kwargs):
        entries.append(entry)

    monkeypatch.setattr(wiki_service, "append_log", _append)
    return entries


def _conflict(new_slug="concept/leave-policy", existing_slug="concept/annual-leave",
              description="12 days vs 15 days", similarity=0.95):
    return {
        "new_slug": new_slug,
        "existing_slug": existing_slug,
        "similarity": similarity,
        "description": description,
    }


def _stub_conflicts(monkeypatch, conflicts):
    async def _found(*_args, **_kwargs):
        return list(conflicts)

    monkeypatch.setattr(verifier, "check_conflicts", _found)


@pytest.mark.asyncio
async def test_a_detected_conflict_is_written_into_the_page_and_the_log(monkeypatch):
    """Replaces the characterisation test that pinned the conflict list being dropped (#95).

    The module docstring promised flags "in logs and in the page content (markers)" and only
    the first half happened, so a page contradicting the KB was committed unmarked. Both
    halves are asserted here: COMMIT persists `content_md` verbatim, so a marker in the body
    is what makes the contradiction reach the API, the reviewer UI and search without a
    schema change, and the activity-log line is what an ingest audit reads.
    """
    entries = _patch_activity_log(monkeypatch)
    _stub_conflicts(monkeypatch, [_conflict()])
    page = _page_result()

    returned = await verifier.run_verify_phase(
        session=object(),
        source=_source(),
        page_results=[page],
        chunk_extracts=[],
        full_text="Body",
        llm=_FakeLLM(),
        embedding_provider=_FakeEmbedding(),
        tracker=_Tracker(),
    )

    assert returned[0] is page
    body = returned[0].content_md
    assert verifier.CONFLICT_MARKER_START in body
    assert verifier.CONFLICT_MARKER_END in body
    assert "concept/annual-leave" in body
    assert "12 days vs 15 days" in body
    # The original prose survives; the marker is appended, not substituted.
    assert "Employees get 12 days of annual leave." in body

    assert len(entries) == 1
    assert "concept/leave-policy" in entries[0]
    assert "concept/annual-leave" in entries[0]


@pytest.mark.asyncio
async def test_re_ingest_replaces_the_marker_instead_of_stacking_another(monkeypatch):
    """Every ingest re-runs the check, so the previous verdict is superseded, not additive.

    Without this a page re-ingested weekly accumulates one warning block per run and the
    body grows without bound.
    """
    _patch_activity_log(monkeypatch)
    _stub_conflicts(monkeypatch, [_conflict()])
    page = _page_result()

    for _ in range(3):
        await verifier.run_verify_phase(
            session=object(), source=_source(), page_results=[page], chunk_extracts=[],
            full_text="Body", llm=_FakeLLM(), embedding_provider=_FakeEmbedding(),
            tracker=_Tracker(),
        )

    assert page.content_md.count(verifier.CONFLICT_MARKER_START) == 1
    assert page.content_md.count(verifier.CONFLICT_MARKER_END) == 1


@pytest.mark.asyncio
async def test_a_page_that_is_no_longer_contradicted_loses_its_marker(monkeypatch):
    """A stale warning is worse than none — it trains reviewers to ignore the marker."""
    _patch_activity_log(monkeypatch)
    page = _page_result()

    _stub_conflicts(monkeypatch, [_conflict()])
    await verifier.run_verify_phase(
        session=object(), source=_source(), page_results=[page], chunk_extracts=[],
        full_text="Body", llm=_FakeLLM(), embedding_provider=_FakeEmbedding(),
        tracker=_Tracker(),
    )
    assert verifier.CONFLICT_MARKER_START in page.content_md

    _stub_conflicts(monkeypatch, [])
    await verifier.run_verify_phase(
        session=object(), source=_source(), page_results=[page], chunk_extracts=[],
        full_text="Body", llm=_FakeLLM(), embedding_provider=_FakeEmbedding(),
        tracker=_Tracker(),
    )

    assert verifier.CONFLICT_MARKER_START not in page.content_md
    assert "Employees get 12 days of annual leave." in page.content_md


@pytest.mark.asyncio
async def test_a_clean_page_is_left_byte_identical(monkeypatch):
    """No conflicts must mean no edit at all, not a normalising rewrite of the body."""
    _patch_activity_log(monkeypatch)
    _stub_conflicts(monkeypatch, [])
    page = _page_result()
    original = page.content_md

    await verifier.run_verify_phase(
        session=object(), source=_source(), page_results=[page], chunk_extracts=[],
        full_text="Body", llm=_FakeLLM(), embedding_provider=_FakeEmbedding(),
        tracker=_Tracker(),
    )

    assert page.content_md == original


@pytest.mark.asyncio
async def test_nothing_is_logged_when_the_check_found_and_failed_nothing(monkeypatch):
    """A per-ingest "no conflicts" line would bury the lines that matter."""
    entries = _patch_activity_log(monkeypatch)
    _stub_conflicts(monkeypatch, [])

    await verifier.run_verify_phase(
        session=object(), source=_source(), page_results=[_page_result()],
        chunk_extracts=[], full_text="Body", llm=_FakeLLM(),
        embedding_provider=_FakeEmbedding(), tracker=_Tracker(),
    )

    assert entries == []


def test_a_conflict_description_cannot_escape_the_marker_block():
    """`description` is LLM output about an uploaded file — untrusted twice over.

    It is rendered into a wiki page body inside a blockquote between two HTML-comment
    anchors. A description carrying `-->` would close the end anchor, and one carrying
    newlines would leave the blockquote and forge headings of its own.
    """
    rendered = verifier.render_conflict_marker([_conflict(
        description="ends here --> <!-- \n\n# Injected heading\n\nBody text",
    )])

    assert rendered.count(verifier.CONFLICT_MARKER_END) == 1
    assert rendered.endswith(verifier.CONFLICT_MARKER_END)
    assert "-->" not in rendered.replace(verifier.CONFLICT_MARKER_START, "").replace(
        verifier.CONFLICT_MARKER_END, ""
    )
    assert "\n# Injected heading" not in rendered
    assert "Injected heading" in rendered
    # Every description line stays inside the blockquote.
    body_lines = rendered.splitlines()[1:-1]
    assert all(line.startswith(">") for line in body_lines), body_lines


def test_an_overlong_description_is_capped():
    """A model asked for a "string" can return a page of prose; the body is not the place."""
    rendered = verifier.render_conflict_marker([_conflict(description="x" * 5_000)])

    assert len(rendered) < 1_000


@pytest.mark.asyncio
async def test_an_inconclusive_check_is_reported_not_reported_as_clean(monkeypatch):
    """A 429/529/timeout used to be indistinguishable from "no contradiction" (#90).

    Both produced an empty list, `VERIFY complete` was logged, and the pages were committed
    as if the KB had been checked. The failure now reaches the activity log, so an operator
    can tell "verified, nothing found" from "never actually verified".
    """
    entries = _patch_activity_log(monkeypatch)
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])
    page = _page_result()

    await verifier.run_verify_phase(
        session=object(),
        source=_source(),
        page_results=[page],
        chunk_extracts=[],
        full_text="Body",
        llm=_FakeLLM(error=asyncio.TimeoutError()),
        embedding_provider=_FakeEmbedding(),
        tracker=_Tracker(),
    )

    assert len(entries) == 1
    assert "inconclusive" in entries[0]
    # Still advisory: the page itself is untouched and stays committable.
    assert verifier.CONFLICT_MARKER_START not in page.content_md


@pytest.mark.asyncio
async def test_the_failure_accumulator_names_the_stage_that_failed(monkeypatch):
    """"Something went wrong" is not actionable; which of the three steps failed is."""
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])
    failures: list[dict] = []

    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=_FakeLLM(reply="I think they disagree, actually."),
        source=_source(),
        failures=failures,
    )

    assert conflicts == []
    assert [f["stage"] for f in failures] == ["verdict-parse"]
    assert "concept/leave-policy" in failures[0]["subject"]


@pytest.mark.asyncio
async def test_a_failed_embed_is_recorded_against_the_page_that_failed(monkeypatch):
    _patch_neighbours(monkeypatch, [(_kb_page(), THRESHOLD + 0.1)])
    failures: list[dict] = []

    await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result(slug="concept/first", title="First")],
        embedding_provider=_FakeEmbedding(fail_slugs=["First"]),
        llm=_FakeLLM(),
        source=_source(),
        failures=failures,
    )

    assert [(f["stage"], f["subject"]) for f in failures] == [
        ("neighbour-search", "concept/first"),
    ]


@pytest.mark.asyncio
async def test_neighbour_fact_checks_do_not_run_one_at_a_time(monkeypatch):
    """Up to 90 serial round trips at a 30 s timeout each, in a band that never moves.

    The pairs have no data dependency, so the wall-clock cost of VERIFY was pure
    serialisation.
    """
    neighbours = [
        (_kb_page(slug=f"concept/kb-{index}"), THRESHOLD + 0.05) for index in range(3)
    ]
    _patch_neighbours(monkeypatch, neighbours)

    class _SlowLLM(_FakeLLM):
        def __init__(self):
            super().__init__()
            self.active = 0
            self.max_active = 0

        async def generate(self, prompt, system=None, temperature=0.0, **_kwargs):
            self.prompts.append(prompt)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return self.reply

    llm = _SlowLLM()
    conflicts = await verifier.check_conflicts(
        session=object(),
        page_results=[_page_result()],
        embedding_provider=_FakeEmbedding(),
        llm=llm,
        source=_source(),
    )

    assert len(conflicts) == 3
    assert llm.max_active > 1, "fact-check calls are still serialised"


@pytest.mark.asyncio
async def test_verify_phase_skips_conflicts_without_an_embedding_provider(monkeypatch):
    """Embeddings are optional configuration; their absence is not an ingest failure."""
    called = []

    async def _record(*_args, **_kwargs):
        called.append(True)
        return []

    monkeypatch.setattr(verifier, "check_conflicts", _record)
    pages = [_page_result()]

    returned = await verifier.run_verify_phase(
        session=object(),
        source=_source(),
        page_results=pages,
        chunk_extracts=[],
        full_text="Body",
        llm=_FakeLLM(),
        embedding_provider=None,
        tracker=_Tracker(),
    )

    assert returned == pages
    assert called == []
