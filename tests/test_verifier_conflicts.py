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
reported".
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


@pytest.mark.asyncio
async def test_detected_conflicts_go_nowhere_but_the_log(monkeypatch):
    """Characterisation, not endorsement — the conflict list is computed and dropped.

    This module's docstring says issues are flagged "in logs and in the page content
    (markers)". Only the first half happens: run_verify_phase calls check_conflicts
    without binding the result, nothing else in app/ references it, and page_results comes
    back byte-identical. A detected contradiction is therefore invisible to the API, the
    UI and the wiki activity log — it exists only as a warning line in the worker's
    stdout. Locked in here so the gap is visible in the suite; if markers or a persisted
    record are ever added, this test is the one to update.
    """
    async def _found_a_conflict(*_args, **_kwargs):
        return [{
            "new_slug": "concept/leave-policy",
            "existing_slug": "concept/annual-leave",
            "similarity": 0.95,
            "description": "12 days vs 15 days",
        }]

    monkeypatch.setattr(verifier, "check_conflicts", _found_a_conflict)
    page = _page_result()
    original = page.content_md

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
    assert returned[0].content_md == original
    assert "conflict" not in original.lower()


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
