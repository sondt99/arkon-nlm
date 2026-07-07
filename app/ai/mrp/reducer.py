"""
Phase 2 (REDUCE) of the MRP pipeline.

Steps:
  2.1  Collect all entities/concepts from chunk extracts
  2.2  Exact deduplication by normalized name
  2.3  Embedding-based deduplication (cosine similarity)
  2.4  LLM batch resolution for ambiguous entity pairs
  2.5  KB reconciliation: search existing wiki pages per entity
  2.6  LLM batch confirmation for MAYBE matches
  2.7  Planning call: 1 LLM call → Compilation Plan JSON
  2.8  Persist SourceCompilationPlan to DB
"""

import asyncio
from difflib import SequenceMatcher
import json
import re
import string
import uuid
from typing import TYPE_CHECKING, Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.providers.base import EmbeddingProvider, LLMProvider
from app.config import settings
from app.utils.progress import ProgressTracker

if TYPE_CHECKING:
    from app.database.models import SourceCompilationPlan

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MERGE_THRESHOLD = settings.mrp_entity_merge_threshold
AMBIGUOUS_LOW = settings.mrp_entity_ambiguous_threshold
KB_UPDATE_THRESHOLD = settings.mrp_kb_update_threshold
KB_MAYBE_THRESHOLD = settings.mrp_kb_maybe_threshold

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PUNCT_TABLE = str.maketrans("", "", string.punctuation)


def _normalize(name: str) -> str:
    return name.lower().strip().translate(_PUNCT_TABLE)


def _slug_tail(slug: str) -> str:
    """Return the human-comparable final segment of a wiki slug."""
    return _normalize((slug or "").rsplit("/", 1)[-1].replace("-", " "))


def _candidate_dict(page, similarity: float, method: str = "semantic") -> dict:
    return {
        "slug": page.slug,
        "title": page.title,
        "page_type": page.page_type,
        "summary": (page.summary or "")[:500],
        "similarity": round(float(similarity), 4),
        "match_method": method,
    }


def _lexical_similarity(left: str, right: str) -> float:
    a, b = _normalize(left), _normalize(right)
    if not a or not b:
        return 0.0
    sequence = SequenceMatcher(None, a, b).ratio()
    a_tokens, b_tokens = set(a.split()), set(b.split())
    token_score = len(a_tokens & b_tokens) / max(len(a_tokens | b_tokens), 1)
    return max(sequence, token_score)


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Step 2.1 — Collect entities and concepts from chunk extracts
# ---------------------------------------------------------------------------

def collect_raw_items(chunk_extracts) -> tuple[list[dict], list[dict], list[dict]]:
    """
    Flatten entities, concepts, and claims from all SourceChunkExtract rows.

    Returns (entities, concepts, claims) where each item carries its
    source chunk_index and absolute_offset fields.
    """
    entities: list[dict] = []
    concepts: list[dict] = []
    claims: list[dict] = []

    for row in chunk_extracts:
        extract = row.extract_json or {}
        chunk_idx = row.chunk_index

        for e in extract.get("entities", []):
            entities.append({**e, "_chunk_index": chunk_idx})

        for c in extract.get("concepts", []):
            concepts.append({**c, "_chunk_index": chunk_idx})

        for cl in extract.get("claims", []):
            claims.append({**cl, "_chunk_index": chunk_idx})

    return entities, concepts, claims


# ---------------------------------------------------------------------------
# Step 2.2 — Exact deduplication
# ---------------------------------------------------------------------------

def exact_dedup_entities(raw_entities: list[dict]) -> list[dict]:
    """
    Group entities by (normalized_name, type). Keep most common name as
    canonical. Accumulate all aliases and absolute_offsets.
    """
    groups: dict[tuple, list[dict]] = {}
    for e in raw_entities:
        key = (_normalize(e.get("name", "")), e.get("type", "other"))
        groups.setdefault(key, []).append(e)

    canonical: list[dict] = []
    for (norm_name, etype), group in groups.items():
        # Pick the name that appears most frequently across the group
        name_counts: dict[str, int] = {}
        for e in group:
            n = e.get("name", "")
            name_counts[n] = name_counts.get(n, 0) + 1
        best_name = max(name_counts, key=lambda x: name_counts[x])

        # Merge aliases
        aliases: set[str] = set()
        for e in group:
            aliases.add(e.get("name", ""))
            aliases.update(e.get("aliases", []))
        aliases.discard(best_name)

        # Collect all offsets
        offsets = [e.get("absolute_offset", 0) for e in group if e.get("absolute_offset") is not None]

        canonical.append({
            "name": best_name,
            "type": etype,
            "aliases": sorted(aliases),
            "mention_count": len(group),
            "absolute_offsets": offsets,
            "_norm": norm_name,
        })

    return canonical


