# API reference

Base URL:

| How you run it | Base |
|---|---|
| Docker + nginx | `http://localhost:3119/api` |
| Host-run API | `http://localhost:5055/api` |

Interactive docs when the API port is reachable: `/docs` (Swagger). Through nginx that is **not** mounted; use `docker exec` or publish 5055 only on a trusted network.

Unless noted, send:

```http
Authorization: Bearer <jwt>
Content-Type: application/json
```

List endpoints that paginate may also return `X-Total-Count`.

MCP is **not** under `/api`. It is `POST /mcp` with an `ark_` token. See [MCP.md](MCP.md).

---

## Health

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET` | `/health` | no | DB + Redis + MinIO. Only on the API container |
| `GET` | `/api/health` | no | `api` / `database` / `worker` |
| `GET` | `/` | no | Name, version, MCP path |

---

## Auth

| Method | Path | Auth |
|---|---|---|
| `POST` | `/api/auth/login` | no (rate-limited) |
| `GET` | `/api/auth/me` | JWT |
| `POST` | `/api/auth/change-password` | JWT |
| `GET` | `/api/auth/status` | no (`{ "auth_required": true }`) |

Login body: `{ "email", "password" }`. Response includes `access_token` and `user`.

---

## Settings

Admin (`org:settings:*` or system admin).

| Method | Path |
|---|---|
| `GET` | `/api/dashboard/stats` |
| `GET` / `PUT` | `/api/settings` |
| `GET` | `/api/settings/providers` |
| `POST` | `/api/settings/fetch-models` |
| `POST` | `/api/settings/test-providers` |
| `POST` | `/api/settings/test-embedding` |
| `POST` | `/api/settings/test-llm` |
| `POST` | `/api/settings/test-vision` |
| `POST` | `/api/settings/test-chatbot` |
| `POST` | `/api/settings/test-gateway` |
| `GET` | `/api/settings/embeddings/catalog` |
| `GET` | `/api/settings/embeddings/status` |
| `POST` | `/api/settings/embeddings/switch` |
| `GET` / `POST` | `/api/settings/embeddings/jobs` (and job-detail routes) |

---

## People and roles

| Method | Path |
|---|---|
| `GET/POST` | `/api/departments` |
| `PUT/DELETE` | `/api/departments/{id}` |
| `GET/POST` | `/api/employees` |
| `PUT/DELETE` | `/api/employees/{id}` |
| `PATCH` | `/api/employees/{id}/toggle` |
| `POST/DELETE` | `/api/employees/{id}/token` |
| `POST/DELETE` | `/api/my/mcp-token` |
| `GET` | `/api/my/mcp-token/status` |
| `GET` | `/api/roles` |
| `GET` | `/api/roles/permissions` |
| `POST` | `/api/roles` |
| `PUT/DELETE` | `/api/roles/{id}` |

---

## Knowledge types

| Method | Path |
|---|---|
| `GET/POST` | `/api/knowledge-types` |
| `PUT/DELETE` | `/api/knowledge-types/{id}` |
| `PATCH` | `/api/knowledge-types/reorder` |

---

## Sources

| Method | Path |
|---|---|
| `GET` | `/api/sources` |
| `GET` | `/api/sources/{id}` |
| `GET` | `/api/sources/{id}/wiki-pages` |
| `GET` | `/api/sources/{id}/progress` |
| `GET` | `/api/sources/{id}/plan` |
| `GET` | `/api/sources/{id}/knowledge-impact` |
| `POST` | `/api/sources/upload` |
| `POST` | `/api/sources/upload-zip` |
| `POST` | `/api/sources/url` |
| `PATCH` | `/api/sources/{id}` |
| `POST` | `/api/sources/{id}/retry` |
| `POST` | `/api/sources/{id}/plan/approve` |
| `POST` | `/api/sources/{id}/plan/reject` |
| `DELETE` | `/api/sources/{id}` |

Uploads are `multipart/form-data`.

---

## Wiki

| Method | Path |
|---|---|
| `GET` | `/api/wiki/pages` |
| `GET` | `/api/wiki/pages/{slug}` |
| `PUT` | `/api/wiki/pages/{slug}` |
| `DELETE` | `/api/wiki/pages/{slug}` |
| `GET` | `/api/wiki/stats` |
| `GET` | `/api/wiki/tree` |
| `GET` | `/api/wiki/search` |
| `GET` | `/api/wiki/index` |
| `GET` | `/api/wiki/log` |
| `GET` | `/api/wiki/graph` |
| `GET` | `/api/wiki/orphaned` |
| `GET` | `/api/wiki/pages/{slug}/revisions` |
| `POST` | `/api/wiki/pages/{slug}/revisions/{version}/rollback` |
| `POST` | `/api/wiki/images/resolve` |
| `GET` | `/api/wiki/images/{image_id}` |

`{slug}` is a path (e.g. `concept/fire-evacuation`).

### Drafts

| Method | Path |
|---|---|
| `POST` | `/api/wiki/pages/{slug}/drafts` |
| `GET` | `/api/wiki/drafts` |
| `GET` | `/api/wiki/pages/{slug}/drafts` |
| `GET` | `/api/wiki/drafts/{id}` |
| `POST` | `/api/wiki/drafts/{id}/approve` |
| `POST` | `/api/wiki/drafts/{id}/reject` |

---

## Workspaces (`/api/projects`)

| Method | Path |
|---|---|
| `GET/POST` | `/api/projects` |
| `PUT/DELETE` | `/api/projects/{id}` |
| `GET/POST` | `/api/projects/{id}/members` |
| `PATCH/DELETE` | `/api/projects/{id}/members/{employee_id}` |
| `GET/POST` | `/api/projects/{id}/sources` |
| `DELETE` | `/api/projects/{id}/sources/{source_id}` |
| `POST` | `/api/projects/{id}/sources/upload` |
| `POST` | `/api/projects/{id}/sources/url` |
| `GET` | `/api/projects/{id}/wiki` |
| `GET` | `/api/projects/{id}/wiki/index` |
| `GET` | `/api/projects/{id}/wiki/graph` |

---

## Chat

| Method | Path |
|---|---|
| `GET/POST` | `/api/chat/conversations` |
| `DELETE` | `/api/chat/conversations` (bulk) |
| `PATCH/DELETE` | `/api/chat/conversations/{id}` |
| `GET/POST` | `/api/chat/conversations/{id}/messages` |
| `PATCH` | `/api/chat/conversations/{id}/messages/{mid}/edit` |
| `POST` | `/api/chat/conversations/{id}/to-wiki` |

---

## Skills

| Method | Path |
|---|---|
| `GET` | `/api/skills` |
| `POST` | `/api/skills/upload` |
| `POST` | `/api/skills/inspect-zip` |
| `GET` | `/api/skills/{slug}` |
| `PATCH/DELETE` | `/api/skills/{slug}` |
| `GET` | `/api/skills/{slug}/versions` |
| `POST` | `/api/skills/{slug}/set-latest` |
| `POST` | `/api/skills/{slug}/reupload` |
| `GET` | `/api/skills/{id}/files` |
| `GET` | `/api/skills/{id}/files/content` |

### Contributions

| Method | Path |
|---|---|
| `GET` | `/api/skill-contributions/check` |
| `POST` | `/api/skill-contributions` |
| `GET` | `/api/skill-contributions` |
| `GET` | `/api/admin/skill-contributions` |
| `GET/DELETE` | `/api/skill-contributions/{id}` |
| `GET` | `/api/skill-contributions/{id}/files` |
| `GET/PUT` | `/api/skill-contributions/{id}/files/content` (and file write/upload/rename/delete) |
| `POST` | `/api/skill-contributions/{id}/submit` |
| `POST` | `/api/skill-contributions/{id}/approve` |
| `POST` | `/api/skill-contributions/{id}/reject` |
| `GET` | `/api/skill-contributions/{id}/diff-status` |

---

## NotebookLM

Auth session (admin):

| Method | Path |
|---|---|
| `GET` | `/api/notebooklm/auth/status` |
| `POST` | `/api/notebooklm/auth/refresh` |
| `POST` | `/api/notebooklm/auth/import-cookies` |
| `POST` | `/api/notebooklm/auth/master-token` |
| `DELETE` | `/api/notebooklm/auth/session` |

Arkon rows:

| Method | Path |
|---|---|
| `GET/POST` | `/api/notebooklm/notebooks` |
| `GET/DELETE` | `/api/notebooklm/notebooks/{id}` |
| `GET/POST` | `/api/notebooklm/notebooks/{id}/artifacts` |
| `GET` | `/api/notebooklm/artifacts/{id}` |
| `POST` | `/api/notebooklm/artifacts/{id}/ingest` |
| `GET` | `/api/notebooklm/artifacts/{id}/download` |

Live NotebookLM (`/api/notebooklm/nlm/...`): list/create/delete notebooks, sources, upload, generate, preview, download, chat, ingest. See `app/routers/notebooklm.py`.

---

## Export API (`ark_` token)

For automation that should not use a user JWT. Disabled until an admin turns **Export API** on in Settings (`export_api_enabled`).

| Method | Path |
|---|---|
| `POST` | `/api/export/v1/chat` |
| `GET` | `/api/export/v1/search` |

---

## Claude Code gateway (`ark_` token)

Disabled until an admin turns **Claude Code Gateway** on in Settings (`claude_gateway_enabled`).

Anthropic Messages shape. Errors are `{ "type": "error", "error": { "type", "message" } }`.

| Method | Path |
|---|---|
| `POST` | `/api/claude-gateway/v1/messages` |
| `POST` | `/api/claude-gateway/v1/messages/count_tokens` |

Auth: `Authorization: Bearer ark_…` **or** `x-api-key: ark_…`.

v0.1.0 is passthrough (no RAG injection). Streaming is synthesized from one complete provider response.

---

## Notes and audit

| Method | Path |
|---|---|
| `GET/POST` | `/api/notes` |
| `DELETE` | `/api/notes/{id}` |
| `GET` | `/api/audit/log` |

---

## Status codes (typical)

| Code | Meaning |
|---|---|
| 401 | Missing / bad JWT or `ark_` token |
| 403 | Authenticated but permission or workspace role denied |
| 404 | Unknown id / slug (or hidden by scope) |
| 409 | Conflict (e.g. last workspace admin) |
| 422 | Validation |
| 429 | Login or token rate limit |
