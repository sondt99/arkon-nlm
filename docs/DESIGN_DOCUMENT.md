# Arkon — System design

**Version:** 0.1.0  
**Date:** 2026-08-18  
**Repo:** [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

This is the formal spec. Operator guides live next to it in `docs/`. When you change APIs, models, pipeline behavior, RBAC, or MCP tools, update the matching section here in the same change.

---

## Contents

1. [Overview](#1-overview)
2. [Technical architecture](#2-technical-architecture)
3. [Data model](#3-data-model)
4. [API specification](#4-api-specification)
5. [Use cases](#5-use-cases)
6. [MRP pipeline](#6-mrp-pipeline)
7. [RBAC](#7-rbac)
8. [MCP](#8-mcp)

---

## 1. Overview

Arkon is a **self-hosted enterprise knowledge hub**. Organizations upload documents. A background compiler (MRP) turns them into an interlinked markdown wiki. People use the web portal; AI clients use MCP. The same permission model applies to both.

### 1.1 Goals

- One compiled wiki instead of ad-hoc RAG chunks
- Citations back to source excerpts
- Department RBAC and workspace isolation
- Provider-agnostic LLM / embedding / vision
- No data leaves the host except calls to the configured AI provider (and optional NotebookLM)

### 1.2 Actors

| Actor | Job |
|---|---|
| System admin | People, roles, providers, workspaces, audit |
| Employee (role = custom Role) | Upload, read, draft, chat — within permissions |
| Workspace member | Same actions inside one project, gated by membership role |
| MCP agent | Tools on `/mcp` as the employee behind the `ark_` token |

### 1.3 Product surfaces

| Surface | Entry |
|---|---|
| Portal | nginx `/` → Next.js |
| REST | nginx `/api` → FastAPI |
| MCP | nginx `/mcp` → FastMCP |
| Chatbot | Portal `/chat` + `/api/chat` |
| Claude Code gateway | `/api/claude-gateway/v1/messages` |
| Export API | `/api/export/v1/*` |
| NotebookLM | Portal `/notebooklm` + `/api/notebooklm` |

### 1.4 Out of scope (v0.1.0)

- Multi-tenant SaaS
- Native mobile apps
- Neo4j / contacts directory (removed)
- True token-by-token gateway streaming
- Per-user NotebookLM sessions (one server session)

---

## 2. Technical architecture

### 2.1 Stack

| Layer | Choice |
|---|---|
| Portal | Next.js 16, React 19, Tailwind 4 |
| API | FastAPI, Pydantic v2, SQLAlchemy 2 (async) |
| MCP | FastMCP, streamable HTTP, `stateless_http=True` |
| Jobs | arq + Redis 7 (two queues) |
| DB | PostgreSQL 16 + pgvector |
| Objects | MinIO |
| Ingress | nginx (only published port) |

### 2.2 Runtime topology

See the diagram in [ARCHITECTURE.md](ARCHITECTURE.md). Default Compose publishes `127.0.0.1:3119` only. An external Docker network `arkon_default` lets a LAN proxy reach `arkon_nginx`.

### 2.3 Backend layout

```text
app/
  main.py            FastAPI app, CORS, MCP mount, routers, health
  config.py          env Settings + secret validators
  worker.py          arq WorkerSettings + SkillWorkerSettings
  routers/           HTTP
  services/          domain logic
  ai/                providers + mrp/{mapper,merger,pipeline,reducer,writer,verifier}
  mcp/               tools.py, resources.py, server.py
  database/          models.py, repository.py
```

Routers depend on `get_current_user` / `require_permission` / `require_admin`. They do not embed SQL for policy.

### 2.4 Startup

`entrypoint.sh`: create writable dirs → `alembic upgrade head` → seed built-in skills → exec uvicorn/arq.

API lifespan: MinIO bucket, seed admin, seed skills, seed security KT hints, reject dangerous CORS/`SECRET_KEY` defaults.

### 2.5 AI providers

Runtime-selected, stored in `app_config` (Fernet via `SECRET_KEY`):

| Provider | Embedding | LLM | Vision |
|---|---|---|---|
| Google | yes | yes | yes |
| OpenAI | yes | yes | yes |
| Anthropic | no | yes | yes |
| Ollama | yes | yes | yes |
| 9Router | yes | yes | yes |
| Omniroute | yes | yes | yes |

Embedding vectors go to one of `wiki_page_embeddings_{768,1024,1536,3072}`. Switching dimension enqueues `reembed_all_pages_task`.

---

## 3. Data model

Latest revision: `033_nlm_passthrough_ownership` (`uv run --extra dev alembic heads` → `033`). Source of truth: `app/database/models.py`.

### 3.1 Identity

| Table | Purpose |
|---|---|
| `departments` | Org units for `own_dept` |
| `employees` | Login, `role` (`admin`/`employee`), `custom_role_id`, `mcp_token` hash, `is_active` |
| `roles` | Named permission lists (`is_system` presets) |

### 3.2 Taxonomy and sources

| Table | Purpose |
|---|---|
| `knowledge_types` | `slug`, `description`, `extraction_hints`, `color` |
| `sources` | Uploaded item: type, status, `pipeline_phase`, scope, storage key, progress |
| `source_departments` | M2M visibility |
| `source_images` | Extracted page images |
| `source_chunk_extracts` | MAP output (resume) |
| `source_compilation_plans` | REDUCE output awaiting review |

Source `status`: `pending` · `processing` · `plan_ready` · `ready` · `error`.

Source `pipeline_phase` (a **separate** column tracking MRP progress, not a status): `map` · `reduce` · `plan_review` · `refine` · `verify` · `commit`.

> The awaiting-review **status** is `plan_ready` (`app/worker.py`). `plan_review` is a `pipeline_phase` value (`app/ai/mrp/reducer.py`). Filtering `status == "plan_review"` matches zero rows silently — no error, just an empty result that reads as "nothing pending review".

Since migration `034` these vocabularies are enforced by `CHECK` constraints rather than being
a convention. Six columns carry one — `sources.status`, `sources.scope_type`,
`source_compilation_plans.status`, `wiki_page_drafts.status`, `embedding_jobs.status`,
`notebooklm_artifacts.status` — and the allowed values are defined once in
`app/database/models.py` (`SOURCE_STATUSES` and friends).

This matters because a *write* of an unknown value used to be silent: the row was created and
then skipped by every listing filter, so the source vanished from the UI with nothing logged.
It is now an `IntegrityError` at the boundary. Adding a value therefore means editing the tuple
in `models.py` **and** shipping a migration that replaces the constraint; a CI test fails if
code writes a status no vocabulary permits.

Two columns are deliberately left unconstrained and are worth knowing about:
`Skill.scope_type` legitimately holds `department` (the others do not), and
`wiki_pages.scope_type` / `chat_conversations.scope_type` are written only through normalising
helpers.

### 3.3 Wiki

| Table | Purpose |
|---|---|
| `wiki_pages` | slug, title, type, markdown, summary, scope, KT slugs, version |
| `wiki_links` | `[[wikilink]]` edges |
| `wiki_page_contributions` | which source wrote which page |
| `wiki_page_drafts` | proposed markdown + reviewer fields |
| `wiki_page_revisions` | history for rollback |
| `wiki_page_embeddings_*` | one row per page per dimension family |

Special slugs: `_index`, `_log` (regenerated on COMMIT).

### 3.4 Workspaces

| Table | Purpose |
|---|---|
| `projects` | Workspace header (`workspace_type`, status) |
| `project_members` | employee + `WorkspaceRole` |
| `project_sources` | attachment |

`WorkspaceRole`: viewer < contributor < editor < admin (`WORKSPACE_ROLE_HIERARCHY`).

### 3.5 Skills

| Table | Purpose |
|---|---|
| `skills` | slug, status, scope, `is_system`, current version |
| `skill_departments` | M2M |
| `skill_versions` | number, hash, storage prefix, changelog |
| `skill_contributions` | sandbox copy + review state |

### 3.6 Chat, NotebookLM, ops

| Table | Purpose |
|---|---|
| `chat_conversations` / `chat_messages` | Portal chatbot |
| `notebooklm_notebooks` / `notebooklm_artifacts` | Portal NLM state |
| `notebooklm_passthrough_owners` | Who created which live NotebookLM notebook (`033`) |
| `app_config` | Encrypted provider settings |
| `embedding_jobs` | Re-embed progress |
| `audit_log` | Who / action / resource / metadata |
| `notes` | Small personal notes |

> The audit table is **`audit_log`**, singular — `models.py` (`AuditLog.__tablename__`) and
> `alembic/versions/007_scope_rbac.py`. A query written against `audit_logs` fails with
> `relation "audit_logs" does not exist`, and this is the table compliance work reads.

`notebooklm_passthrough_owners` records nothing but a claim of ownership over an opaque
Google notebook id, so it is deliberately separate from `notebooklm_notebooks`:

| Column | Type | Notes |
|---|---|---|
| `id` | uuid | PK |
| `nlm_id` | varchar(200) | NOT NULL, unique (`uq_notebooklm_passthrough_owners_nlm_id`) |
| `owner_employee_id` | uuid | NOT NULL, FK `employees.id` `ON DELETE CASCADE`, indexed |
| `created_at` | timestamptz | `now()` |

One owner per notebook, enforced by the unique constraint — a second row would silently
grant a second employee full access. `ON DELETE CASCADE` (not `SET NULL`) is deliberate: a
row whose owner is gone answers no question this table exists to answer, and dropping it
returns the notebook to the admin-only bucket. Migration `033` also adds
`ix_notebooklm_notebooks_notebook_id`, because the ownership guard queries that column on
every passthrough request. See §7.4.

### 3.7 Legacy tables with no model

`knowledge_scopes` (`002_rbac.py`) and `scope_memberships` (`007_scope_rbac.py`) still exist
in every migrated database. Nothing in `app/` maps or reads them — the scope realm they
belonged to was replaced by `projects` / `project_members`. They are inert, not cleaned up.
Contacts, by contrast, really were dropped, in migration `009`.

---

## 4. API specification

Full tables: [API-REFERENCE.md](API-REFERENCE.md). Rules that the tables do not repeat:

### 4.1 AuthN

| Client | Scheme |
|---|---|
| Portal | JWT HS256, 24 h, `iss=arkon`, `aud=arkon-api` |
| `<img>` proxy | JWT in header **or** `?token=` |
| MCP / export / gateway | `ark_` bearer (gateway also accepts `x-api-key`) |

Login is rate-limited per IP (nginx + Redis). Passwords are bcrypt.

### 4.2 AuthZ

Every mutating route and every scoped GET goes through `permission_engine` (global) and/or workspace membership. System `admin` short-circuits.

### 4.3 Prefix map

| Prefix | Router file |
|---|---|
| `/api/auth` | `auth.py` |
| `/api/sources` | `sources.py` |
| `/api/wiki` | `wiki.py`, `wiki_drafts.py`, `wiki_images.py` |
| `/api/projects` | `projects.py` |
| `/api/departments`, `/api/employees`, `/api/my` | `rbac.py` |
| `/api/roles` | `roles.py` |
| `/api/knowledge-types` | `knowledge_types.py` |
| `/api/settings`, `/api/dashboard` | `admin_settings.py` |
| `/api/settings/embeddings` | `admin_embeddings.py` |
| `/api/skills` | `skills.py` |
| `/api/skill-contributions`, `/api/admin/skill-contributions` | `skill_contributions.py` |
| `/api/chat` | `chat.py` |
| `/api/notebooklm` | `notebooklm.py` |
| `/api/export` | `export_api.py` |
| `/api/claude-gateway` | `claude_gateway.py` |
| `/api/audit` | `audit.py` |
| `/api/notes` | `notes.py` |
| `/mcp` | FastMCP app mounted in `main.py` |

### 4.4 Error shape

Most routes: `{ "detail": "…" }` (FastAPI default).  
Gateway: `{ "type": "error", "error": { "type", "message" } }`.

### 4.5 Request size limits

Three layers, outermost first. All of them answer **413** in the `{ "detail": … }` shape.

| Layer | Where | Cap | Setting |
|---|---|---|---|
| Edge | `nginx.conf` `location /api/` | 260 MB | `client_max_body_size` |
| Every request | `BodySizeLimitMiddleware` (`app/services/upload_guard.py`) | 256 MB | `MAX_REQUEST_BODY_MB` |
| Per route | the upload guards below | 100 / 200 MB | `MAX_UPLOAD_MB`, `MAX_ZIP_UPLOAD_MB` |

`BodySizeLimitMiddleware` is registered in `main.py` **before** CORS, so CORS ends up
outermost and the 413 carries `Access-Control-Allow-Origin` instead of surfacing in the
browser as an opaque network error. It applies to **every** route, including `/mcp`, the
health endpoints, and routes that take no upload at all. It checks the `Content-Length`
header *and* keeps a running total over the delivered body, so a chunked or lying client is
still cut off, and it closes the connection rather than draining the remainder. Route-level
guards cannot replace it: they only run once the ASGI server has already received the whole
body. `MAX_REQUEST_BODY_MB` must stay above the largest per-route cap, or `Settings()`
refuses to construct (`app/config.py`).

Per-route caps, all 413:

| Route | Cap |
|---|---|
| `POST /api/sources/upload` | `MAX_UPLOAD_MB` (100) |
| `POST /api/projects/{id}/sources/upload` | `MAX_UPLOAD_MB` (100) |
| `POST /api/skill-contributions/{id}/upload` | `MAX_UPLOAD_MB` (100) |
| `POST /api/notebooklm/nlm/notebooks/{nlm_id}/sources/upload` | `MAX_UPLOAD_MB` (100) |
| `POST /api/sources/upload-zip` | `MAX_ZIP_UPLOAD_MB` (200) |
| `POST /api/skills/upload`, `/{slug}/reupload`, `/inspect-zip` | `MAX_ZIP_UPLOAD_MB` (200), per file |
| `PUT /api/skill-contributions/{id}/files` | `MAX_CONTRIBUTION_TEXT_KB` (1024 KB of UTF-8) |

The text cap exists because `PutFileRequest.content` arrives as a JSON string, so no
multipart guard applies to it.

**Cumulative per-contribution budget.** Per-file caps bound one request, not the workspace,
so `POST /api/skill-contributions/{id}/upload` and `PUT …/files` also check the whole
contribution prefix in MinIO before writing: `MAX_CONTRIBUTION_TOTAL_MB` (25) → **413**, and
`MAX_CONTRIBUTION_FILES` (200) → **400**. Overwriting an existing path credits back the old
object's bytes and does not count against the file total.

**ZIP extraction** caps the *decompressed* archive, since the upload cap bounds compressed
bytes only. On `POST /api/sources/upload-zip` every failure is **422** (`ZipExtractionError`
→ `HTTPException(422)`): a corrupt or non-ZIP payload (`"Invalid or corrupted zip file."`), a
corrupt member, `MAX_ZIP_ENTRIES` (50), or `MAX_ZIP_TOTAL_MB` (500). `MAX_ZIP_MEMBER_MB` (50)
skips the entry rather than failing the request, and is enforced on bytes actually inflated
rather than the archive's self-declared sizes; if nothing survives, the response is 422
`"No supported files found in the archive."` A filename not ending in `.zip` is 400.

> The skill ZIP paths do **not** go through `zip_service`. `SkillService` uses raw `zipfile`
> with its own hardcoded caps (10 MB uncompressed, 100 files, both 400), and a corrupt
> archive on the non-admin contribution branch of `POST /api/skills/upload` still reaches the
> generic handler as **500 `"ZIP extraction failed."`** The 4xx-for-a-corrupt-archive
> guarantee holds for `/api/sources/upload-zip` only.

Other request-shape caps worth knowing: `POST /api/wiki/images/resolve` accepts 100 ids
(400 over that), and a proposed draft's `content_md` is capped at 50,000 **characters** by a
Pydantic validator, so it fails as 422 with the validation envelope rather than a bare
`detail` string.

---

## 5. Use cases

### UC-1 First install

Admin clones the repo, creates `arkon_default`, fills `.env.docker`, starts Compose, logs in, sets embedding + LLM. **Success:** `/api/health` healthy, Settings tests pass.

### UC-2 Ingest a global document

Employee with `doc:create:own_dept` uploads a PDF + knowledge type. Worker extracts → MAP → REDUCE → plan. Editor approves. REFINE/VERIFY/COMMIT write pages. **Success:** source `ready`, pages searchable.

### UC-3 Ingest into a workspace

Workspace Editor uploads with `scope=project`. Pages get `scope_id` = that workspace. Non-members cannot read them in the portal or via MCP.

### UC-4 Ask the chatbot

User sends a message. If an embedding model is active, the service embeds the query and pulls top wiki pages in scope; otherwise it skips retrieval and still calls the LLM. Then it stores the turn. Optional **Add to Wiki** creates a page from the thread.

### UC-5 Claude asks a question

Client calls `search_wiki` then `read_wiki_page`. Empty results mean “out of scope,” not “does not exist.”

### UC-6 Propose vs edit

Contributor calls `propose_wiki_edit` (or the portal editor in draft mode). Editor `approve_draft`. Direct `edit_wiki_page` / `PUT /wiki/pages/{slug}` requires write-all or workspace Editor+.

### UC-7 Switch embeddings

Admin picks a model with a new dimension. API enqueues `reembed_all_pages_task`. Search uses the new table when the job completes.

### UC-8 NotebookLM enrich

Admin connects a master token. User sends a source, generates a report, **Add to Wiki**. Worker runs `notebooklm_ingest_artifact_task`. The Google session is shared, but a user sees and acts on only the notebooks they created — see §7.4. Notebooks that predate that boundary are visible to admins only.

### UC-9 Skill contribution

Employee opens a contribution, edits files, submits. Reviewer approves → new `SkillVersion`, slug’s current version increments.

---

## 6. MRP pipeline

Implemented in `app/ai/mrp/`. Jobs: `ingest_map_reduce_task` then `ingest_refine_task` (after approval).

| Phase | Module | Durable output |
|---|---|---|
| Triage | `pipeline.py` | strategy on the source |
| MAP | `mapper.py` | `source_chunk_extracts` |
| REDUCE | `reducer.py`, `merger.py` | `source_compilation_plans` |
| Review | `sources` router | plan status |
| REFINE | `writer.py` | in-memory pages |
| VERIFY | `verifier.py` | annotations / warnings |
| COMMIT | `pipeline.py` + `wiki_service` | pages, links, embeddings, index |

Config knobs (`MRP_*`) are on `Settings` in `app/config.py` (chunk size, concurrency, similarity thresholds, timeouts). Relationships are validated (overlap < target, maybe < update, ambiguous < merge).

Security types: `security_artifacts.py` extracts commands/payloads/CVE/ATT&CK with offsets and hashes; writers must preserve them.

COMMIT is atomic. Resume rules: [WIKI.md](WIKI.md).

Narrative job timeout default: 3600 s. `job_completion_wait` 300 s; Compose `stop_grace_period` is 330 s so in-flight jobs can finish.

### 6.1 Prompt assembly and the untrusted-content envelope

Uploaded documents, wiki pages, category hints, and chat transcripts are all attacker-reachable
text. Every prompt that carries them states where that text begins and ends, using a delimiter
the content cannot forge. Primitives live in `app/ai/providers/base.py`:

| Name | What it is |
|---|---|
| `UNTRUSTED_DOCUMENT_TAG` | `untrusted_document` — uploaded text, chunks, excerpts, evidence, security artifacts |
| `UNTRUSTED_KB_CONTEXT_TAG` | `untrusted_kb_context` — existing wiki pages read back into a prompt |
| `UNTRUSTED_HINTS_TAG` | `untrusted_category_hints` — `knowledge_types.extraction_hints` |
| `UNTRUSTED_CONVERSATION_TAG` | `untrusted_conversation` — chat transcript in conversation → wiki synthesis |
| `UNTRUSTED_TAGS` | all four, so one strip call clears every marker the codebase uses |
| `new_envelope_nonce()` | `secrets.token_hex(4)` — a fresh 8-hex suffix per call |
| `strip_envelope_markers()` | removes opener/closer forms from the content before it is embedded |
| `flatten_untrusted_metadata()` | for labels that must render *outside* an envelope: strip, collapse whitespace, cap at 200 chars |

A rendered block is `<{tag}_{nonce}> … </{tag}_{nonce}>`, always preceded by a trusted boundary
statement telling the model the span is data. The nonce is what makes it hold — with a fixed tag
name a document could simply write the closing form and have everything after it read as
operator instructions. Document-derived labels (titles, slugs, section paths, category names)
are rendered outside the envelopes, so they go through `flatten_untrusted_metadata` instead.

Envelopes are in use in MAP (`mapper.py`), REDUCE and planning (`reducer.py`), REFINE
(`writer.py`, including agent-loop tool results, which share the loop's nonce), the single-shot
compiler (`wiki_compiler.py`), the chatbot (`chat_service.py`), and conversation → wiki
synthesis (`app/routers/chat.py`). Extraction hints are wrapped at all four points that inject
them, and are framed as advisory: they may steer attention, they cannot change the output
format or lift a restriction.

**Not fenced, deliberately recorded here rather than implied otherwise:**

- `merger.py` and `verifier.py` interpolate page bodies raw.
- Chat conversation history is neither wrapped nor stripped. It sits after the KB envelope has
  closed, so it cannot terminate that block, but it can contain a forged opener.
- The Claude Code gateway is a passthrough proxy — the messages are the client's own.
- Model-emitted `[[wikilinks]]` are not checked against the plan's slug allow-list. The
  allow-list exists only as prompt text; `wiki_service.refresh_links` inserts whatever matched
  the wikilink regex, and those edges drive the chatbot's 1-hop retrieval expansion.

Model output is de-fenced and JSON-parsed tolerantly rather than schema-enforced. The
structural defenses that do exist are separate from the envelope: slug format validation,
deterministic reconciliation overriding the planner, writer-output rejection of placeholder and
agent-chatter text, truncation detection on merges, and re-appending security artifacts the
model dropped.

### 6.2 Chatbot prompt

The chatbot builds one system prompt and one user turn (`app/services/chat_service.py`).

| Position | Content |
|---|---|
| System | Persona and instructions **only** — including how to treat the fenced block |
| User turn, 1 | Retrieved KB pages, inside `untrusted_kb_context` |
| User turn, 2 | Conversation history (last `CHAT_HISTORY_MESSAGES` messages) |
| User turn, 3 | The actual question, last, outside every fence |

Retrieved pages used to be appended to the **system** prompt, which made a poisoned page read
as operator instruction. They now travel in the user turn, page bodies *and titles* are
stripped, and the question is placed after the fence closes. The optional second
"expand a short answer" call re-uses the same already-fenced context block.

---

## 7. RBAC

Two realms. Implementation: `app/services/permission_engine.py`, `permissions.py`.

### 7.1 Global

Format `resource:action:own_dept|all`. Resources: `doc`, `wiki`, `skill`, plus `org:*` and `workspace:view:all`.

Default employee set (no custom role) — `EMPLOYEE_DEFAULT_PERMISSIONS` in
`app/services/permissions.py`:

```text
doc:read:own_dept
doc:create:own_dept
wiki:read:own_dept
wiki:write:own_dept
skill:read:own_dept
org:departments:read
```

`org:departments:read` is part of the default set, so an employee with no custom role can
enumerate the org chart via `GET /api/departments`. Intended, and listed here because omitting
it made the default reach look narrower than it is.

`wiki:write:own_dept` = propose drafts. `wiki:write:all` = direct edit + review. Page rollback and page delete are system-admin operations (`wiki.py`).

`can_access_skill` requires `skill:{action}:own_dept` (or `:all`) before the department comparison. A Viewer cannot PATCH or DELETE a global skill.

`org:employees:manage` cannot write `Employee.role`, reset passwords, or toggle admin accounts. Those writes require `Employee.role == admin`. The last active admin cannot be demoted or deactivated.

**No permission may be granted by someone who does not hold it.** `ensure_no_escalation`
(`app/services/employee_policy.py`) compares the permissions being granted against the actor's
own effective set and answers **403 “You cannot grant permissions you do not hold yourself: …”**.
It guards three writes, all of which also require `org:roles:manage` / `org:employees:manage`
first:

| Write | Rule |
|---|---|
| `POST /api/roles` | Every permission on the new role must be one the caller holds |
| `PUT /api/roles/{id}` | Same, minus the permissions the role already carried |
| `POST /api/employees`, `PUT /api/employees/{id}` — `custom_role_id` | The assigned role's permission set must be a subset of the caller's |

System `admin` short-circuits all three. Clearing `custom_role_id` is always allowed — removing
authority is not escalation. Assigning a `custom_role_id` that does not exist is 404, not a
silent write. Separately, an actor cannot change **their own** `custom_role_id` to a different
role (403 “You cannot change your own custom role”), mirroring the existing rule for their own
system role; that one has no admin exemption.

Both were live escalations before: a holder of `org:roles:manage` could mint a role carrying
`:all` permissions and attach it to themselves, and `Employee.custom_role_id` was written
straight from the request body with no authorization at all.

Skill-contribution approval uses the **target skill's** current departments. A contributor cannot claim `scope_type=global` to skip department review. Approval does not clear `SkillDepartment` rows unless a system admin passes an explicit `final_scope_type`.

MCP / export wiki visibility: `ResolvedIdentity.allowed_knowledge_types` is populated in `_resolve_scope` (`None` = unrestricted, `[]` = no wiki access). Pages with an empty KT array are not world-readable when a restriction is set. `read_wiki_index` returns a filtered catalog for restricted tokens.

Workspace-scoped sources (`scope_type=project`) are **not** treated as global just because they have no `source_departments` rows. `can_access_document` requires workspace membership first; `doc:read:all` does not open another team's files. Members may read; editor+ may edit/delete via the global source endpoints.

`workspace:view:all` is in the permission catalog but **not** consulted by `GET /api/projects` — only `Employee.role == admin` sees every workspace.

Role presets (Viewer, Contributor, Department Admin, Knowledge Admin) are **templates in code** (`ROLE_PRESETS`). Alembic seeds system roles named Admin / Employee; it does not insert those four presets as rows.

### 7.2 Workspace

Membership role ladder. Global permissions are ignored inside a workspace except that system `admin` is implicit Admin.

Last-admin protection on demote/remove.

### 7.3 MCP

Token → employee → `ResolvedIdentity` (allowed KTs, allowed source ids, admin flag, memberships). Tools call `apply_scope_filter`.

The four wiki tools additionally check `identity.wiki_readable` — set in `_resolve_scope` iff the
employee holds `wiki:read:own_dept` or `wiki:read:all` — and return
`"Access denied: your token's role does not include wiki:read."` when it is false. MCP tools
return text, so a denial is an error string, not an HTTP status. The source-reading tools are not
`wiki_readable`-gated; they scope per source through `apply_scope_filter`.

### 7.4 NotebookLM passthrough

The `/api/notebooklm/nlm/*` routes proxy a **single shared Google session**, so the identity of
the notebook's creator is Arkon's only ownership signal. Every route that takes an `{nlm_id}`
resolves it through `_resolve_nlm_notebook` (`app/routers/notebooklm.py`), which accepts the id
only if one of these holds:

1. the caller is a system `admin`; or
2. `notebooklm_passthrough_owners` has a row for `(nlm_id, caller)`; or
3. `notebooklm_notebooks` has a row for that id created by the caller.

Anything else is **404 “Notebook not found”** — not 403, because 403 would confirm that the id
names a real notebook in the shared account and let a caller enumerate other employees'
notebooks one id at a time.

**Unowned notebooks are admin-only.** Everything predating this boundary, plus anything created
directly in the Google account, matches none of the three cases; there is no backfill that could
invent a creator. Treating unowned as public would restore the company-wide read this exists to
stop, and hiding them from lists while still honouring a guessed id in `DELETE` would leave them
invisible-but-deletable. Admin-only keeps them reachable for cleanup.

An ownership row is written at exactly one place: a successful `POST /api/notebooklm/nlm/notebooks`.
Opening, listing, or chatting with a notebook never creates one. The Arkon-side
`POST /api/notebooklm/notebooks` does not write one either — it records
`created_by_employee_id`, which is what case 3 reads. Rows are deleted on a confirmed delete, and
cascade when the owning employee is deleted.

The two `/nlm/*` routes with no `{nlm_id}` are handled separately: the list endpoint filters the
upstream list to the caller's owned ids (admins see all), and create has no caller-supplied id to
authorize. Ingest routes additionally require `doc:create`, and attaching an existing Arkon source
to a notebook re-checks `can_access_document` on that source.

---

## 8. MCP

Server: `create_mcp_server()` mounted at `/mcp`.

### 8.1 Tools (16)

Read: `search_wiki`, `read_wiki_index`, `read_wiki_page`, `list_wiki_pages`, `list_sources`, `get_source`, `get_source_outline`, `get_source_pages`, `list_knowledge_types`, `get_knowledge_type_docs`.

Write: `propose_wiki_edit`, `edit_wiki_page`, `list_pending_drafts`, `review_draft`, `approve_draft`, `reject_draft`.

No contact-directory tool. No skill-execution tool.

### 8.2 Resources

`arkon://about`, `arkon://wiki-index`.

### 8.3 Client contract

```http
Authorization: Bearer ark_<token>
```

Wiki-first: search pages, then sources for citations. Repo skills `skills/arkon-query|edit|review` encode that contract for Claude Code.

Scope is `doc:read` (`all` / `own_dept` + global sources) plus workspace membership, and — for the
wiki tools — `wiki:read` plus the knowledge-type restriction on the resolved identity. See §7.1
and §7.3.

---

## Appendix — environment

Infrastructure settings are env-only (`app/config.py`). AI keys live in `app_config`, except Omniroute which can be bootstrapped from env when the LLM slot is still empty: `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL`. A saved Admin Settings value always wins.

Must-set in production: `SECRET_KEY`, `DEFAULT_ADMIN_PASSWORD`, `POSTGRES_PASSWORD` + matching `DATABASE_URL`, `REDIS_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_PUBLIC_ENDPOINT`.

`Settings` rejects known-weak values at construction time, so the app refuses to start rather
than running with them. Each credential is checked independently: `SECRET_KEY` at its default,
`DEFAULT_ADMIN_PASSWORD` in `{change-me-admin-password, admin123, admin, password}`,
`MINIO_ACCESS_KEY` == `minioadmin`, `MINIO_SECRET_KEY` in
`{minioadmin123, change-me-minio-secret-key}`, an empty or `change-me-redis-password`
`REDIS_PASSWORD`, and a `DATABASE_URL` still embedding `arkon_secret` or
`change-me-postgres-password`. `ARKON_ALLOW_DEFAULT_SECRET=1` skips **all** of these — local
development only.

CORS default is empty (same-origin). `CORS_ORIGINS=*` raises unless `ARKON_ALLOW_CORS_WILDCARD=1`.

Both escape hatches are declared as real `Settings` fields, so they work from `.env` / `.env.local`
as well as from the process environment.

### Settings groups

| Group | Vars |
|---|---|
| Core | `DATABASE_URL`, `SECRET_KEY`, `DEFAULT_ADMIN_EMAIL`, `DEFAULT_ADMIN_PASSWORD`, `CORS_ORIGINS` |
| MinIO | `MINIO_ENDPOINT`, `MINIO_PUBLIC_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE`, `MINIO_PRESIGN_EXPIRY_MINUTES` |
| Redis / worker | `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB`, `WORKER_MAX_JOBS`, `WORKER_JOB_TIMEOUT` |
| Uploads | `MAX_UPLOAD_MB`, `MAX_ZIP_UPLOAD_MB`, `MAX_REQUEST_BODY_MB` |
| ZIP extraction | `MAX_ZIP_ENTRIES`, `MAX_ZIP_MEMBER_MB`, `MAX_ZIP_TOTAL_MB` |
| Contribution workspaces | `MAX_CONTRIBUTION_TEXT_KB`, `MAX_CONTRIBUTION_TOTAL_MB`, `MAX_CONTRIBUTION_FILES` |
| MCP | `MCP_TOKEN_EXPIRY_DAYS` |
| MRP | `MRP_AUTO_APPROVE_PLAN`, `MRP_INGESTION_MODEL_ID`, and the chunk / concurrency / threshold / timeout knobs |
| Chatbot | `CHAT_RAG_TOP_K`, `CHAT_LINKED_PAGES_LIMIT`, `CHAT_CONTEXT_CHARS_PER_PAGE`, `CHAT_HISTORY_MESSAGES`, `CHAT_GENERATION_TIMEOUT`, `CHAT_MIN_DETAILED_ANSWER_CHARS`, `CHAT_EXPAND_SHORT_ANSWERS` |
| Omniroute | `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL` |
| NotebookLM | `NOTEBOOKLM_STORAGE_PATH` |
| Dev escape hatches | `ARKON_ALLOW_DEFAULT_SECRET`, `ARKON_ALLOW_CORS_WILDCARD` |

`MINIO_PRESIGN_EXPIRY_MINUTES` defaults to **30**. Presigned URLs were previously good for 24
hours and re-minted on every source-detail fetch; after issuance the URL is an unauthenticated
bearer capability that survives permission revocation and account deletion.

Relationships are validated as well as values: `MAX_REQUEST_BODY_MB` must be at least the largest
per-route upload cap, `MAX_ZIP_MEMBER_MB` at most `MAX_ZIP_TOTAL_MB`, MRP chunk overlap below the
chunk target, and each MRP lower threshold below its upper one.

Templates: `.env.docker.example`, `.env.local.example`.
