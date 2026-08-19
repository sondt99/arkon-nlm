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
| `GET` | `/api/health` | no | `api` / `database` / `worker`. **200** when all three are reachable, **503** when any is not — so an orchestrator can use the status code alone. Does not create the MinIO bucket as a side effect. |
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
| `GET` | `/api/settings/embeddings/jobs/{job_id}` |
| `POST` | `/api/settings/embeddings/jobs/{job_id}/cancel` |

There is **no** route that lists embedding jobs. `{job_id}` is typed `uuid.UUID`, so the bare
`/api/settings/embeddings/jobs` path 404s. Get the current job from
`/api/settings/embeddings/status`, which is what the portal uses.

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

Uploads are `multipart/form-data`. See [Request size limits](#request-size-limits).

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
| `GET/PUT/DELETE` | `/api/skill-contributions/{id}/files` |
| `GET` | `/api/skill-contributions/{id}/files/content` |
| `POST` | `/api/skill-contributions/{id}/upload` |
| `POST` | `/api/skill-contributions/{id}/rename` |
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

These proxy one shared Google session, so they are **owner-scoped**. Every route taking an
`{nlm_id}` accepts it only from a system admin, the employee who created it through
`POST /api/notebooklm/nlm/notebooks`, or the employee who created the matching Arkon notebook row.
Anything else is **404 `"Notebook not found"`** — deliberately not 403, which would confirm the id
names a real notebook and allow enumeration. `GET /api/notebooklm/nlm/notebooks` returns only the
caller's own notebooks (admins see all). Notebooks with no recorded creator — anything predating
this boundary, or created directly in the Google account — are reachable by admins only. The two
ingest routes also require `doc:create`.

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

## Request size limits

Every response below is `{ "detail": "…" }`.

| Limit | Value | Applies to | Code |
|---|---|---|---|
| `client_max_body_size` (nginx) | 260 MB | `/api/` at the edge | 413 (nginx HTML) |
| `MAX_REQUEST_BODY_MB` | 256 MB | **every** request, including `/mcp` and non-upload routes | 413 |
| `MAX_UPLOAD_MB` | 100 MB | `POST /api/sources/upload`, `/api/projects/{id}/sources/upload`, `/api/skill-contributions/{id}/upload`, `/api/notebooklm/nlm/notebooks/{nlm_id}/sources/upload` | 413 |
| `MAX_ZIP_UPLOAD_MB` | 200 MB | `POST /api/sources/upload-zip`; `/api/skills/upload`, `/{slug}/reupload`, `/inspect-zip` (per file) | 413 |
| `MAX_CONTRIBUTION_TEXT_KB` | 1024 KB | `PUT /api/skill-contributions/{id}/files` — UTF-8 bytes of `content` | 413 |
| `MAX_CONTRIBUTION_TOTAL_MB` | 25 MB | cumulative bytes under one contribution | 413 |
| `MAX_CONTRIBUTION_FILES` | 200 | cumulative file count under one contribution | 400 |

`MAX_REQUEST_BODY_MB` is enforced by middleware ahead of every route, so a body over it is
refused before the handler runs — including on endpoints that accept no upload. It checks the
`Content-Length` header *and* the bytes actually delivered, and closes the connection.

The cumulative contribution budget counts what is already stored, so an under-cap write can still
be refused. Overwriting an existing path credits back the old object's bytes.

`POST /api/sources/upload-zip` caps the **decompressed** archive: `MAX_ZIP_ENTRIES` (50) and
`MAX_ZIP_TOTAL_MB` (500) are 422, and `MAX_ZIP_MEMBER_MB` (50) skips the oversized entry and
reports it in `skipped` rather than failing the request. A corrupt or non-ZIP payload is 422
(`"Invalid or corrupted zip file."`); a filename not ending in `.zip` is 400. The skill ZIP
endpoints use a different implementation with its own caps (10 MB uncompressed, 100 files, 400).

Other input caps: `POST /api/wiki/images/resolve` takes at most 100 ids (400), and a draft's
`content_md` is capped at 50,000 characters (422).

---

## Status codes (typical)

| Code | Meaning |
|---|---|
| 401 | Missing / bad JWT or `ark_` token |
| 403 | Authenticated but permission or workspace role denied |
| 404 | Unknown id / slug (or hidden by scope) |
| 409 | Conflict (e.g. last workspace admin) |
| 413 | Request body or upload over a size cap — see above |
| 422 | Validation, and every ZIP-extraction failure |
| 429 | Login or token rate limit |
| 502 | Upstream (NotebookLM / AI provider) did not confirm the operation |
