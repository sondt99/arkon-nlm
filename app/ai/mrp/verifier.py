"""
Phase 4 (VERIFY) of the MRP pipeline.

Two checks:
  4.1  Coverage check — entities with many mentions not covered by any page
  4.2  Conflict check — new page content may contradict existing KB pages

All checks are non-blocking for the pipeline: issues are flagged in the worker log, in the
wiki activity log, and in the page content (a marker block appended to the affected page),
but never cause the pipeline to fail.

A check that could not complete is reported as such. It is not reported as "no conflict":
the two outcomes have opposite meanings for whoever reads the page next.
"""

import asyncio
import re
from typing import Optional

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.mrp.writer import PageWriteResult
from app.ai.providers.base import (
    UNTRUSTED_TAGS,
    EmbeddingProvider,
    LLMProvider,
    flatten_untrusted_metadata,
    new_envelope_nonce,
    parse_json_response,
)
from app.config import settings
from app.utils.progress import ProgressTracker

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFLICT_SIM_THRESHOLD = settings.mrp_verify_conflict_threshold
CONFLICT_CHECK_TIMEOUT = 30
# The fact-check calls have no data dependency on one another. Run serially, each one owned
# the full timeout in turn: 30 pages x top_k=3 neighbours is up to 90 round trips at 30 s,
# inside a progress band that never moves. Bounded rather than unbounded because the same
# account-level rate limit that REFINE just finished hammering still applies.
CONFLICT_CHECK_CONCURRENCY = 5