def exact_dedup_concepts(raw_concepts: list[dict]) -> list[dict]:
    """Group concepts by normalized term."""
    groups: dict[str, list[dict]] = {}
    for c in raw_concepts:
        key = _normalize(c.get("term", ""))
        groups.setdefault(key, []).append(c)

    canonical: list[dict] = []
    for norm_term, group in groups.items():
        term_counts: dict[str, int] = {}
        for c in group:
            t = c.get("term", "")
            term_counts[t] = term_counts.get(t, 0) + 1
        best_term = max(term_counts, key=lambda x: term_counts[x])

        # Keep longest/best definition_excerpt
        best_def = max(
            (c.get("definition_excerpt", "") for c in group),
            key=len,
            default="",
        )
        offsets = [c.get("absolute_offset", 0) for c in group if c.get("absolute_offset") is not None]

        canonical.append({
            "term": best_term,
            "definition_excerpt": best_def,
            "mention_count": len(group),
            "absolute_offsets": offsets,
            "_norm": norm_term,
        })

    return canonical


# ---------------------------------------------------------------------------
# Step 2.3 — Embedding-based deduplication
# ---------------------------------------------------------------------------

async def embedding_dedup_entities(
    entities: list[dict],
    embedding_provider: EmbeddingProvider,
) -> list[dict]:
    """
    Merge entities whose name embeddings are very similar (> MERGE_THRESHOLD)
    and have the same type. Returns a reduced list of canonical entities.
    """
    if len(entities) <= 1:
        return entities

    names = [e["name"] for e in entities]
    try:
        vectors = await embedding_provider.embed_batch(names)
    except Exception as exc:
        logger.warning(f"MRP REDUCE embedding dedup failed: {exc}. Skipping.")
        return entities

    n = len(entities)
    merged_into: dict[int, int] = {}  # index → canonical index

    def _root(i: int) -> int:
        while i in merged_into:
            i = merged_into[i]
        return i

    auto_merge_pairs: list[tuple[int, int]] = []
    ambiguous_pairs: list[tuple[int, int]] = []

    for i in range(n):
        for j in range(i + 1, n):
            if entities[i]["type"] != entities[j]["type"]:
                continue
            sim = _cosine(vectors[i], vectors[j])
            if sim >= MERGE_THRESHOLD:
                auto_merge_pairs.append((i, j))
            elif sim >= AMBIGUOUS_LOW:
                ambiguous_pairs.append((i, j))

    # Apply auto-merges
    for i, j in auto_merge_pairs:
        ri, rj = _root(i), _root(j)
        if ri != rj:
            # Merge lower-mention into higher-mention
            if entities[ri]["mention_count"] >= entities[rj]["mention_count"]:
                merged_into[rj] = ri
            else:
                merged_into[ri] = rj

    # Collect ambiguous pairs not already merged
    still_ambiguous = [
        (i, j) for i, j in ambiguous_pairs if _root(i) != _root(j)
    ]

    return merged_into, still_ambiguous, vectors, entities


async def resolve_ambiguous_entities(
    llm: LLMProvider,
    entities: list[dict],
    ambiguous_pairs: list[tuple[int, int]],
    merged_into: dict[int, int],
) -> dict[int, int]:
    """
    Send ambiguous entity pairs to LLM for batch disambiguation.
    Returns updated merged_into dict.
    """
    if not ambiguous_pairs:
        return merged_into

    def _root(i):
        while i in merged_into:
            i = merged_into[i]
        return i

    lines = []
    for k, (i, j) in enumerate(ambiguous_pairs):
        lines.append(
            f"{k + 1}. \"{entities[i]['name']}\" ({entities[i]['type']}) vs "
            f"\"{entities[j]['name']}\" ({entities[j]['type']})"
        )

    prompt = (
        "For each pair below, determine if they refer to the same real-world entity.\n"
        "Return a JSON array of exactly " + str(len(ambiguous_pairs)) + " booleans "
        "(true = same entity, false = different).\n"
        "Return ONLY the JSON array.\n\n" + "\n".join(lines)
    )

    try:
        raw = await asyncio.wait_for(
            llm.generate(prompt, system="You are a named-entity resolution assistant. Return only JSON.", temperature=0.0),
            timeout=60,
        )
        cleaned = raw.strip().strip("```json").strip("```").strip()
        decisions: list[bool] = json.loads(cleaned)
        for k, (i, j) in enumerate(ambiguous_pairs):
            if k < len(decisions) and decisions[k]:
                ri, rj = _root(i), _root(j)
                if ri != rj:
                    if entities[ri]["mention_count"] >= entities[rj]["mention_count"]:
                        merged_into[rj] = ri
                    else:
                        merged_into[ri] = rj
    except Exception as exc:
        logger.warning(f"MRP REDUCE ambiguous resolution failed: {exc}. Skipping.")

    return merged_into


