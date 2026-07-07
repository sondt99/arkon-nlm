# API Reference

Base URL: `http://localhost:5055`  
Interactive docs (Swagger): `http://localhost:5055/docs`

---

## Authentication

Tất cả API (trừ `/api/auth/login` và `/health`) yêu cầu JWT token:

```
Authorization: Bearer <jwt_token>
```

Lấy token:

```bash
curl -X POST http://localhost:5055/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@company.com", "password": "password"}'

# Response:
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": { "id": "...", "name": "Admin", "role": "admin" }
}
```

---

## Auth

### POST /api/auth/login
Đăng nhập, nhận JWT token.

**Body:**
```json
{ "email": "string", "password": "string" }
```

**Response 200:**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "name": "string",
    "email": "string",
    "role": "admin | employee"
  }
}
```

### GET /api/auth/me
Thông tin user hiện tại.

**Response 200:**
```json
{
  "id": "uuid",
  "name": "string",
  "email": "string",
  "role": "admin | employee",
  "department": { "id": "uuid", "name": "string" },
  "custom_role": { "id": "uuid", "name": "string" }
}
```

---

## Sources (Tài liệu)

### GET /api/sources
Danh sách tài liệu (filtered theo permission).

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `status` | string | pending / processing / plan_review / ready / error |
| `knowledge_type_id` | uuid | Filter theo knowledge type |
| `scope_type` | string | global / project |
| `limit` | int | Mặc định 50 |
| `offset` | int | Pagination |

**Response 200:** `Source[]`

### POST /api/sources/upload
Upload tài liệu từ file.

**Content-Type:** `multipart/form-data`

| Field | Type | Bắt buộc | Mô tả |
|---|---|---|---|
| `file` | File | Có | PDF, DOCX, TXT, MD |
| `knowledge_type_id` | uuid | Không | |
| `scope_type` | string | Không | global (mặc định) / project |
| `scope_id` | uuid | Khi scope=project | Workspace ID |
| `department_ids` | uuid[] | Không | Visibility filter |
| `title` | string | Không | Override auto-detected title |

**Response 201:** `Source`

### POST /api/sources/url
Upload từ URL.

**Body:**
```json
{
  "url": "https://...",
  "knowledge_type_id": "uuid",
  "scope_type": "global",
  "title": "optional override"
}
```

### GET /api/sources/{id}
Chi tiết một tài liệu.

**Response 200:**
```json
{
  "id": "uuid",
  "title": "string",
  "status": "string",
  "progress": 75,
  "progress_message": "Running MAP phase...",
  "pipeline_phase": "map | reduce | plan_review | refine | verify | commit",
  "pipeline_strategy": "single_pass | standard | hierarchical",
  "knowledge_type": { "id": "uuid", "name": "string" },
  "outline_json": [],
  "created_at": "ISO8601"
}
```

### DELETE /api/sources/{id}
Xóa tài liệu. Yêu cầu `doc:delete:own_dept` hoặc `doc:delete:all`.

Từ v2, endpoint xóa contribution thuộc source, dựng lại các wiki page dùng
chung từ source còn lại, sau đó refresh wikilinks và embeddings.

**Response 200:**
```json
{
  "deleted": true,
  "knowledge_impact": {
    "pages_deleted": 2,
    "pages_rebuilt": 3,
    "legacy_pages_detached": 0,
    "rebuilt_page_ids": ["uuid"]
  }
}
```

### GET /api/sources/{id}/knowledge-impact
Xem trước tác động lên Knowledge Base trước khi xóa source.

**Response 200:**
```json
{
  "source_id": "uuid",
  "source_title": "Tài liệu B",
  "affected_pages": 2,
  "pages": [
    {
      "slug": "concept/example",
      "title": "Example",
      "contribution_summary": "Phần tri thức do tài liệu B đóng góp",
      "provenance_complete": true,
      "action": "rebuild_page",
      "remaining_sources": 1
    }
  ]
}
```

`action` gồm `delete_page`, `rebuild_page` hoặc `detach_legacy`.

### GET /api/sources/{id}/plan
Xem Compilation Plan (khi source ở trạng thái `plan_ready`).

**Response 200:**
```json
{
  "id": "uuid",
  "source_id": "uuid",
  "plan": {
    "pages": [
      {
        "slug": "concept/example",
        "title": "string",
        "action": "CREATE | UPDATE",
        "entity_names": ["..."],
        "priority": 1
      }
    ]
  },
  "status": "pending_review"
}
```

### POST /api/sources/{id}/plan/approve
Approve compilation plan và bắt đầu REFINE phase.

### POST /api/sources/{id}/plan/reject
Reject compilation plan với lý do.

**Body:** `{ "note": "string" }`

---

## Wiki Pages

### GET /api/wiki/pages
Danh sách wiki pages.

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `q` | string | Full-text search |
| `knowledge_type_id` | uuid | Filter |
| `scope_type` | string | global / project |
| `scope_id` | uuid | Workspace ID |
| `limit` | int | Mặc định 20 |

### GET /api/wiki/pages/{slug}
Đọc một wiki page theo slug.

**Response 200:**
```json
{
  "id": "uuid",
  "slug": "concept/example",
  "title": "string",
  "content_md": "# Markdown content...",
  "version": 3,
  "knowledge_type_slugs": ["technical"],
  "source_ids": ["uuid"],
  "provenance_complete": true,
  "source_documents": [
    { "id": "uuid", "title": "Architecture.pdf", "status": "ready" }
  ],
  "backlinks": ["topic/related"],
  "outlinks": ["concept/another-page"],
  "created_at": "ISO8601",
  "updated_at": "ISO8601"
}
```

### PUT /api/wiki/pages/{slug}
Chỉnh sửa trực tiếp wiki page. Yêu cầu `wiki:write:all` hoặc workspace Editor+.

**Body:**
```json
{
  "content_md": "# Updated content...",
  "change_note": "optional description"
}
```

### DELETE /api/wiki/pages/{slug}
Xóa wiki page. Yêu cầu `wiki:delete:all` hoặc admin.

### GET /api/wiki/search
Semantic search (embedding + cosine similarity) over wiki pages, scoped identically to `GET /api/wiki/pages` (same permission/knowledge-type/workspace filtering — no separate scope param needed).

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `q` | string | Query text (required, non-empty) |
| `top_k` | int | Default 20, clamped 1-50 |

**Response 200:** bare array, not wrapped in an object.
```json
[
  {
    "slug": "string",
    "title": "string",
    "page_type": "string",
    "summary": "string",
    "scope_type": "global",
    "scope_id": null,
    "score": 0.92
  }
]
```
**Errors:** `422` empty `q` · `502` embedding provider unavailable/search failed · `503` no active embedding model configured.

### GET /api/wiki/graph
Knowledge graph nodes và edges.

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `scope_type` | string | global / project |
| `scope_id` | uuid | Workspace ID |
| `center_slug` | string | Lấy neighborhood graph xung quanh 1 slug |
| `depth` | int | Depth khi dùng center_slug (mặc định 2) |

---

## Wiki Drafts

### GET /api/wiki/drafts
Danh sách drafts đang pending (cho editors).

### POST /api/wiki/pages/{slug}/drafts
Đề xuất chỉnh sửa (tạo draft). Yêu cầu `wiki:write:own_dept`.

**Body:**
```json
{
  "content_md": "# Proposed content...",
  "note": "Lý do chỉnh sửa"
}
```

### GET /api/wiki/drafts/{id}
Đọc chi tiết draft (kèm current page content để compare).

### POST /api/wiki/drafts/{id}/approve
Approve draft. Yêu cầu `wiki:write:all` hoặc workspace Editor+.

**Body:** `{ "reviewer_note": "optional", "edited_content_md": "optional override" }`

### POST /api/wiki/drafts/{id}/reject
Reject draft với lý do bắt buộc.

**Body:** `{ "reviewer_note": "Lý do từ chối" }`

### GET /api/wiki/pages/{slug}/revisions
Lịch sử phiên bản của wiki page.

**Response 200:** `WikiPageRevision[]`

---

## Wiki Images

### POST /api/wiki/images/resolve
Resolve danh sách image UUIDs thành proxy URLs.

**Body:**
```json
{ "ids": ["uuid1", "uuid2"] }
```

**Response 200:**
```json
{
  "resolved": { "uuid1": "/api/wiki/images/uuid1" },
  "denied": ["uuid2"]
}
```

### GET /api/wiki/images/{id}
Stream image từ MinIO (sau khi xác thực).

Chấp nhận JWT qua header HOẶC `?token=<jwt>` query param.

---

## Knowledge Types

### GET /api/knowledge-types
Danh sách tất cả knowledge types.

**Response 200:**
```json
[
  {
    "id": "uuid",
    "slug": "pentest",
    "name": "Pentest",
    "color": "#ef4444",
    "description": "Tài liệu pentest và kỹ thuật tấn công",
    "extraction_hints": "KEEP lệnh platform-specific...",
    "sort_order": 1,
    "source_count": 12
  }
]
```

### POST /api/knowledge-types
Tạo knowledge type mới. Yêu cầu `documents.create`.

**Body:**
```json
{
  "name": "string",
  "slug": "string (tùy chọn — tự generate từ name nếu bỏ trống)",
  "color": "#6366f1",
  "description": "string (tùy chọn) — nhãn ngắn hiển thị UI",
  "extraction_hints": "string (tùy chọn) — Markdown instructions cho LLM pipeline"
}
```

> **`extraction_hints`**: Hướng dẫn chi tiết được inject vào LLM prompt khi compile tài liệu. Ghi đè rule chung (keep/drop) của pipeline cho domain này. Để `null` với domain thông thường — chỉ cần với domain chuyên biệt (pentest, y tế, pháp lý).

### PUT /api/knowledge-types/{id}
Cập nhật knowledge type. Yêu cầu `documents.edit`.

**Body:** Giống POST — tất cả fields đều có thể update, kể cả `extraction_hints`.

Để xóa `extraction_hints` (reset về mặc định): truyền `"extraction_hints": null`.

### DELETE /api/knowledge-types/{id}
Xóa knowledge type. Yêu cầu `documents.delete`.

Sources đang dùng type này sẽ có `knowledge_type_id = NULL` (không bị xóa).

---

## RBAC — Employees

### GET /api/employees
Danh sách nhân viên. Yêu cầu `org:employees:read`.

### POST /api/employees
Tạo nhân viên mới. Yêu cầu `org:employees:manage`.

**Body:**
```json
{
  "name": "string",
  "email": "string",
  "password": "string",
  "department_id": "uuid",
  "custom_role_id": "uuid",
  "role": "employee"
}
```

### GET /api/employees/{id}
Chi tiết nhân viên.

### PUT /api/employees/{id}
Cập nhật nhân viên.

### POST /api/employees/{id}/deactivate
Vô hiệu hóa tài khoản.

### POST /api/employees/{id}/mcp-token
Tạo MCP token.

**Response 200:**
```json
{ "token": "ark_xxxx...", "created_at": "ISO8601" }
```

### DELETE /api/employees/{id}/mcp-token
Thu hồi MCP token.

---

## RBAC — Departments

### GET /api/departments
Danh sách phòng ban.

### POST /api/departments
Tạo phòng ban. Yêu cầu `org:departments:manage`.

**Body:** `{ "name": "string", "description": "string" }`

### PUT /api/departments/{id}
Cập nhật phòng ban.

### DELETE /api/departments/{id}
Xóa phòng ban.

---

## RBAC — Roles

### GET /api/roles
Danh sách custom roles.

### POST /api/roles
Tạo role mới. Yêu cầu `org:roles:manage`.

**Body:**
```json
{
  "name": "string",
  "permissions": [
    "doc:read:own_dept",
    "wiki:write:all",
    "org:departments:read"
  ]
}
```

### PUT /api/roles/{id}
Cập nhật role và permissions.

### DELETE /api/roles/{id}
Xóa role.

---

## Projects (Workspaces)

### GET /api/projects
Danh sách workspaces accessible.

### POST /api/projects
Tạo workspace. Yêu cầu admin.

**Body:** `{ "name": "string", "description": "string" }`

### GET /api/projects/{id}
Chi tiết workspace (kèm members, sources, wiki pages).

### PUT /api/projects/{id}
Cập nhật workspace metadata.

### DELETE /api/projects/{id}
Xóa workspace. Yêu cầu admin.

### GET /api/projects/{id}/members
Danh sách members.

### POST /api/projects/{id}/members
Thêm member vào workspace.

**Body:**
```json
{
  "employee_id": "uuid",
  "workspace_role": "viewer | contributor | editor | admin"
}
```

### PUT /api/projects/{id}/members/{employee_id}
Thay đổi workspace role.

### DELETE /api/projects/{id}/members/{employee_id}
Xóa member khỏi workspace.

### GET /api/projects/{id}/wiki/graph
Knowledge graph của workspace.

---

## Skills

### GET /api/skills
Danh sách skills accessible.

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `status` | string | active / deprecated / archived |
| `department_id` | uuid | Filter theo phòng ban |

### POST /api/skills/upload
Upload skill package mới.

**Content-Type:** `multipart/form-data`

| Field | Bắt buộc | Mô tả |
|---|---|---|
| `file` | Có | .zip skill package |
| `name` | Có | Tên skill |
| `slug` | Có | URL-safe identifier |
| `description` | Có | Mô tả |
| `department_ids` | Không | Visibility |

### GET /api/skills/{id}
Chi tiết skill.

### PATCH /api/skills/{id}
Cập nhật metadata skill.

### DELETE /api/skills
Xóa skills (bulk). Body: `{ "ids": ["uuid1"] }`

### GET /api/skills/{id}/versions
Lịch sử versions.

---

## Skill Contributions

### GET /api/skill-contributions
Danh sách contributions (pending review).

### POST /api/skill-contributions/upload
Đề xuất skill mới hoặc update.

### PUT /api/skill-contributions/{id}/approve
Approve contribution.

### PUT /api/skill-contributions/{id}/reject
Reject contribution. Body: `{ "note": "string" }`

---

## Admin — Settings

### GET /api/settings
Đọc AI provider config. Yêu cầu `org:settings:manage`.

**Response 200:**
```json
{
  "llm_provider": "google",
  "llm_model_id": "gemini-2.5-pro",
  "llm_api_key": "•••••",
  "llm_base_url": "",
  "vision_provider": "google",
  "vision_model_id": "gemini-2.0-flash",
  "vision_api_key": "•••••",
  "vision_base_url": "",
  "embedding_provider": "google",
  "embedding_model_id": "text-embedding-004",
  "embedding_api_key__google": "•••••"
}
```

### PUT /api/settings
Cập nhật settings.

**Body:**
```json
{
  "settings": {
    "llm_provider": "openai",
    "llm_model_id": "gpt-4o",
    "llm_api_key": "sk-..."
  }
}
```

### POST /api/settings/test-llm
Test kết nối LLM provider.

**Response 200:**
```json
{ "success": true, "message": "Connected to gpt-4o successfully" }
```

---

## Admin — Embeddings

### POST /api/admin/embeddings/reembed-all
Trigger re-embedding tất cả wiki pages (dùng khi đổi embedding model).

**Response 202:**
```json
{ "job_id": "string", "message": "Re-embedding job enqueued" }
```

---

## Audit

### GET /api/audit
Audit log. Yêu cầu `org:audit:read`.

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `from` | date | Start date (ISO8601) |
| `to` | date | End date |
| `actor_id` | uuid | Nhân viên thực hiện |
| `resource_type` | string | source / wiki_page / employee / ... |
| `action` | string | create / update / delete / approve / ... |
| `limit` | int | Mặc định 100 |
| `offset` | int | Pagination |

---

## Export API (External Integrations)

REST endpoints for external tools that aren't MCP clients (n8n, Zapier, internal scripts, other AI platforms) to chat with Victor/Ashley or query the wiki directly. Authenticates with the same bearer token used for MCP — not the JWT used by `## Authentication` above:

