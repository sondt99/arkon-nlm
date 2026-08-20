# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

Repository: [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

---

## [Unreleased]

### Changed

- Portal chrome, operator docs, comments, and the generated Word export are English. Functional Vietnamese (compiler few-shots, chat intent regexes, unicode tests, skill-name `À-ỹ`) is unchanged (#140, #141, #142, #143).

### Fixed

- `org:employees:manage` can no longer promote anyone to admin, reset passwords, or deactivate the last admin (#6).
- Skill-contribution approval follows the target skill's departments, not the submitter's claimed `scope_type`. Approving no longer strips department ACLs unless an admin explicitly widens scope (#9).
- MCP and export tokens now get a real `allowed_knowledge_types` value. Wiki search, page reads, the index, RAG, and export chat all honor it; empty KT arrays fail closed (#11).


- Anthropic Settings models now use current IDs (`claude-opus-4-8`, `claude-sonnet-5`, `claude-haiku-4-5`). Sampling params are omitted on models that reject them (#1, #2).
- Viewers can no longer edit or delete global skills (`can_access_skill` now requires `skill:{action}:own_dept`) (#4).
- Workspace-private sources are no longer treated as global documents (#5).
- Deleting a department that still has employees returns 409; the FK is `ON DELETE RESTRICT` (#7).
- Chat conversations cannot be scoped to a workspace the caller is not in; membership is re-checked on every message (#32).
- Add-to-Wiki inherits the conversation scope and requires the same write rights as the wiki editor (#8).
- NotebookLM ingest requires `doc:create` and writes an audit row (#10).
- `get_source_pages` rejects unbounded page ranges (#12).
- Saving provider settings no longer wipes stored API keys (#14).
- Opening a non-editable skill contribution no longer hard-reloads the tab (#15).
- The department Access button that called missing `/api/scopes/...` endpoints is removed (#16).

## [0.1.0] — 2026-08-18

First tagged release of **arkon-nlm**. This is the product snapshot published at [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm). It is based on [nduckmink/arkon](https://github.com/nduckmink/arkon) and adds NotebookLM, the Claude Code gateway, source-aware wiki compilation, and a hardened Docker deploy.

### Added

- **Wiki compiler (MRP)** — Map → Reduce → Plan → Refine → Verify → Commit. Documents become interlinked markdown pages with citations, not raw RAG chunks.
- **Human plan review** before pages are written, with optional `MRP_AUTO_APPROVE_PLAN`.
- **Source-aware knowledge** — every claim can be traced to a source excerpt; security knowledge types keep commands, payloads, and CVE/ATT&CK IDs verbatim.
- **Wiki portal** — page tree, markdown reader, backlinks, full-text + semantic search, knowledge graph, revisions, rollback, draft propose/review/approve.
- **Workspaces** — project-scoped sources, wiki, and membership roles (Viewer, Contributor, Editor, Admin).
- **Dual-realm RBAC** — department permissions (`resource:action:scope`) plus independent workspace membership. System `admin` bypasses both.
- **MCP server** at `/mcp` — 16 tools (wiki search/read, source drill-down, draft workflow). Tokens are scoped server-side.
- **Claude Code skills** in `skills/` — `arkon-query`, `arkon-edit`, `arkon-review`.
- **In-app chatbot** — RAG over the wiki, conversation history, edit-and-regenerate, **Add to Wiki**.
- **Claude Code gateway** — Anthropic-compatible `POST /api/claude-gateway/v1/messages` authenticated with the same `ark_` token.
- **Export API** — `POST /api/export/v1/chat` and `GET /api/export/v1/search` for external callers.
- **AI Skills** — versioned ZIP packages, department/workspace scope, contribution and review workflow, built-in system skills.
- **NotebookLM** — cookie import or master-token headless auth, notebooks, artifact generation (audio, video, quiz, flashcards, report, slides, infographic, data table), chat, import back into the wiki, 30-minute session keepalive.
- **Multi-provider AI settings** — Google, OpenAI, Anthropic, Ollama, 9Router. Separate keys for embedding, LLM, vision, chatbot, and gateway. Embedding catalog with 768 / 1024 / 1536 / 3072-d tables and a re-embed job.
- **Audit log** for administrative and knowledge actions.
- **Docker stack** — postgres (pgvector), redis, minio, api, worker, worker_skills, frontend, nginx. Single published port `3119`. Migrations run in `entrypoint.sh`.
- **Security hardening** — default-secret rejection, login rate limits, nginx IP overwrite, path-safe MinIO keys, encrypted provider keys, CORS default same-origin.

### Changed

- Public ingress is **nginx on port 3119**. The API (`5055`), Postgres, Redis, and MinIO are not published to the host.
- MCP URL through Docker is `http://localhost:3119/mcp` (not `:5055`).
- Contacts directory was removed. Use employees + wiki pages for people.
- Neo4j is not used. The knowledge graph is PostgreSQL + wiki links.

### Docs

- New root [README](README.md), [CONTRIBUTING](CONTRIBUTING.md), and this changelog.
- All guides under [`docs/`](docs/README.md) rewritten for v0.1.0.

[0.1.0]: https://github.com/sondt99/arkon-nlm/releases/tag/v0.1.0