def _apply_merges(entities: list[dict], merged_into: dict[int, int]) -> list[dict]:
    """Apply merge map to produce final deduplicated entity list."""
    def _root(i):
        while i in merged_into:
            i = merged_into[i]
        return i

    roots = set(_root(i) for i in range(len(entities)))
    result = []
    for ri in roots:
        canonical = dict(entities[ri])
        # Merge all aliases and mention_counts from merged-in entities
        for i, e in enumerate(entities):
            if i != ri and _root(i) == ri:
                canonical["mention_count"] = canonical.get("mention_count", 0) + e.get("mention_count", 0)
                canonical["aliases"] = list(set(canonical.get("aliases", [])) | set(e.get("aliases", [])) | {e["name"]})
                canonical["absolute_offsets"] = canonical.get("absolute_offsets", []) + e.get("absolute_offsets", [])
        result.append(canonical)
    return result


# ---------------------------------------------------------------------------
# Step 2.5 — KB reconciliation
# ---------------------------------------------------------------------------

async def reconcile_with_kb(
    session: AsyncSession,
    entities: list[dict],
    concepts: list[dict],
    embedding_provider: EmbeddingProvider,
    source,
    llm: Optional[LLMProvider] = None,
) -> dict[str, dict]:
    """
    For each canonical entity/concept, search existing wiki pages.
    Returns {item_name: {"action": "CREATE"|"UPDATE"|"MAYBE", "page_slug": str|None, "similarity": float}}
    """
    from app.services import wiki_service

    scope_type = source.scope_type or "global"
    scope_id = source.scope_id

    all_items = [("entity", e["name"], e) for e in entities] + \
                [("concept", c["term"], c) for c in concepts]

    reconciliation: dict[str, dict] = {}

    if not all_items:
        return reconciliation

    # Exact title/slug-tail matching is deterministic and considerably more
    # reliable than comparing a short concept name with a full-page embedding.
    existing_pages = await wiki_service.list_pages(
        session, limit=10_000, scope_type=scope_type, scope_id=scope_id,
    )
    exact_by_title: dict[str, list] = {}
    exact_by_slug: dict[str, list] = {}
    for page in existing_pages:
        exact_by_title.setdefault(_normalize(page.title or ""), []).append(page)
        exact_by_slug.setdefault(_slug_tail(page.slug), []).append(page)

    unresolved: list[tuple[str, str, dict]] = []
    for item_type, name, item in all_items:
        norm = _normalize(name)
        matches = [
            page for page in (exact_by_title.get(norm, []) or exact_by_slug.get(norm, []))
            if page.page_type != "source"
        ]
        # A document source with the same title is not the same thing as an
        # entity/concept page. Prefer type-compatible knowledge pages.
        compatible = [p for p in matches if p.page_type == item_type]
        target = compatible or matches
        if target:
            page = target[0]
            reconciliation[name] = {
                "action": "UPDATE",
                "page_slug": page.slug,
                "page_title": page.title,
                "page_type": page.page_type,
                "similarity": 1.0,
                "confidence": 1.0,
                "match_method": "exact_title" if _normalize(page.title or "") == norm else "exact_slug",
                "reason": "Exact normalized title/slug match",
                "candidates": [_candidate_dict(page, 1.0, "exact")],
            }
        else:
            unresolved.append((item_type, name, item))

    if not unresolved:
        return reconciliation

    # Batch-embed unresolved query texts, then search DB sequentially.
    # Sequential DB access avoids concurrent AsyncSession errors.
    query_texts = [
        (f"{name}: {item['definition_excerpt'][:200]}" if itype == "concept" and item.get("definition_excerpt") else name)[:4000]
        for itype, name, item in unresolved
    ]
    try:
        vectors = await embedding_provider.embed_batch(query_texts)
    except Exception as exc:
        logger.warning(f"MRP REDUCE kb reconcile embed_batch failed: {exc}. All items → CREATE.")
        for _, name, _ in unresolved:
            reconciliation[name] = {
                "action": "CREATE", "page_slug": None, "similarity": 0.0,
                "confidence": 0.0, "match_method": "embedding_error", "candidates": [],
            }
        return reconciliation

    for ((item_type, name, _), vec) in zip(unresolved, vectors):
        try:
            hits = await wiki_service.search_pages_semantic(
                session, vec, top_k=3, scope_type=scope_type, scope_id=scope_id,
            )
        except Exception as exc:
            logger.debug(f"MRP REDUCE kb reconcile failed for '{name}': {exc}")
            reconciliation[name] = {"action": "CREATE", "page_slug": None, "similarity": 0.0,
                                    "confidence": 0.0, "match_method": "search_error", "candidates": []}
            continue

        if not hits:
            reconciliation[name] = {"action": "CREATE", "page_slug": None, "similarity": 0.0,
                                    "confidence": 0.0, "match_method": "no_candidate", "candidates": []}
            continue

        semantic_scores = {page.slug: float(sim) for page, sim in hits}
        page_lookup = {page.slug: page for page in existing_pages}
        candidate_scores: dict[str, tuple[float, float, float]] = {}
        for page, sim in hits:
            lexical = _lexical_similarity(name, page.title or "")
            candidate_scores[page.slug] = (max(float(sim), lexical * 0.82), float(sim), lexical)
        for page in existing_pages:
            if page.page_type == "source":
                continue
            lexical = _lexical_similarity(name, page.title or "")
            if lexical < 0.45:
                continue
            semantic = semantic_scores.get(page.slug, 0.0)
            candidate_scores[page.slug] = (max(semantic, lexical * 0.82), semantic, lexical)

        ranked = sorted(candidate_scores.items(), key=lambda item: item[1][0], reverse=True)[:5]
        top_slug, (top_score, top_semantic, top_lexical) = ranked[0]
        top_page = page_lookup[top_slug]
        candidates = []
        for slug, (score, semantic, lexical) in ranked:
            candidate = _candidate_dict(page_lookup[slug], score, "hybrid")
            candidate["semantic_similarity"] = round(semantic, 4)
            candidate["lexical_similarity"] = round(lexical, 4)
            candidates.append(candidate)

        if (
            top_score >= KB_UPDATE_THRESHOLD
            and top_semantic >= settings.mrp_kb_min_semantic_similarity
            and top_lexical >= settings.mrp_kb_min_lexical_similarity
        ):
            reconciliation[name] = {
                "action": "UPDATE", "page_slug": top_page.slug, "page_title": top_page.title,
                "page_type": top_page.page_type, "similarity": top_score, "confidence": top_score,
                "match_method": "hybrid_high", "reason": "High semantic and title similarity",
                "candidates": candidates,
            }
        elif top_score >= KB_MAYBE_THRESHOLD:
            reconciliation[name] = {
                "action": "MAYBE", "page_slug": top_page.slug, "page_title": top_page.title,
                "page_type": top_page.page_type, "similarity": top_score, "confidence": top_score,
                "match_method": "hybrid_maybe", "candidates": candidates,
                "item_type": item_type,
            }
        else:
            reconciliation[name] = {
                "action": "CREATE", "page_slug": None, "similarity": top_score,
                "confidence": 1 - max(top_score, 0), "match_method": "hybrid_low",
                "reason": "No sufficiently similar existing page", "candidates": candidates,
            }

    # Batch-resolve MAYBE items with LLM
    maybe_items = [(name, rec) for name, rec in reconciliation.items() if rec["action"] == "MAYBE"]
    if maybe_items:
        await _resolve_maybe_items(reconciliation, maybe_items, llm)

    return reconciliation


