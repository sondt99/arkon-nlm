# Project status — v0.1.0

This page is a snapshot of what **arkon-nlm** contains at the first GitHub release. Feature-level history lives in [CHANGELOG.md](../CHANGELOG.md). How to run it lives in [QUICKSTART.md](QUICKSTART.md).

**Repo:** [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)  
**Lineage:** fork and extension of [nduckmink/arkon](https://github.com/nduckmink/arkon)

---

## What this release is

A complete, self-hosted knowledge hub:

- Documents in → MRP compiler → wiki out
- Portal for people, MCP for Claude
- Department RBAC + workspaces
- Optional NotebookLM and a Claude Code gateway

It is **not** a hosted SaaS and **not** a drop-in public demo. You bring Docker and an AI key.

---

## Quality bar at tag

| Area | State |
|---|---|
| App / frontend version | `0.1.0` |
| Schema | Alembic through `033` |
| Ingress | nginx on `:3119`; API/DB/Redis/MinIO unpublished |
| Tests | `tests/` (chat, gateway, MRP, wiki, paths, export, embeddings) + frontend Playwright |
| Secrets | Default `SECRET_KEY` / admin / MinIO factory pair refused at boot |
| Docs | Rewritten for this repo and this topology |

---

## Implemented capabilities

| Capability | Notes |
|---|---|
| MRP wiki compiler | Map → Reduce → Plan → Refine → Verify → Commit, resumable |
| Plan review | Human gate, optional auto-approve |
| Source-aware pages | Citations + security artifact preservation |
| Portal wiki | Tree, search, graph, drafts, revisions |
| Workspaces | Isolated wiki + files + members |
| RBAC | Dual realm, custom roles |
| MCP | 16 tools, token scope, `/mcp` on nginx |
| Chatbot | RAG + history + Add to Wiki |
| Skills | ZIP versions + contribution review |
| NotebookLM | Cookies or master token, artifacts, ingest |
| Gateway | Anthropic-compatible messages |
| Export API | Chat + search with `ark_` token |
| Multi-provider AI | Google / OpenAI / Anthropic / Ollama / 9Router |
| Audit | Admin-readable log |

---

## Deliberately not in v0.1.0

- Arkon CLI for one-command employee setup
- In-app notifications for draft review
- Usage analytics dashboard
- One NotebookLM session per user
- Neo4j, contacts CRM

---

## How to verify a build

```bash
docker network create arkon_default
cp .env.docker.example .env.docker   # set real secrets
docker compose --env-file .env.docker up -d --build
curl -s http://localhost:3119/api/health
```

Then: login → Settings tests → upload one file → approve plan → open Wiki → mint MCP token → `search_wiki`.

---

## Where to read next

| Question | Doc |
|---|---|
| How do I deploy? | [SETUP.md](SETUP.md) |
| How is it built? | [ARCHITECTURE.md](ARCHITECTURE.md) |
| What is the contract? | [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md) |
| What changed? | [../CHANGELOG.md](../CHANGELOG.md) |
