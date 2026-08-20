# Architecture

How Arkon is put together. For the formal spec (models, APIs, use cases) see [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md).

Version 0.2.0 · [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

---

## In one picture

```text
                    Browser                         Claude Desktop / Code
                       │                                    │
                       │ HTTP :3119                         │ HTTP /mcp
                       ▼                                    ▼
                 ┌──────────────────────────────────────────────┐
                 │                   nginx                       │
                 │  /  → frontend    /api → api    /mcp → api   │
                 │  /arkon-files → minio                         │
                 └───────────────┬───────────────┬──────────────┘
                                 │               │
                    ┌────────────▼───┐     ┌─────▼──────┐
                    │  Next.js portal │     │  FastAPI   │
                    │  (frontend)     │     │  + FastMCP │
                    └─────────────────┘     └─────┬──────┘
                                                  │
                         ┌────────────────────────┼──────────────┐
                         ▼                        ▼              ▼
                   ┌──────────┐            ┌──────────┐    ┌─────────┐
                   │ Postgres │            │  Redis   │    │  MinIO  │
                   │ +vector  │            │  (arq)   │    │  files  │
                   └──────────┘            └────┬─────┘    └─────────┘
                                                │
                                    ┌───────────┴───────────┐
                                    ▼                       ▼
                             worker (docs)           worker_skills
```

Outbound traffic: your configured AI provider, and Google NotebookLM if you enable it. There is no telemetry.

---

## Processes

| Container | Role |
|---|---|
| `arkon_nginx` | Only published port (`127.0.0.1:3119`). Routes UI, API, MCP, files |
| `arkon_frontend` | Next.js 16 portal |
| `arkon_api` | FastAPI + MCP. Runs Alembic on start |
| `arkon_worker` | Document extract, MRP, captions, NotebookLM, re-embed |
| `arkon_worker_skills` | Skill ZIP ingest / delete |
| `arkon_postgres` | PostgreSQL 16 + pgvector |
| `arkon_redis` | arq queues + rate-limit counters |
| `arkon_minio` | Objects: sources, images, skill files |

Workers start only after the API health check passes.

---

## Networks

| Network | Who is on it |
|---|---|
| `frontend` | nginx, frontend, api |
| `backend` | api, workers, postgres, redis, minio, nginx |
| `arkon_default` (external) | nginx — so a LAN proxy can reach it by container name |

Create `arkon_default` once: `docker network create arkon_default`.

---

## Request paths

| Browser / client path | Upstream |
|---|---|
| `/` | Next.js |
| `/api/*` | FastAPI |
| `/mcp` | FastMCP (streamable HTTP, long timeout, no buffering) |
| `/arkon-files/*` | MinIO bucket |

Login is rate-limited twice: nginx `limit_req` on `/api/auth/login`, and Redis in the API.

nginx sets `X-Forwarded-For` to `$remote_addr` (overwrite, not append) so clients cannot spoof the IP the API rate-limiter sees.

---

## Application layers

```text
routers/     HTTP + auth dependencies
services/    business rules (wiki, RBAC, storage, chat, NotebookLM)
ai/          providers + MRP (map / merge / plan / write / verify)
mcp/         tools + resources
database/    SQLAlchemy models
worker.py    arq jobs
```

Routers stay thin. Permission checks go through `permission_engine` / `require_permission`.

---

## Data stores

**PostgreSQL** holds everything durable: people, sources, wiki pages, drafts, revisions, links, skills, chats, NotebookLM rows, audit, encrypted provider keys, and four embedding tables (768 / 1024 / 1536 / 3072 dimensions).

**Redis** is the job queue (`arq`) plus login / token rate-limit keys. It is not the source of truth for documents.

**MinIO** stores original files, extracted images, and skill package members. The wiki itself is markdown in Postgres.

---

## Knowledge flow

```text
Upload  →  extract text (+ optional vision)
        →  MAP (per-chunk LLM extract)
        →  REDUCE (dedup + reconcile + compilation plan)
        →  human approve plan (unless auto-approve)
        →  REFINE (write pages)
        →  VERIFY (citations, coverage, conflicts)
        →  COMMIT (atomic write + embed + index)
        →  searchable wiki
```

Resume is based on `source.pipeline_phase`. Details: [WIKI.md](WIKI.md).

---

## Auth surfaces

| Surface | Credential |
|---|---|
| Portal | JWT (`Authorization: Bearer`, 24 h, `iss=arkon`, `aud=arkon-api`) |
| Wiki `<img>` | Same JWT, also accepted as `?token=` (images cannot send headers) |
| MCP | Employee `ark_…` token |
| Export API | Same `ark_…` token |
| Claude Code gateway | `Authorization: Bearer` or `x-api-key` with the `ark_…` token |

Provider API keys live in `app_config`, encrypted with a Fernet key derived from `SECRET_KEY`.

---

## Frontend

Next.js App Router. Portal routes live under `frontend/src/app/(portal)/`.

| Path | Page |
|---|---|
| `/` | Home — workspace list |
| `/workspaces`, `/workspaces/[id]` | Workspaces (same list as `/` / `/projects`) |
| `/knowledge` | Documents |
| `/wiki`, `/wiki/[…slug]` | Wiki |
| `/wiki/graph` | Graph |
| `/chat` | Chatbot |
| `/skills` | Skills |
| `/notebooklm` | NotebookLM |
| `/departments` `/employees` `/roles` | Org admin |
| `/audit` `/settings` | System admin |
| `/profile` | Password + own MCP token |
| `/admin/skill-contributions` | Skill review queue |
| `/login` | Login |

The Docker image talks to the API on the Docker network (`INTERNAL_API_URL=http://api:5055`) and, when `NEXT_PUBLIC_API_URL` is empty, the browser uses same-origin `/api`.

---

## What this is not

- Not a public SaaS. You host it.
- Not Neo4j. The graph is wiki links in Postgres.
- Not a contacts CRM. That table was dropped.
- Not a chunk-only RAG store. Chunks exist as intermediate MRP extracts; people and agents read wiki pages.