async def _resolve_maybe_items(
    reconciliation: dict,
    maybe_items: list[tuple[str, dict]],
    llm: Optional[LLMProvider],
):
    """Ask one bounded LLM call to decide uncertain KB matches."""
    if llm is None:
        for name, _ in maybe_items:
            reconciliation[name].update(
                action="CREATE", page_slug=None, match_method="maybe_unresolved",
                reason="No LLM available to confirm the uncertain match",
            )
        return

    payload = []
    for name, rec in maybe_items[:40]:
        payload.append({
            "name": name,
            "item_type": rec.get("item_type"),
            "candidates": rec.get("candidates", [])[:3],
        })
    prompt = (
        "Decide whether each extracted wiki item is the SAME subject as one existing candidate. "
        "Do not match merely related topics. Return a JSON array with name, decision (UPDATE or CREATE), "
        "target_slug (required for UPDATE), confidence (0..1), and reason. Only choose target_slug from "
        "that item's candidates.\n\n" + json.dumps(payload, ensure_ascii=False)
    )
    decisions: dict[str, dict] = {}
    try:
        raw = await asyncio.wait_for(
            llm.generate(prompt, system="You are a conservative knowledge-base entity resolver. Return JSON only.", temperature=0.0),
            timeout=90,
        )
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            decisions = {str(d.get("name")): d for d in parsed if isinstance(d, dict)}
    except Exception as exc:
        logger.warning(f"MRP REDUCE MAYBE resolution failed: {exc}")

    for name, rec in maybe_items:
        decision = decisions.get(name, {})
        allowed = {c.get("slug") for c in rec.get("candidates", [])}
        target = decision.get("target_slug")
        confidence = float(decision.get("confidence") or 0)
        if decision.get("decision") == "UPDATE" and target in allowed and confidence >= 0.65:
            candidate = next(c for c in rec["candidates"] if c.get("slug") == target)
            rec.update(
                action="UPDATE", page_slug=target, page_title=candidate.get("title"),
                page_type=candidate.get("page_type"), confidence=confidence,
                match_method="semantic_llm", reason=decision.get("reason") or "LLM confirmed same subject",
            )
        else:
            rec.update(
                action="CREATE", page_slug=None, confidence=max(confidence, 0.5),
                match_method="semantic_llm", reason=decision.get("reason") or "Uncertain candidate rejected",
            )