# Anchors for the flag block written into a page body. Stable and machine-findable so a
# re-ingest replaces the previous block instead of stacking a second one under it.
CONFLICT_MARKER_START = "<!-- arkon:conflict-flags:start -->"
CONFLICT_MARKER_END = "<!-- arkon:conflict-flags:end -->"
_CONFLICT_MARKER_BLOCK = re.compile(
    re.escape(CONFLICT_MARKER_START) + r".*?" + re.escape(CONFLICT_MARKER_END) + r"\n*",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# 4.1 Coverage check
# ---------------------------------------------------------------------------

def check_coverage(
    chunk_extracts: list,
    page_results: list[PageWriteResult],
    min_mentions: int = settings.mrp_verify_min_mentions,
) -> list[str]:
    """
    Returns entity names mentioned >= min_mentions times in extracts
    but not covered by any page result. Logged as warnings (non-blocking).
    """
    # Count mentions per entity
    mention_counts: dict[str, int] = {}
    for row in chunk_extracts:
        for e in (row.extract_json or {}).get("entities", []):
            name = e.get("name", "").lower()
            if name:
                mention_counts[name] = mention_counts.get(name, 0) + 1

    # Collect all entity names covered by page results
    covered: set[str] = set()
    for pr in page_results:
        covered.update(n.lower() for n in pr.entity_names)
        covered.add(pr.title.lower())

    uncovered = [
        name for name, count in mention_counts.items()
        if count >= min_mentions and name not in covered
    ]

    if uncovered:
        logger.warning(
            f"MRP VERIFY coverage: {len(uncovered)} significant entities not covered: "
            + ", ".join(uncovered[:10])
        )

    return uncovered


# ---------------------------------------------------------------------------
# 4.2 Conflict check
# ---------------------------------------------------------------------------

async def check_conflicts(
    session: AsyncSession,
    page_results: list[PageWriteResult],
    embedding_provider: EmbeddingProvider,
    llm: LLMProvider,
    source,
    failures: Optional[list[dict]] = None,
) -> list[dict]:
    """
    For each new/updated page, find KB neighbors with high similarity and
    check for factual contradictions via LLM. Returns list of conflict dicts.

    Non-blocking: nothing here raises, because Phase 4 runs after minutes of REFINE work
    that an exception would discard. Every swallowed failure is logged with its exception
    and, when `failures` is supplied, appended to it — a 429, a 529, a timeout and a
    malformed verdict used to be indistinguishable from a clean "no contradiction", which
    made the whole check silently unfalsifiable.
    """
    from app.services import wiki_service

    scope_type = source.scope_type or "global"
    scope_id = source.scope_id

    def _record_failure(stage: str, subject: str, exc: BaseException) -> None:
        detail = f"{type(exc).__name__}: {exc}"
        logger.warning(
            f"MRP VERIFY conflict check inconclusive ({stage}) for '{subject}': {detail}"
        )
        if failures is not None:
            failures.append({"stage": stage, "subject": subject, "error": detail})

    # Neighbour lookup stays serial: it goes through the AsyncSession, which is not safe for
    # concurrent use. Only the LLM calls below are parallelised.
    pairs: list[tuple[PageWriteResult, object, float]] = []
    for pr in page_results:
        try:
            vec = await embedding_provider.embed(
                f"{pr.title}\n\n{pr.summary}\n\n{pr.content_md[:3000]}"
            )
            hits = await wiki_service.search_pages_semantic(
                session, vec, top_k=3, scope_type=scope_type, scope_id=scope_id,
            )
        except Exception as exc:
            _record_failure("neighbour-search", pr.slug, exc)
            continue

        pairs.extend(
            (pr, page, sim) for page, sim in hits
            if sim >= CONFLICT_SIM_THRESHOLD and page.slug != pr.slug
        )

    if not pairs:
        return []

    semaphore = asyncio.Semaphore(CONFLICT_CHECK_CONCURRENCY)

    async def _fact_check(pr: PageWriteResult, kb_page, sim: float) -> Optional[dict]:
        subject = f"{pr.slug} ↔ {kb_page.slug}"
        prompt = (
            f"Do the following two texts contain contradictory factual statements?\n\n"
            f"Text A (new):\n{pr.content_md[:1500]}\n\n"
            f"Text B (existing wiki page '{kb_page.slug}'):\n{(kb_page.content_md or '')[:1500]}\n\n"
            f"Return JSON: {{\"contradicts\": true|false, \"description\": \"string\"}}"
        )
        async with semaphore:
            try:
                raw = await asyncio.wait_for(
                    llm.generate(
                        prompt,
                        system="You are a fact-checking assistant. Return only JSON.",
                        temperature=0.0,
                    ),
                    timeout=CONFLICT_CHECK_TIMEOUT,
                )
            except Exception as exc:
                _record_failure("fact-check", subject, exc)
                return None

        try:
            verdict = parse_json_response(raw)
        except ValueError as exc:
            _record_failure("verdict-parse", subject, exc)
            return None

        if not isinstance(verdict, dict):
            _record_failure(
                "verdict-parse", subject,
                TypeError(f"expected a JSON object, got {type(verdict).__name__}"),
            )
            return None
        if not verdict.get("contradicts"):
            return None

        desc = str(verdict.get("description") or "")
        logger.warning(
            f"MRP VERIFY conflict: '{pr.slug}' ↔ '{kb_page.slug}' (sim={sim:.2f}): {desc[:150]}"
        )
        return {
            "new_slug": pr.slug,
            "existing_slug": kb_page.slug,
            "similarity": sim,
            "description": desc,
        }

    checked = await asyncio.gather(*(_fact_check(*pair) for pair in pairs))
    return [conflict for conflict in checked if conflict is not None]


# ---------------------------------------------------------------------------
# 4.2b Surfacing — page markers and the activity log
# ---------------------------------------------------------------------------

def strip_conflict_marker(content_md: str) -> str:
    """Remove a flag block written by a previous run.

    Re-ingesting a source re-runs the whole check, so the previous verdict is superseded.
    Stripping first also means a page that used to be flagged and no longer is loses the
    warning instead of carrying it forever.
    """
    return _CONFLICT_MARKER_BLOCK.sub("", content_md or "")


def _flag_text(value: str, limit: int) -> str:
    """Reduce a model-written description to one line that is safe inside a page body.

    `description` is LLM output about an uploaded document — untrusted twice over. Flattening
    the whitespace keeps it inside the blockquote it is rendered in rather than letting it
    open headings of its own, and dropping HTML comment delimiters stops it from closing the
    block's own end marker and escaping.
    """
    flat = flatten_untrusted_metadata(value, UNTRUSTED_TAGS, new_envelope_nonce(), limit=limit)
    return flat.replace("<!--", "").replace("-->", "")


def render_conflict_marker(conflicts: list[dict]) -> str:
    """Render the in-content flag block for one page's conflicts."""
    lines = [
        CONFLICT_MARKER_START,
        "> [!WARNING] Possible contradiction with existing wiki pages",
        "> Detected automatically while ingesting a source. Neither side has been"
        " corrected — a human needs to decide which is right.",
        ">",
    ]
    for conflict in conflicts:
        slug = _flag_text(str(conflict.get("existing_slug") or "?"), 200)
        description = _flag_text(str(conflict.get("description") or ""), 300)
        similarity = conflict.get("similarity")
        sim_text = (
            f" (similarity {float(similarity):.2f})"
            if isinstance(similarity, (int, float)) else ""
        )
        lines.append(f"> - [[{slug}]]{sim_text}: {description or '(no detail given)'}")
    lines.append(CONFLICT_MARKER_END)
    return "\n".join(lines)


def annotate_conflicts(
    page_results: list[PageWriteResult], conflicts: list[dict],
) -> int:
    """Write each page's conflicts into its own body. Returns the number of pages flagged.

    The page body is the one channel that reaches every consumer without a schema change:
    COMMIT persists `content_md` verbatim, so the flag lands in the API response, the
    reviewer UI, the page's own revision history and full-text search at once.
    """
    by_slug: dict[str, list[dict]] = {}
    for conflict in conflicts:
        by_slug.setdefault(str(conflict.get("new_slug") or ""), []).append(conflict)

    flagged = 0
    for pr in page_results:
        body = strip_conflict_marker(pr.content_md)
        page_conflicts = by_slug.get(pr.slug)
        if page_conflicts:
            pr.content_md = f"{body.rstrip()}\n\n{render_conflict_marker(page_conflicts)}\n"
            flagged += 1
        else:
            pr.content_md = body
    return flagged


async def _log_verify_outcome(
    session: AsyncSession,
    source,
    conflicts: list[dict],
    failures: list[dict],
) -> None:
    """Record the outcome in the wiki activity log.

    The page marker is where a reader meets the contradiction; the log is where someone
    auditing an ingest looks, and it is also the only place an *inconclusive* check can be
    reported without stamping "we could not verify this" onto the page itself.

    Advisory like the rest of the phase: a failed log write must not cost the pages. The
    entry is flushed here and committed by the caller, so it disappears with the rest of the
    transaction if COMMIT later rolls back.
    """
    from app.services import wiki_service

    label = (
        getattr(source, "title", None)
        or getattr(source, "file_name", None)
        or str(getattr(source, "id", "unknown"))
    )
    parts = []
    if conflicts:
        pairs = "; ".join(
            f"'{c.get('new_slug')}' ↔ '{c.get('existing_slug')}'" for c in conflicts[:10]
        )
        parts.append(f"{len(conflicts)} possible contradiction(s): {pairs}")
    if failures:
        parts.append(
            f"{len(failures)} check(s) inconclusive — those pages were not verified"
        )
    if not parts:
        return

    try:
        await wiki_service.append_log(
            session,
            f"MRP VERIFY on '{label}': " + "; ".join(parts),
            scope_type=source.scope_type or "global",
            scope_id=source.scope_id,
        )
    except Exception as exc:
        logger.warning(f"MRP VERIFY could not write the activity-log entry: {exc}")


# ---------------------------------------------------------------------------
# Phase 4 orchestrator
# ---------------------------------------------------------------------------

async def run_verify_phase(
    session: AsyncSession,
    source,
    page_results: list[PageWriteResult],
    chunk_extracts: list,
    full_text: str,
    llm: LLMProvider,
    embedding_provider: Optional[EmbeddingProvider],
    tracker: ProgressTracker,
) -> list[PageWriteResult]:
    """
    Run Phase 4 (VERIFY). Returns the same page results, with any detected contradiction
    written into the affected page's `content_md` as a flag block.

    Coverage and conflict checks are advisory and never fail the phase.
    """
    await tracker.update(88, "Checking coverage...")

    # 4.1 Coverage check (code only, non-blocking)
    check_coverage(chunk_extracts, page_results)

    await tracker.update(91, "Checking for conflicts...")

    # 4.2 Conflict check (non-blocking)
    conflicts: list[dict] = []
    failures: list[dict] = []
    if embedding_provider is not None:
        try:
            conflicts = await check_conflicts(
                session, page_results, embedding_provider, llm, source,
                failures=failures,
            )
        except Exception as exc:
            logger.warning(f"MRP VERIFY conflict check failed: {exc}")

    flagged = annotate_conflicts(page_results, conflicts)
    await _log_verify_outcome(session, source, conflicts, failures)

    logger.info(
        f"MRP VERIFY complete: {len(page_results)} pages verified for source={source.id}; "
        f"{len(conflicts)} conflict(s) flagged on {flagged} page(s); "
        f"{len(failures)} check(s) inconclusive"
    )
    return page_results