```
Authorization: Bearer <mcp_token>
```

Get a token via `POST /api/my/mcp-token` (self-service, while logged in with a JWT) or have an admin issue one via `POST /api/employees/{id}/mcp-token`. Not scoped to internet search — this API only reaches Arkon's own knowledge base.

An admin can turn the whole API off from **Settings → Export API** (`export_api_enabled` config key, defaults to enabled). While disabled, every `/api/export/v1/*` request returns `503`.

The same Settings card lets an admin tune generation for `/chat`: `export_api_temperature` (0.0-1.0), `export_api_top_p` (0.0-1.0), `export_api_max_tokens` (int). Each is optional — unset means the provider default (temperature falls back to the existing 0.5/0.4 used by `/api/chat`). The model itself isn't configured here; it reuses whatever's set in **Chatbot Provider** (falling back to **LLM Provider** if unset).

### POST /api/export/v1/chat
Chat with a persona (`victor` or `ashley`); reuses the same RAG pipeline as `/api/chat`.

**Body:**
```json
{
  "persona": "victor",
  "question": "What is XYZ?",
  "conversation_id": null,
  "workspace_id": null
}
```
Omit `conversation_id` to start a new conversation (owned by the token's employee). `workspace_id` scopes the conversation to a workspace — the token's employee must be a member.

**Response 200:**
```json
{
  "answer": "string",
  "sources": [{ "slug": "string", "title": "string" }],
  "conversation_id": "uuid"
}
```
**Errors:** `401` bad/missing token · `403` no workspace access · `404` unknown `conversation_id` · `422` invalid persona/empty question · `502` chat generation failed (unlike `/api/chat`, no assistant message is saved on failure — machine callers get a clean HTTP error instead of a 200-with-apology) · `503` Export API disabled by an admin.

### GET /api/export/v1/search
Direct semantic search over wiki pages — no chat/persona layer, no wiki-link expansion.

**Query params:**
| Param | Type | Mô tả |
|---|---|---|
| `q` | string | Search query (required) |
| `top_k` | int | Default 10, clamped 1-50 |
| `workspace_id` | uuid | Optional — scope to a workspace |

**Response 200:**
```json
{
  "query": "incident response",
  "results": [
    { "slug": "string", "title": "string", "summary": "string", "page_type": "string", "knowledge_type_slugs": ["string"], "score": 0.87 }
  ]
}
```
**Errors:** `401`, `403` (workspace), `422` empty `q`, `502` embedding/search backend failed (e.g. no active embedding model configured in Settings), `503` Export API disabled by an admin.

---

## Health

### GET /health
Tổng quan sức khỏe hệ thống.

**Response 200:**
```json
{
  "status": "healthy | degraded",
  "services": {
    "database": "healthy | error",
    "redis": "healthy | error",
    "minio": "healthy | error"
  }
}
```

### GET /api/health
Chi tiết health check API + database + worker.

---

## Error Codes

| HTTP Status | Mô tả |
|---|---|
| `400` | Bad request — dữ liệu không hợp lệ |
| `401` | Unauthorized — thiếu hoặc JWT hết hạn |
| `403` | Forbidden — không có permission |
| `404` | Not found |
| `413` | File quá lớn (chỉ áp dụng khi upload qua Next.js proxy) |
| `422` | Validation error — Pydantic validation failed |
| `500` | Internal server error |
| `502` | Bad gateway — MinIO hoặc external service lỗi |

---

## Ví dụ cURL

### Đăng nhập và lấy token

```bash
TOKEN=$(curl -s -X POST http://localhost:5055/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@company.com","password":"pass"}' \
  | python -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

### Upload tài liệu

```bash
curl -X POST http://localhost:5055/api/sources/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@document.pdf" \
  -F "scope_type=global"
```

### Tìm kiếm wiki

```bash
curl "http://localhost:5055/api/wiki/search?q=onboarding+process" \
  -H "Authorization: Bearer $TOKEN"
```

### Tạo nhân viên

```bash
curl -X POST http://localhost:5055/api/employees \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Nguyen Van A",
    "email": "nguyen@company.com",
    "password": "password123",
    "department_id": "uuid-here"
  }'
```

### Lấy compilation plan

```bash
curl "http://localhost:5055/api/sources/{source_id}/plan" \
  -H "Authorization: Bearer $TOKEN"
```

### Approve plan

```bash
curl -X POST "http://localhost:5055/api/sources/{source_id}/approve" \
  -H "Authorization: Bearer $TOKEN"
```
