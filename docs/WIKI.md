# Wiki System

The Arkon wiki is the primary knowledge surface. Instead of storing raw document chunks, Arkon compiles documents into structured, interlinked wiki pages — written by an LLM agent, enriched by every new document you add.

---

## How compilation works

When you upload a document, the background worker runs the **MRP pipeline** — a five-phase deterministic process that guarantees every section of the document is read and every claim is traceable back to its source.

```
Phase 0: Triage   → classify document size/strategy
Phase 1: MAP      → chunk document + parallel LLM extraction per chunk
Phase 2: REDUCE   → entity dedup + KB reconciliation → Compilation Plan
Phase 2.5: Review → human approves / modifies / rejects the plan
Phase 3: REFINE   → parallel page writers (one per planned page)
Phase 4: VERIFY   → citation check + coverage check + conflict check
Phase 5: COMMIT   → write pages to DB + embed + regenerate index
```

### Phase 0 — Triage

Classifies the document to choose the right processing strategy based on length:

| Strategy | Document size | Description |
|---|---|---|
| `single_pass` | < 30K chars | Entire document fits in one extraction call |
| `standard` | 30K–200K chars | Split into ~20K-char chunks along section headings |
| `hierarchical` | > 200K chars | Same as standard but with additional context stitching |

### Phase 1 — MAP

The document is split into chunks aligned to section headings (from `outline_json`). Each chunk is ~20K characters with a 1K-char overlap prefix from the previous section. Chunks are processed in parallel (up to 6 at once).

Each chunk extraction call returns structured JSON:

```json
{
  "entities": [{ "name": "...", "type": "person|org|product|...", "local_offset": 0 }],
  "concepts": [{ "term": "...", "definition_excerpt": "...", "local_offset": 0 }],
  "claims":   [{ "statement": "...", "subject": "...", "local_offset": 0, "evidence_length": 200 }],
  "relations": [{ "from": "...", "to": "...", "type": "..." }],
  "topics":   ["..."]
}
```

`local_offset` values are converted to `absolute_offset` (byte position in the original document) so every claim can be traced back to its exact source excerpt. Each chunk's result is saved to `source_chunk_extracts` immediately — if the worker crashes, MAP resumes from where it left off.

### Phase 2 — REDUCE

All chunk extracts are merged into a unified knowledge graph:

1. **Exact dedup** — normalize entity names (lowercase + strip punctuation), group duplicates
2. **Embedding dedup** — cosine similarity between entity name vectors; auto-merge above 0.90, LLM disambiguates 0.75–0.90
3. **KB reconciliation** — semantic search against existing wiki pages per entity:
   - sim ≥ 0.85 → `UPDATE` candidate (entity has an existing page)
   - sim 0.60–0.85 → LLM confirms whether to merge or create new
   - sim < 0.60 → `CREATE` candidate
4. **Planning call** — a single LLM call produces the **Compilation Plan**: a prioritized list of pages to create or update, each with entity coverage and cross-link targets

The plan is saved to `source_compilation_plans` with `status = pending_review`.

### Phase 2.5 — Human plan review

Before any pages are written, an editor reviews the Compilation Plan:

- **Portal:** Knowledge Base → source row with "Review Plan" status → click to open the plan review dialog
- **API:** `GET /api/sources/{id}/plan` → `POST /api/sources/{id}/plan/approve` or `/reject`

The plan shows every page that will be created or updated, its type, and the entities it will cover. Editors can approve as-is, submit a modified plan (reorder, rename, remove pages), or reject with a note.

**Auto-approve** is available for CI/CD or trusted pipelines: set `MRP_AUTO_APPROVE_PLAN=true` in your environment.

### Phase 3 — REFINE

Each planned page gets its own writer. Writers run in parallel (up to 4 at once). Every writer receives pre-assembled evidence — the relevant claims with their source excerpts — so it never needs to scan the full document.

**Simple writer** (≤ 8 evidence items, existing page ≤ 3K chars): a single `llm.generate()` call.

**Complex writer** (larger pages): a mini agent loop (max 10 steps) with tools:

| Tool | Purpose |
|---|---|
| `read_kb_page` | Read any existing wiki page for cross-referencing |
| `read_source_excerpt` | Read more context from the source document |
| `finish` | Submit the completed page content |

Every factual claim in the written content is marked with a `[^N]` footnote citation.

### Phase 4 — VERIFY

Three non-blocking checks run after REFINE:

1. **Citation verification** — each `[^N]` claim is checked against its source excerpt by the LLM. Verdicts: `SUPPORTED` (no change), `PARTIAL` (caveat added), `NOT_SUPPORTED` (marked `[unverified]`), `CONTRADICTED` (flagged with warning marker).

2. **Coverage check** — entities mentioned ≥ 3 times in chunk extracts but not covered by any planned page are logged as warnings.

3. **Conflict check** — new page content is embedded and compared against existing KB pages. Semantically similar pages (sim > 0.80) are checked for factual contradictions by the LLM and logged.

All three checks are informational — they never block the pipeline.

### Phase 5 — COMMIT

All verified pages are written to the database in a single atomic transaction. For each page:
- `CREATE` → `wiki_service.apply_create()`
- `UPDATE` → `wiki_service.apply_update()` (falls back to create if the page was deleted)

After all pages are flushed, the wiki index is regenerated, an activity log entry is appended, and the source is marked `ready`.

COMMIT is fail-fast and atomic. A failure on any page rolls back the complete commit, so a source cannot become `ready` with only part of its planned knowledge saved. `REFINE complete` and `VERIFY complete` are intermediate milestones; durable wiki data is confirmed only by `MRP COMMIT complete` and source progress `100`.

For large documents, the worker timeout defaults to 3600 seconds. REFINE writers retry transient AI-provider failures up to three times with backoff and never replace failed output with placeholder content.

### Domain-aware security preservation

When a source uses a pentest, red-team, exploit or vulnerability Knowledge Type, its extraction policy is carried through every MRP phase. Commands, payloads, code blocks, CVE identifiers and ATT&CK IDs are also extracted deterministically with source offsets and hashes. Each artifact is routed to one relevant page and preserved verbatim, even if the LLM omits it from its prose.

Security artifacts appear under `Exact commands and payloads` when deterministic recovery was required. This section is evidence from the uploaded source, not generated syntax. Writer output that starts with agent scratchpad phrases such as “Let me search” is rejected rather than committed.

### Resume behavior

The field `source.pipeline_phase` tracks which phase completed last. If the worker crashes, the next retry picks up from the right phase:

| `pipeline_phase` | Behavior on retry |
|---|---|
| `map` | Skip chunks already extracted, process remaining |
| `reduce` | Re-run REDUCE (all chunks already done) |
| `plan_review` | Return existing plan — do not re-run MAP+REDUCE |
| `refine` / `verify` / `commit` | Re-run REFINE from plan (in-memory results are regenerated) |

### Page types

| Type | Description |
|---|---|
| `entity` | A named thing: person, company, product, location |
| `concept` | A process, rule, methodology, or framework |
| `topic` | A broad subject area |
| `source` | A page representing the source document itself |

---

## Wiki page structure

Each page is stored with:
- `slug` — URL-safe identifier (e.g. `concept/fire-safety`, `entity/acme-corp`)
- `title` — human-readable name
- `page_type` — entity / concept / topic / source
- `content_md` — full markdown content
- `summary` — one-sentence summary for index and search
- `knowledge_type_slugs[]` — which knowledge types this page belongs to
- `source_ids[]` — which source documents contributed to this page
- `embedding` — vector for semantic search (pgvector)
- `scope_type` + `scope_id` — global or project-scoped
- `version` — current version number
- `orphaned` — true if all contributing sources have been deleted

---

## Version history

Every change to a wiki page creates an immutable revision record:

```
WikiPageRevision
  page_id       → which page
  version       → monotonically increasing integer
  content_md    → full snapshot of the content at this version
  change_type   → agent_compile | editor_edit | draft_approved | rollback
  changed_by_id → which employee (null for agent compilations)
  change_note   → optional description
  draft_id      → linked draft if change_type = draft_approved
```

### Accessing revision history

- **Portal:** Wiki page → History tab → list of all versions
- **API:** `GET /api/wiki/pages/{slug}/revisions`

### Rollback

Admins can restore any previous version:
- **Portal:** History tab → select version → Rollback
- **API:** `POST /api/wiki/pages/{slug}/revisions/{version}/rollback`

Rollback creates a new revision with `change_type=rollback` — the history is preserved, not overwritten.

---

## Editing wiki pages

Two paths depending on your role:

### Direct edit (Editor / Admin)

Editors can edit a page directly — no review step. The change takes effect immediately and a revision is created.