# ---------------------------------------------------------------------------
# Step 2.7 — Planning call
# ---------------------------------------------------------------------------

PLANNING_SYSTEM = """\
You are a wiki compilation planner. Given extracted entities and their relationship
to an existing knowledge base, produce a compilation plan. Return ONLY valid JSON.
"""

PLANNING_PROMPT_TEMPLATE = """\
## Source document
Title: {source_title}
Knowledge type: {kt_context}
Strategy: {strategy}
{domain_note}

## Extracted entities (with mention counts)
{entities_summary}

## Extracted concepts (with mention counts)
{concepts_summary}

## KB reconciliation results
{kb_reconciliation}

Produce a JSON compilation plan:

{{
  "pages": [
    {{
      "action": "CREATE",
      "slug": "concept/example-name",
      "title": "Example Page Title",
      "page_type": "entity | concept | topic | source",
      "entity_names": ["entity or concept name covered by this page"],
      "related_kb_pages": ["existing-slug-1"],
      "priority": 1
    }}
  ],
  "source_page_slug": "source/short-doc-slug",
  "estimated_page_count": 5,
  "compilation_notes": "any important notes for the compiler"
}}

Rules:
- action must be "CREATE" or "UPDATE"
- For UPDATE, slug must be an existing wiki page slug (from KB reconciliation above)
- For CREATE, slug must be new (type-prefixed, lowercase, hyphenated)
- Always include exactly one page with page_type "source" for the document itself
- Group closely related small entities onto the same page (max 3-4 per page)
- priority 1 = highest importance (process first)
- entity_names must match the names in the entities/concepts lists above
- Target approximately {target_page_count} total pages (feel free to create more if the document is dense and contains many distinct concepts).
- Domain-critical commands, payloads, procedures, platform variants, CVEs and TTPs
  must not be grouped into a generic page when their exact distinction matters.
- Return ONLY the JSON object
"""


