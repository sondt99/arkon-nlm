# Arkon — System design

**Version:** 0.1.0  
**Date:** 2026-08-18  
**Repo:** [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

This is the formal spec. Operator guides live next to it in `docs/`. When you change APIs, models, pipeline behavior, RBAC, or MCP tools, update the matching section here in the same change.

---

## Contents

1. [Overview](#1-overview-tổng-quan-hệ-thống)
2. [Technical architecture](#2-technical-architecture-kiến-trúc-kỹ-thuật)
3. [Data model](#3-data-model-mô-hình-dữ-liệu)
4. [API specification](#4-api-specification-đặc-tả-api)
5. [Use cases](#5-use-cases-đặc-tả-use-case)
6. [MRP pipeline](#6-mrp-pipeline-luồng-xử-lý-mrp-pipeline)
7. [RBAC](#7-rbac-hệ-thống-quyền-hạn)
8. [MCP](#8-mcp-tích-hợp-mcp)

---

## 1. Overview (Tổng quan hệ thống)

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

## 2. Technical architecture (Kiến trúc kỹ thuật)

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

Embedding vectors go to one of `wiki_page_embeddings_{768,1024,1536,3072}`. Switching dimension enqueues `reembed_all_pages_task`.

---

## 3. Data model (Mô hình dữ liệu)

Latest revision: `026_legacy_ninerouter_spec_ids`. Source of truth: `app/database/models.py`.

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

Source `status`: `pending` · `processing` · `plan_review` · `ready` · `error`.

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
| `app_config` | Encrypted provider settings |
| `embedding_jobs` | Re-embed progress |
| `audit_logs` | Who / action / resource / metadata |
| `notes` | Small personal notes |

Contacts were dropped in migration `009`.

---

## 4. API specification (Đặc tả API)

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
| `/api/skill-contributions` | `skill_contributions.py` |
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

---

## 5. Use cases (Đặc tả Use Case)

### UC-1 First install

Admin clones the repo, creates `arkon_default`, fills `.env.docker`, starts Compose, logs in, sets embedding + LLM. **Success:** `/api/health` healthy, Settings tests pass.

### UC-2 Ingest a global document

Employee with `doc:create:own_dept` uploads a PDF + knowledge type. Worker extracts → MAP → REDUCE → plan. Editor approves. REFINE/VERIFY/COMMIT write pages. **Success:** source `ready`, pages searchable.

### UC-3 Ingest into a workspace

Workspace Editor uploads with `scope=project`. Pages get `scope_id` = that workspace. Non-members cannot read them in the portal or via MCP.

### UC-4 Ask the chatbot

User sends a message. Service embeds the query, pulls top wiki pages in scope, calls the chatbot (or fallback LLM) provider, stores the turn. Optional **Add to Wiki** creates a page from the thread.

### UC-5 Claude asks a question

Client calls `search_wiki` then `read_wiki_page`. Empty results mean “out of scope,” not “does not exist.”

### UC-6 Propose vs edit

Contributor calls `propose_wiki_edit` (or the portal editor in draft mode). Editor `approve_draft`. Direct `edit_wiki_page` / `PUT /wiki/pages/{slug}` requires write-all or workspace Editor+.

### UC-7 Switch embeddings

Admin picks a model with a new dimension. API enqueues `reembed_all_pages_task`. Search uses the new table when the job completes.

### UC-8 NotebookLM enrich

Admin connects a master token. User sends a source, generates a report, **Add to Wiki**. Worker runs `notebooklm_ingest_artifact_task`.

### UC-9 Skill contribution

Employee opens a contribution, edits files, submits. Reviewer approves → new `SkillVersion`, slug’s current version increments.

---

## 6. MRP pipeline (Luồng xử lý MRP Pipeline)

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

---

## 7. RBAC (Hệ thống quyền hạn)

Two realms. Implementation: `app/services/permission_engine.py`, `permissions.py`, `policy_engine.py`.

### 7.1 Global

Format `resource:action:own_dept|all`. Resources: `doc`, `wiki`, `skill`, plus `org:*` and `workspace:view:all`.

Default employee set (no custom role):

```text
doc:read:own_dept
doc:create:own_dept
wiki:read:own_dept
wiki:write:own_dept
skill:read:own_dept
```

`wiki:write:own_dept` = propose drafts. `wiki:write:all` = direct edit + review. Page rollback and page delete are system-admin operations (`wiki.py`).

`can_access_skill` requires `skill:{action}:own_dept` (or `:all`) before the department comparison. A Viewer cannot PATCH or DELETE a global skill.

`org:employees:manage` cannot write `Employee.role`, reset passwords, or toggle admin accounts. Those writes require `Employee.role == admin`. The last active admin cannot be demoted or deactivated.

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

---

## 8. MCP (Tích hợp MCP)

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

`ResolvedIdentity.allowed_knowledge_types` is unused in v0.1.0. Scope is `doc:read` (`all` / `own_dept` + global sources) plus workspace membership.

---

## Appendix — environment

Infrastructure settings are env-only (`app/config.py`). AI keys are not env — they are in `app_config`.

Must-set in production: `SECRET_KEY`, `DEFAULT_ADMIN_PASSWORD`, `POSTGRES_PASSWORD` + matching `DATABASE_URL`, `REDIS_PASSWORD`, `MINIO_SECRET_KEY`, `MINIO_PUBLIC_ENDPOINT`.

CORS default is empty (same-origin). `CORS_ORIGINS=*` raises unless `ARKON_ALLOW_CORS_WILDCARD=1`.

Templates: `.env.docker.example`, `.env.local.example`.