- **Portal:** Open wiki page → Edit button
- **API:** `PUT /api/wiki/pages/{slug}`
- **MCP:** `edit_wiki_page(slug, content_md, change_note)`

Requires: **workspace editor+** for workspace-scoped pages, or **`wiki:write:all`** for global pages.

### Propose a draft (Contributor)

Contributors propose edits that go through editor review before being applied.

- **Portal:** Open wiki page → Propose Edit
- **API:** `POST /api/wiki/pages/{slug}/drafts`
- **MCP:** `propose_wiki_edit(slug, content_md, note)`

Requires: **workspace contributor+** for workspace-scoped pages, or **`wiki:write:own_dept`** for global pages.

---

## Draft workflow

```
Contributor submits draft
    │
    ▼
Draft status: pending
    │
    ├── Editor reviews → Approve
    │       │
    │       └── content_md applied to page
    │           WikiPageRevision(change_type=draft_approved) created
    │           Draft status → approved
    │
    └── Editor reviews → Reject (reviewer_note required)
            │
            └── Draft status → rejected
                Contributor can see the rejection reason
```

Multiple drafts can be pending for the same page at the same time. Editors resolve them one by one — approving a draft applies its content; later drafts may need to be reviewed again if their base was outdated.

### Editor review actions

**Via portal:** Wiki Drafts queue → select draft → compare side-by-side → Approve or Reject.

**Via API:**
- `GET /api/wiki/drafts` — list pending drafts (filtered to your scope)
- `GET /api/wiki/pages/{slug}/drafts` — drafts for a specific page
- `GET /api/wiki/drafts/{id}` — full draft with current page content
- `POST /api/wiki/drafts/{id}/approve` — approve (optionally with edited content)
- `POST /api/wiki/drafts/{id}/reject` — reject (reviewer_note required)

**Via MCP (for Claude Desktop editors):**
- `list_pending_drafts(workspace_id?)` — see pending drafts
- `review_draft(draft_id)` — read draft vs current content
- `approve_draft(draft_id, reviewer_note?, edited_content_md?)`
- `reject_draft(draft_id, reviewer_note)`

---

## Scope: Global vs. Workspace

Wiki pages are either global or workspace-scoped:

**Global pages** — visible to all employees who have `wiki:read` permission.
Compiled from global sources (documents not assigned to any specific workspace).

**Workspace-scoped pages** — visible only to workspace members.
Compiled from workspace-owned sources. Accessible through the workspace wiki browser.

When a source is uploaded directly into a workspace (via the workspace Sources tab), its compiled wiki pages are automatically scoped to that workspace.

---

## Orphaned pages

When all source documents contributing to a wiki page are deleted, the page is marked `orphaned = true`. It is NOT automatically deleted — editors can review orphaned pages and decide whether to keep, update, or remove them.

- **API:** `GET /api/wiki/orphaned` (admin only)

---

## Knowledge graph

## Source-aware knowledge provenance

Arkon v2 stores the generated contribution from each source separately in
`wiki_page_contributions`. A wiki page is the canonical synthesis of those
contributions; `wiki_pages.source_ids` remains a fast denormalized index.

This changes source lifecycle behavior:

- Uploading a document creates or replaces that document's contribution to each planned page.
- Updating a shared concept rebuilds it from all current contributions instead of overwriting another source.
- Deleting a source deletes pages owned only by that source and rebuilds shared pages from the remaining sources.
- Embeddings and wiki links are refreshed after a rebuild.
- `GET /api/sources/{source_id}/knowledge-impact` previews which pages will be deleted, rebuilt, or treated as legacy before deletion.

Migration `023` losslessly backfills existing single-source pages. Historical
multi-source pages cannot be separated after the fact and are marked
`provenance_complete=false`; new pages and new contributions use complete
source-aware provenance.

Wiki pages are linked via `[[wikilinks]]` in their content. Arkon extracts these links into a `wiki_links` table, enabling:

- **Backlinks** — which pages link to this one
- **Outlinks** — which pages this one links to
- **Graph visualization** — interactive node/edge graph in the portal

The full graph is available at `/wiki/graph`. Each workspace also has a scoped graph at `GET /api/projects/{id}/wiki/graph`.

---

## Wiki index and log

Two reserved pages are maintained automatically:

- `_index` — a catalog of all wiki pages, updated after each compilation
- `_log` — a chronological log of ingestion and compilation events

These are visible in the wiki browser and accessible via:
- `GET /api/wiki/index`
- `GET /api/wiki/log`
