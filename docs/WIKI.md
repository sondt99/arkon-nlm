# Wiki and the MRP compiler

The wiki is the main knowledge surface. Arkon does not stop at “chunk this PDF and retrieve snippets.” It **writes pages**: one page per entity, concept, topic, or source, with links and citations.

---

## What a page is

| Field | Meaning |
|---|---|
| `slug` | Stable URL id, e.g. `concept/fire-evacuation` |
| `title` | Display name |
| `page_type` | `entity` · `concept` · `topic` · `source` |
| `content_md` | Full markdown, with `[[wikilinks]]` and `[^n]` citations |
| `summary` | One line, used in search and the index |
| `knowledge_type_slugs` | Categories this page belongs to |
| `scope_type` / `scope_id` | `global` or a workspace |

Pages have revisions. You can roll back. Contributors who cannot direct-edit open a **draft**; an editor approves or rejects it.

The portal is a three-panel browser: tree, content, backlinks. There is also a force-directed graph (`/wiki/graph`).

---

## The pipeline (MRP)

When a source is uploaded, the worker runs:

```text
0  Triage     pick a strategy from document length
1  MAP        chunk + parallel LLM extract
2  REDUCE     merge entities, reconcile with the existing wiki, write a plan
2.5 Review    a human approves / edits / rejects the plan
3  REFINE     write or update each planned page
4  VERIFY     citations, coverage, conflicts (non-blocking)
5  COMMIT     one transaction: pages + embeddings + index
```

`source.pipeline_phase` records the last finished phase so a crash can resume.

### 0 — Triage

| Strategy | Size | What happens |
|---|---|---|
| `single_pass` | short | One extraction call |
| `standard` | medium | Heading-aligned chunks (~12k chars, 1k overlap, configurable) |
| `hierarchical` | very long | Same chunking, extra stitching |

### 1 — MAP

Each chunk returns entities, concepts, claims (with byte offsets), relations, and topics. Results are saved to `source_chunk_extracts` immediately so MAP can resume.

### 2 — REDUCE

1. Exact name dedup
2. Embedding dedup (auto-merge above the merge threshold; LLM disambiguates the grey band)
3. Reconcile against existing wiki pages (update vs create)
4. One planning call → a **compilation plan** (`source_compilation_plans`, `pending_review`)

Thresholds live in env (`MRP_*`). See `.env.docker.example`.

### 2.5 — Plan review

**Documents →** source row **Review Plan**, or:

```http
GET  /api/sources/{id}/plan
POST /api/sources/{id}/plan/approve
POST /api/sources/{id}/plan/reject
```

Set `MRP_AUTO_APPROVE_PLAN=true` to skip this on a trusted pipeline.

### 3 — REFINE

One writer per planned page, in parallel. Small pages are a single LLM call. Larger ones use a short tool loop (`read_kb_page`, `read_source_excerpt`, `finish`). Claims get `[^n]` footnotes.

Writers retry transient provider errors. They never commit placeholder “I could not write this” text.

### 4 — VERIFY

| Check | What it does |
|---|---|
| Citations | Each `[^n]` vs its excerpt: supported / partial / unverified / contradicted |
| Coverage | Frequently extracted entities with no page |
| Conflicts | Semantically close existing pages checked for contradictions |

Informational. They do not block COMMIT.

### 5 — COMMIT

Atomic. Any page failure rolls the whole commit back. The source becomes `ready` only after `MRP COMMIT complete`.

---

## Security knowledge types

For pentest / red-team / exploit / vulnerability types, extraction hints ride through every phase. Commands, payloads, CVEs, and ATT&CK IDs are also lifted deterministically (offset + hash) and stored under **Exact commands and payloads**. That block is evidence from the file, not generated syntax.

Scratchpad openings (“Let me search…”) are rejected.

---

## Resume

| `pipeline_phase` | On retry |
|---|---|
| `map` | Skip chunks already extracted |
| `reduce` | Re-run REDUCE |
| `plan_review` | Return the existing plan |
| `refine` / `verify` / `commit` | Re-run REFINE from the plan |

---

## Editing after compile

| Who | How |
|---|---|
| Contributor (`wiki:write:own_dept` or workspace Contributor) | Propose a draft |
| Editor / Knowledge Admin / workspace Editor | Direct `PUT` or approve a draft |
| Anyone with `wiki:read` | Read history |
| System admin | Rollback a revision; delete a page (`wiki:delete` + admin) |

MCP mirrors this: `propose_wiki_edit` vs `edit_wiki_page` / `approve_draft`. See [MCP.md](MCP.md).

---

## Search

Two indexes:

- Full-text (Postgres) for keywords
- Vector (pgvector, dimension-specific table) for semantic search

Changing the embedding model/dimension starts a re-embed job from **Settings**. Until it finishes, search quality drops.

---

## Provenance

Wiki pages record which sources contributed. From a source you can list **wiki pages it produced**. From a page you can follow citations back to excerpts. Prefer the wiki for answers; open the source when you need the original wording.