async def run_planning_call(
    llm: LLMProvider,
    source,
    strategy: str,
    canonical_entities: list[dict],
    canonical_concepts: list[dict],
    reconciliation: dict[str, dict],
    kt_name: Optional[str],
    kt_desc: Optional[str],
    kt_extraction_hints: Optional[str] = None,
) -> dict:
    """Single LLM call to produce the Compilation Plan JSON."""
    n_chars = len(source.full_text or "")
    # Calculate target based on the actual number of extracted concepts rather than just document length
    total_extracted_items = len(canonical_entities) + len(canonical_concepts)
    
    if strategy == "single_pass":
        # Usually 1 page per 2-3 items, minimum 3, maximum 15
        target_pages = max(3, min(15, total_extracted_items // 2))
    elif strategy == "standard":
        target_pages = max(8, min(30, total_extracted_items // 3))
    else:
        target_pages = max(15, min(60, total_extracted_items // 3))

    kt_context = kt_name or "(no specific knowledge type)"
    if kt_desc:
        kt_context += f" — {kt_desc}"

    def _fmt_entity(e: dict) -> str:
        aliases = ", ".join(e["aliases"][:3]) if e.get("aliases") else ""
        kb = reconciliation.get(e["name"], {})
        kb_info = f"→ {kb['action']} {kb.get('page_slug', '')}" if kb else "→ CREATE"
        return (
            f"  - {e['name']} ({e['type']}, {e['mention_count']} mentions"
            + (f", aliases: {aliases}" if aliases else "")
            + f") {kb_info}"
        )

    def _fmt_concept(c: dict) -> str:
        kb = reconciliation.get(c["term"], {})
        kb_info = f"→ {kb['action']} {kb.get('page_slug', '')}" if kb else "→ CREATE"
        return f"  - {c['term']} ({c['mention_count']} mentions) {kb_info}"

    # Security items are often valuable precisely because they are rare. Put
    # technique/tool/CVE/payload records first instead of ranking only by count.
    security_types = {"technique", "tool", "cve", "payload"}
    sorted_entities = sorted(
        canonical_entities,
        key=lambda x: (
            0 if str(x.get("type") or "").lower() in security_types else 1,
            -int(x.get("mention_count", 0)),
        ),
    )
    sorted_concepts = sorted(canonical_concepts, key=lambda x: x.get("mention_count", 0), reverse=True)

    is_security = bool(kt_extraction_hints and kt_extraction_hints.strip())
    planner_limit = 100 if is_security else 30
    entities_summary = "\n".join(_fmt_entity(e) for e in sorted_entities[:planner_limit]) or "  (none)"
    concepts_summary = "\n".join(_fmt_concept(c) for c in sorted_concepts[:planner_limit]) or "  (none)"

    kb_lines = []
    for name, rec in reconciliation.items():
        if rec["action"] == "UPDATE":
            kb_lines.append(f"  - UPDATE: {name} → {rec['page_slug']} (sim={rec['similarity']:.2f})")
    kb_reconciliation = "\n".join(kb_lines) if kb_lines else "  (all items are new)"

    prompt = PLANNING_PROMPT_TEMPLATE.format(
        source_title=source.title or source.file_name or str(source.id),
        kt_context=kt_context,
        strategy=strategy,
        domain_note=(
            f"\n## Domain-specific preservation policy\n{kt_extraction_hints.strip()}\n"
            if kt_extraction_hints and kt_extraction_hints.strip() else ""
        ),
        entities_summary=entities_summary,
        concepts_summary=concepts_summary,
        kb_reconciliation=kb_reconciliation,
        target_page_count=target_pages,
    )

    raw = await asyncio.wait_for(
        llm.generate(prompt, system=PLANNING_SYSTEM, temperature=0.1),
        timeout=120,
    )

    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        plan = json.loads(cleaned)
    except json.JSONDecodeError:
        last_brace = cleaned.rfind("}")
        if last_brace != -1:
            plan = json.loads(cleaned[: last_brace + 1])
        else:
            raise

    return plan


def enforce_reconciliation(plan: dict, reconciliation: dict[str, dict]) -> dict:
    """Make deterministic KB matches authoritative over planner creativity.

    A planner may group several extracted names onto one new page. If some of
    those names map to different existing pages, split them into individual
    UPDATE operations and keep the unmatched names on the original CREATE.
    """
    normalized = {_normalize(name): (name, rec) for name, rec in reconciliation.items()}
    output: list[dict] = []
    covered_updates: set[str] = set()

    for original in plan.get("pages", []):
        page = dict(original)
        names = [str(n) for n in page.get("entity_names", []) if str(n).strip()]
        update_groups: dict[str, list[tuple[str, dict]]] = {}
        unmatched: list[str] = []
        for name in names:
            found = normalized.get(_normalize(name))
            if found and found[1].get("action") == "UPDATE" and found[1].get("page_slug"):
                update_groups.setdefault(found[1]["page_slug"], []).append((name, found[1]))
            else:
                unmatched.append(name)

        if update_groups:
            for slug, group in update_groups.items():
                rec = group[0][1]
                covered_updates.add(slug)
                output.append({
                    **page,
                    "action": "UPDATE",
                    "slug": slug,
                    "title": rec.get("page_title") or page.get("title") or group[0][0],
                    "page_type": rec.get("page_type") or page.get("page_type", "concept"),
                    "entity_names": [name for name, _ in group],
                    "match_confidence": rec.get("confidence"),
                    "match_method": rec.get("match_method"),
                    "match_reason": rec.get("reason"),
                    "candidates": rec.get("candidates", []),
                })
            if unmatched:
                first = normalized.get(_normalize(unmatched[0]))
                rec = first[1] if first else {}
                output.append({
                    **page, "action": "CREATE", "entity_names": unmatched,
                    "match_confidence": rec.get("confidence"),
                    "match_method": rec.get("match_method"),
                    "match_reason": rec.get("reason"),
                    "candidates": rec.get("candidates", []),
                })
        else:
            # UPDATE is only valid when reconciliation produced that exact slug.
            # Source pages normally have no entity_names and remain CREATE.
            if str(page.get("action", "CREATE")).upper() == "UPDATE":
                valid = any(
                    rec.get("action") == "UPDATE" and rec.get("page_slug") == page.get("slug")
                    for rec in reconciliation.values()
                )
                if not valid:
                    page["action"] = "CREATE"
            first = normalized.get(_normalize(names[0])) if names else None
            rec = first[1] if first else {}
            output.append({
                **page,
                "match_confidence": page.get("match_confidence", rec.get("confidence")),
                "match_method": page.get("match_method", rec.get("match_method")),
                "match_reason": page.get("match_reason", rec.get("reason")),
                "candidates": page.get("candidates", rec.get("candidates", [])),
            })

    # Multiple extracted names may resolve to the same existing page. Keep one
    # writer operation per target so concurrent writes cannot overwrite each
    # other during the same plan.
    consolidated: list[dict] = []
    update_index: dict[str, int] = {}
    for page in output:
        if page.get("action") == "UPDATE" and page.get("slug"):
            slug = str(page["slug"])
            if slug in update_index:
                current = consolidated[update_index[slug]]
                current["entity_names"] = list(dict.fromkeys([
                    *(current.get("entity_names") or []), *(page.get("entity_names") or []),
                ]))
                current["priority"] = min(int(current.get("priority") or 99), int(page.get("priority") or 99))
                continue
            update_index[slug] = len(consolidated)
        consolidated.append(page)
    output = consolidated

    # Do not let the planner silently omit a confirmed update.
    next_priority = max((int(p.get("priority") or 0) for p in output), default=0) + 1
    for name, rec in reconciliation.items():
        slug = rec.get("page_slug")
        if rec.get("action") != "UPDATE" or not slug:
            continue
        if slug in covered_updates:
            target = next((item for item in output if item.get("action") == "UPDATE" and item.get("slug") == slug), None)
            if target is not None:
                target["entity_names"] = list(dict.fromkeys([*(target.get("entity_names") or []), name]))
            continue
        output.append({
            "action": "UPDATE",
            "slug": slug,
            "title": rec.get("page_title") or name,
            "page_type": rec.get("page_type") or "concept",
            "entity_names": [name],
            "related_kb_pages": [],
            "priority": next_priority,
            "match_confidence": rec.get("confidence"),
            "match_method": rec.get("match_method"),
            "match_reason": rec.get("reason"),
            "candidates": rec.get("candidates", []),
        })
        next_priority += 1

    plan["pages"] = output
    plan["estimated_page_count"] = len(output)
    return plan


# ---------------------------------------------------------------------------
# Phase 2 orchestrator
# ---------------------------------------------------------------------------

async def run_reduce_phase(
    session: AsyncSession,
    source,
    chunk_extracts: list,
    llm: LLMProvider,
    embedding_provider: EmbeddingProvider,
    query_embedding_provider: Optional[EmbeddingProvider],
    kt_name: Optional[str],
    kt_desc: Optional[str],
    tracker: ProgressTracker,
    kt_extraction_hints: Optional[str] = None,
) -> "SourceCompilationPlan":
    """
    Run full Phase 2 (REDUCE).

    Returns a SourceCompilationPlan ORM object with status='pending_review'.
    Uses INSERT ... ON CONFLICT DO UPDATE so re-runs safely overwrite old plans.
    """
    from app.database.models import Source, SourceCompilationPlan

    await tracker.update(66, "Collecting extractions...")

    # 2.1 Collect raw items
    raw_entities, raw_concepts, raw_claims = collect_raw_items(chunk_extracts)
    logger.info(f"MRP REDUCE: {len(raw_entities)} raw entities, {len(raw_concepts)} concepts, {len(raw_claims)} claims")

    # 2.2 Exact dedup
    canonical_entities = exact_dedup_entities(raw_entities)
    canonical_concepts = exact_dedup_concepts(raw_concepts)
    logger.info(f"MRP REDUCE after exact-dedup: {len(canonical_entities)} entities, {len(canonical_concepts)} concepts")

    await tracker.update(68, "Deduplicating entities...")

    # 2.3 Embedding dedup for entities
    if len(canonical_entities) > 1 and embedding_provider is not None:
        try:
            result = await embedding_dedup_entities(canonical_entities, embedding_provider)
            if isinstance(result, tuple):
                merged_into, ambiguous_pairs, vectors, canonical_entities = result
                # 2.4 LLM resolution for ambiguous pairs
                merged_into = await resolve_ambiguous_entities(llm, canonical_entities, ambiguous_pairs, merged_into)
                canonical_entities = _apply_merges(canonical_entities, merged_into)
                logger.info(f"MRP REDUCE after embedding-dedup: {len(canonical_entities)} entities")
        except Exception as exc:
            logger.warning(f"MRP REDUCE embedding dedup error: {exc}. Continuing with exact-dedup result.")

    await tracker.update(72, "Reconciling with knowledge base...")

    # 2.5 KB reconciliation
    reconciliation: dict[str, dict] = {}
    reconcile_embedding = query_embedding_provider or embedding_provider
    if reconcile_embedding is not None:
        try:
            reconciliation = await reconcile_with_kb(
                session, canonical_entities, canonical_concepts, reconcile_embedding, source, llm,
            )
        except Exception as exc:
            logger.warning(f"MRP REDUCE KB reconciliation failed: {exc}. All items will be CREATE.")

    action_counts = {
        action: sum(1 for rec in reconciliation.values() if rec.get("action") == action)
        for action in ("CREATE", "UPDATE", "MAYBE")
    }
    method_counts: dict[str, int] = {}
    for rec in reconciliation.values():
        method = str(rec.get("match_method") or "unknown")
        method_counts[method] = method_counts.get(method, 0) + 1
    logger.info(f"MRP REDUCE reconciliation actions={action_counts} methods={method_counts}")

    await tracker.update(76, "Generating compilation plan...")

    # 2.7 Planning call
    strategy = source.pipeline_strategy or "standard"
    plan_dict = await run_planning_call(
        llm=llm,
        source=source,
        strategy=strategy,
        canonical_entities=canonical_entities,
        canonical_concepts=canonical_concepts,
        reconciliation=reconciliation,
        kt_name=kt_name,
        kt_desc=kt_desc,
        kt_extraction_hints=kt_extraction_hints,
    )

    from app.ai.mrp.security_artifacts import extract_security_artifacts, is_security_domain
    if is_security_domain(None, kt_extraction_hints):
        plan_dict["_security_artifacts"] = extract_security_artifacts(source.full_text or "")
    else:
        plan_dict["_security_artifacts"] = []

    plan_dict = enforce_reconciliation(plan_dict, reconciliation)
    plan_dict["reconciliation"] = reconciliation
    plan_dict["reconciliation_summary"] = {
        **action_counts,
        "match_methods": method_counts,
    }

    # Attach claim evidence to plan (so REFINE can access claims per entity)
    plan_dict["_claims"] = raw_claims
    plan_dict["_entities"] = canonical_entities
    plan_dict["_concepts"] = canonical_concepts

    # 2.8 Persist plan (upsert: safe to re-run)
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    existing = (await session.execute(
        select(SourceCompilationPlan).where(SourceCompilationPlan.source_id == source.id)
    )).scalar_one_or_none()

    if existing:
        existing.plan_json = plan_dict
        existing.status = "pending_review"
        existing.reviewed_by = None
        existing.review_note = None
        existing.reviewed_at = None
        plan_row = existing
    else:
        plan_row = SourceCompilationPlan(
            source_id=source.id,
            plan_json=plan_dict,
            status="pending_review",
        )
        session.add(plan_row)

    src = await session.get(Source, source.id)
    if src:
        src.pipeline_phase = "plan_review"

    await session.commit()
    logger.info(f"MRP REDUCE complete: plan with {len(plan_dict.get('pages', []))} pages for source={source.id}")

    return plan_row
