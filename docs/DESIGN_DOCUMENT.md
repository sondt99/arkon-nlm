# Arkon — Tài liệu Phân tích Thiết kế Hệ thống

**Phiên bản:** 1.2.8  
**Ngày:** 2026-06-05  

---

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Kiến trúc kỹ thuật](#2-kiến-trúc-kỹ-thuật)
3. [Mô hình dữ liệu](#3-mô-hình-dữ-liệu)
4. [Đặc tả API](#4-đặc-tả-api)
5. [Đặc tả Use Case](#5-đặc-tả-use-case)
6. [Luồng xử lý MRP Pipeline](#6-luồng-xử-lý-mrp-pipeline)
7. [Hệ thống quyền hạn (RBAC)](#7-hệ-thống-quyền-hạn-rbac)
8. [Tích hợp MCP](#8-tích-hợp-mcp)

---

## 1. Tổng quan hệ thống

### 1.1 Giới thiệu

Arkon là nền tảng **knowledge base (KB) doanh nghiệp được hỗ trợ bởi AI**. Hệ thống cho phép tổ chức tải lên tài liệu, tự động trích xuất và biên soạn kiến thức thành các trang wiki có cấu trúc, sau đó cung cấp kiến thức đó cho nhân viên và các agent AI thông qua giao diện web và giao thức MCP (Model Context Protocol).

### 1.2 Các tính năng chính

| Tính năng | Mô tả |
|-----------|-------|
| **Ingestion Pipeline (MRP)** | Tự động phân tích tài liệu qua 5 phase: MAP → REDUCE → REFINE → VERIFY → COMMIT |
| **Wiki System** | Trang wiki markdown tự động biên soạn, có wikilink, revision history, draft workflow |
| **Skill System** | Quản lý gói kỹ năng AI (ZIP packages) với workflow đóng góp và phê duyệt |
| **RBAC** | Phân quyền hai tầng: global scope + workspace membership |
| **NotebookLM Integration** | Gửi tài liệu sang Google NotebookLM, nhận artifact ngược lại |
| **RAG Chatbot** | Chatbot hỏi đáp dựa trên wiki KB — RAG search + LLM generation, có lịch sử hội thoại; hội thoại có thể được tổng hợp thành wiki page (Add to Wiki) |
| **Chatbot Provider** | AI provider riêng cho chatbot (tùy chọn); nếu không cấu hình sẽ dùng LLM Provider làm fallback |
| **MCP Server** | Cho phép Claude Desktop/Claude Code truy vấn KB qua 16 tools |

### 1.3 Người dùng hệ thống

| Actor | Vai trò |
|-------|---------|
| **Admin** | Quản trị hệ thống, nhân viên, cài đặt AI provider |
| **Employee (Contributor)** | Tải tài liệu, đề xuất chỉnh sửa wiki |
| **Employee (Editor/Knowledge Admin)** | Phê duyệt draft, chỉnh sửa wiki trực tiếp |
| **MCP Agent (Claude)** | Truy vấn KB, đề xuất/phê duyệt chỉnh sửa qua MCP token |

---

## 2. Kiến trúc kỹ thuật

### 2.1 Stack công nghệ

```
┌─────────────────────────────────────────────────────────────┐
│                     Frontend (Next.js 15)                    │
│  React Client Components · Tailwind CSS · shadcn/ui          │
└─────────────────────────┬───────────────────────────────────┘
                          │ HTTP/REST + SSE (MCP)
┌─────────────────────────▼───────────────────────────────────┐
│                  Backend (FastAPI / Python)                   │
│  Async · Pydantic v2 · SQLAlchemy 2 · arq (task queue)      │
├──────────────┬──────────────┬───────────────────────────────┤
│  PostgreSQL  │    Redis     │          MinIO                 │
│  + pgvector  │  (arq queue) │  (files, images, skills)      │
└──────────────┴──────────────┴───────────────────────────────┘
```

### 2.2 Sơ đồ component

```
app/
├── routers/          # FastAPI route handlers
│   ├── auth.py
│   ├── sources.py
│   ├── wiki.py
│   ├── skills.py
│   ├── projects.py
│   ├── notebooklm.py
│   ├── admin.py
│   └── ...
├── services/         # Business logic layer
│   ├── wiki_service.py
│   ├── kb_service.py         # Document parsing, OCR, image
│   ├── embedding_storage.py
│   ├── notebooklm_service.py
│   ├── permissions.py
│   └── ...
├── ai/               # AI pipeline
│   ├── mrp/
│   │   ├── mapper.py     # Phase 1: MAP
│   │   ├── reducer.py    # Phase 2: REDUCE
│   │   ├── writer.py     # Phase 3: REFINE
│   │   ├── verifier.py   # Phase 4: VERIFY
│   │   └── pipeline.py   # Phase 5: COMMIT + orchestration
│   ├── wiki_agent.py
│   ├── registry.py       # AI provider registry
│   └── embedding_catalog.py
├── database/
│   └── models.py         # SQLAlchemy models
├── mcp/
│   └── tools.py          # MCP server tools (16 tools)
└── worker.py             # arq background tasks
```

### 2.3 Luồng dữ liệu tổng quan

```
[Tài liệu PDF/URL]
        │
        ▼
[ingest_file_task / ingest_url_task]
        │ extract text, images
        ▼
[caption_images_task] ──── Vision LLM ──── captions → source_images
        │
        ▼
[ingest_map_reduce_task]
        │
        ├── Phase 1: MAP ──── LLM ──── chunk extracts → source_chunk_extracts
        │
        └── Phase 2: REDUCE ── LLM+Embeddings ──── compilation plan
                                                          │
                                                   [Human Review]
                                                          │ approve
                                                          ▼
                                              [ingest_refine_task]
                                                          │
                                          ┌───────────────┼───────────────┐
                                          ▼               ▼               ▼
                                    Phase 3:REFINE  Phase 4:VERIFY  Phase 5:COMMIT
                                     (write pages)  (check coverage) (save to DB)
                                                                          │
                                                                   wiki_pages ──── embeddings
```

---

## 3. Mô hình dữ liệu

### 3.1 Nhóm Identity & Organization

#### `employees`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `name` | VARCHAR | Tên nhân viên |
| `email` | VARCHAR UNIQUE | Email đăng nhập |
| `password_hash` | VARCHAR | bcrypt |
| `role` | ENUM | `admin` / `employee` |
| `department_id` | UUID FK→departments | Nullable |
| `custom_role_id` | UUID FK→roles | Vai trò tùy chỉnh, nullable |
| `mcp_token` | VARCHAR UNIQUE | Bearer token dùng chung cho MCP (`/mcp`) và Export API (`/api/export/v1/*`), nullable |
| `is_active` | BOOLEAN | Mặc định TRUE |
| `last_connected` | TIMESTAMP | Lần cuối kết nối |

#### `departments`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `name` | VARCHAR UNIQUE | |
| `description` | TEXT | Nullable |

#### `roles` (Custom RBAC)
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `name` | VARCHAR UNIQUE | |
| `permissions` | JSONB | Mảng permission strings |
| `is_system` | BOOLEAN | Vai trò hệ thống không thể xóa |

---

### 3.2 Nhóm Projects / Workspaces

#### `projects`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `name` | VARCHAR | |
| `workspace_type` | ENUM | `project` / `customer` |
| `status` | ENUM | `active` / `archived` |
| `created_by_id` | UUID FK→employees | |

#### `project_members`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `project_id` | UUID FK (PK) | |
| `employee_id` | UUID FK (PK) | |
| `role` | ENUM | `viewer` / `contributor` / `editor` / `admin` |

---

### 3.3 Nhóm Document Sources

#### `sources`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `title` | VARCHAR | Tiêu đề tài liệu |
| `file_name` | VARCHAR | Tên file gốc, nullable |
| `full_text` | TEXT | Nội dung trích xuất |
| `source_type` | ENUM | `file` / `url` |
| `scope_type` | VARCHAR | `global` / `project` |
| `scope_id` | UUID | ID project nếu scoped, nullable |
| `knowledge_type_id` | UUID FK | Nullable |
| `minio_key` | VARCHAR | Đường dẫn lưu trữ file |
| `status` | ENUM | `pending` / `processing` / `plan_ready` / `ready` / `error` |
| `progress` | INTEGER | 0–100 |
| `progress_message` | VARCHAR | Thông báo tiến trình |
| `pipeline_phase` | VARCHAR | Phase MRP hiện tại |
| `outline_json` | JSONB | Mục lục tài liệu |

#### `source_images`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `source_id` | UUID FK | |
| `minio_key` | VARCHAR | Đường dẫn ảnh trong MinIO |
| `page_number` | INTEGER | Số trang chứa ảnh |
| `caption` | TEXT | Caption do Vision LLM tạo |
| `content_type` | VARCHAR | MIME type (image/jpeg, ...) |

#### `source_chunk_extracts`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `source_id` | UUID FK | |
| `chunk_index` | INTEGER | Thứ tự chunk |
| `start_char` / `end_char` | INTEGER | Vị trí trong full_text |
| `extract_json` | JSONB | Kết quả MAP phase (entities, concepts, facts) |
| `status` | ENUM | `pending` / `done` |

#### `source_compilation_plans`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `source_id` | UUID UNIQUE FK | Một plan mỗi source |
| `plan_json` | JSONB | Danh sách wiki page targets |
| `status` | ENUM | `pending_review` / `approved` / `in_progress` / `done` / `rejected` |
| `reviewed_by` | UUID FK | Nullable |
| `review_note` | VARCHAR | Ghi chú phê duyệt |

---

### 3.4 Nhóm Wiki

#### `wiki_pages`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `slug` | VARCHAR | Định danh duy nhất trong scope |
| `title` | VARCHAR | |
| `page_type` | ENUM | `entity` / `concept` / `topic` / `source` / `synthesis` |
| `content_md` | TEXT | Nội dung Markdown |
| `summary` | TEXT | Tóm tắt ngắn |
| `scope_type` | VARCHAR | `global` / `project` |
| `scope_id` | UUID | ID project, nullable |
| `source_ids` | ARRAY(UUID) | Các source đóng góp |
| `knowledge_type_slugs` | ARRAY(VARCHAR) | |
| `version` | INTEGER | Tăng mỗi lần cập nhật |
| `orphaned` | BOOLEAN | Không có wikilink trỏ vào |

#### `wiki_page_drafts`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `page_id` | UUID FK→wiki_pages | |
| `author_id` | UUID FK→employees | |
| `content_md` | TEXT | Nội dung đề xuất |
| `status` | ENUM | `pending` / `approved` / `rejected` |
| `source` | VARCHAR | `web_ui` / `mcp_claude_desktop` / ... |

#### `wiki_page_revisions`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `page_id` | UUID FK | |
| `version` | INTEGER | |
| `content_md` | TEXT | |
| `change_type` | ENUM | `agent_compile` / `editor_edit` / `draft_approved` / `rollback` |
| `changed_by_id` | UUID FK | Null = system |

---

### 3.5 Nhóm Skills

#### `skills`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `name` / `slug` | VARCHAR | |
| `current_version` | VARCHAR | Semver |
| `storage_path` | VARCHAR | MinIO path |
| `status` | ENUM | `active` / `processing` / `deprecated` / `archived` |
| `is_system` | BOOLEAN | Kỹ năng tích hợp sẵn |

#### `skill_contributions`
| Cột | Kiểu | Mô tả |
|-----|------|-------|
| `id` | UUID PK | |
| `skill_id` | UUID FK | Null = đề xuất kỹ năng mới |
| `contributor_id` | UUID FK | |
| `status` | ENUM | `draft` / `pending` / `approved` / `rejected` |
| `storage_path` | VARCHAR | MinIO staging area |

---

### 3.6 Nhóm Embeddings

Bốn bảng vector tương ứng với 4 kích thước: **768, 1024, 1536, 3072**.

```
wiki_page_embeddings_{768|1024|1536|3072}
  ├── page_id       UUID FK (PK component)
  ├── model_spec_id VARCHAR  (PK component)
  ├── content_hash  VARCHAR  SHA256 của content
  └── embedding     VECTOR(N)
```

### 3.7 Nhóm Chat (RAG Chatbot)

```
chat_conversations
  ├── id          UUID PK
  ├── employee_id UUID FK → employees
  ├── title       VARCHAR(500)
  ├── scope_type  VARCHAR(20)   "global" | "project"
  ├── scope_id    UUID nullable → workspace
  ├── created_at  TIMESTAMPTZ
  └── updated_at  TIMESTAMPTZ

chat_messages
  ├── id              UUID PK
  ├── conversation_id UUID FK → chat_conversations (CASCADE)
  ├── role            VARCHAR(20)  "user" | "assistant"
  ├── content         TEXT
  ├── sources         JSON nullable  [{slug, title}]
  └── created_at      TIMESTAMPTZ
```

---

## 4. Đặc tả API

### 4.1 Xác thực — `/api/auth`

---

#### `POST /api/auth/login`
**Mô tả:** Đăng nhập và nhận JWT access token.

**Request Body:**
```json
{
  "email": "user@company.com",
  "password": "secret"
}
```

**Response 200:**
```json
{
  "access_token": "eyJhbGci...",
  "token_type": "bearer",
  "user": {
    "id": "uuid",
    "name": "Nguyễn Văn A",
    "email": "user@company.com",
    "role": "employee",
    "department_id": "uuid",
    "department_name": "Engineering",
    "permissions": ["doc:read:own_dept", "wiki:read:own_dept"],
    "workspace_memberships": [{"project_id": "uuid", "role": "contributor"}]
  }
}
```

**Lỗi:** `401` — sai thông tin đăng nhập; `403` — tài khoản bị vô hiệu hóa.

---

#### `GET /api/auth/me`
**Mô tả:** Lấy thông tin người dùng hiện tại.  
**Auth:** JWT Bearer  
**Response:** Cùng cấu trúc `user` như login response.

---

#### `POST /api/auth/change-password`
**Request Body:** `{current_password, new_password}`  
**Response 200:** `{message: "Password changed successfully"}`

---

### 4.2 Tài liệu (Sources) — `/api/sources`

---

#### `GET /api/sources`
**Mô tả:** Lấy danh sách tài liệu với phân trang và lọc.  
**Auth:** JWT, cần `doc:read:*`

**Query Parameters:**
| Param | Kiểu | Mô tả |
|-------|------|-------|
| `knowledge_type_id` | UUID | Lọc theo loại kiến thức |
| `department_id` | UUID | Lọc theo phòng ban |
| `status` | string | `pending` / `processing` / `ready` / `error` |
| `search` | string | Tìm kiếm theo title |
| `page` | int | Mặc định 1 |
| `page_size` | int | Mặc định 20 |

**Response 200:**
```json
{
  "items": [{
    "id": "uuid",
    "title": "Báo cáo Q1 2025",
    "file_name": "bao-cao-q1.pdf",
    "source_type": "file",
    "scope_type": "global",
    "status": "ready",
    "progress": 100,
    "page_count": 45,
    "wiki_page_count": 12,
    "knowledge_type_name": "Finance",
    "knowledge_type_color": "#2563eb",
    "department_names": ["Finance", "Management"],
    "contributed_by_name": "Nguyễn Văn A",
    "created_at": "2025-01-15T10:30:00Z"
  }],
  "total": 150,
  "page": 1,
  "page_size": 20,
  "total_pages": 8
}
```

---

#### `POST /api/sources/upload`
**Mô tả:** Tải lên tài liệu PDF/DOCX/TXT.  
**Content-Type:** `multipart/form-data`  
**Auth:** JWT, cần `doc:create:*`

**Form Fields:**
| Field | Bắt buộc | Mô tả |
|-------|----------|-------|
| `file` | ✓ | File tài liệu |
| `title` | | Tiêu đề (mặc định = tên file) |
| `knowledge_type_id` | | UUID |
| `department_ids` | | Mảng UUID |
| `scope_type` | | `global` (mặc định) / `project` |
| `scope_id` | | UUID project nếu `scope_type=project` |

**Response 201:** `SourceResponse` — tài liệu được tạo với `status: "pending"`.  
**Side effect:** Enqueue `ingest_file_task` vào arq queue.

---

#### `POST /api/sources/url`
**Mô tả:** Thêm tài liệu từ URL.  
**Request Body:**
```json
{
  "url": "https://example.com/document.pdf",
  "title": "Tên tài liệu",
  "knowledge_type_id": "uuid"
}
```
**Response 201:** `SourceResponse`  
**Side effect:** Enqueue `ingest_url_task`.

---

#### `GET /api/sources/{id}/progress`
**Mô tả:** Polling tiến trình xử lý tài liệu.  
**Response:**
```json
{
  "id": "uuid",
  "status": "processing",
  "progress": 45,
  "progress_message": "Extracting knowledge from chunks (3/8)...",
  "page_count": 45,
  "wiki_page_count": 0
}
```

---

#### `GET /api/sources/{id}/plan`
**Mô tả:** Lấy compilation plan cho tài liệu (sau phase REDUCE).  
**Response:**
```json
{
  "id": "uuid",
  "source_id": "uuid",
  "status": "pending_review",
  "plan": {
    "pages": [
      {
        "slug": "nguyen-van-a",
        "title": "Nguyễn Văn A",
        "page_type": "entity",
        "action": "CREATE",
        "priority": 1
      }
    ]
  },
  "created_at": "2025-01-15T11:00:00Z"
}
```

---

#### `POST /api/sources/{id}/plan/approve`
**Mô tả:** Phê duyệt compilation plan, bắt đầu phase REFINE.  
**Auth:** Admin hoặc `doc:manage`  
**Request Body:**
```json
{
  "note": "Looks good",
  "modified_plan": { "pages": [...] }
}
```
**Response:** `{job_id: "arq-job-id"}`  
**Side effect:** Enqueue `ingest_refine_task`.

---

#### `GET /api/sources/{id}/wiki-pages`
**Mô tả:** Lấy danh sách wiki pages được tạo từ tài liệu này.  
**Response:**
```json
[
  {
    "id": "uuid",
    "slug": "nguyen-van-a",
    "title": "Nguyễn Văn A",
    "page_type": "entity",
    "summary": "Giám đốc điều hành...",
    "updated_at": "2025-01-15T12:00:00Z"
  }
]
```

---

#### `PATCH /api/sources/{id}`
**Mô tả:** Cập nhật metadata tài liệu.  
**Request Body:** (tất cả optional)
```json
{
  "title": "Tiêu đề mới",
  "knowledge_type_id": "uuid",
  "department_ids": ["uuid1", "uuid2"],
  "scope_type": "project",
  "scope_id": "uuid"
}
```

---

#### `DELETE /api/sources/{id}`
**Auth:** Admin  
**Side effect:** Xóa file từ MinIO, xóa `source_id` khỏi tất cả wiki pages liên quan.

---

#### `POST /api/sources/{id}/retry`
**Mô tả:** Thử lại xử lý tài liệu sau khi lỗi.  
**Response:** `SourceResponse` với status reset về `processing`.

---

### 4.3 Wiki — `/api/wiki`

---

#### `GET /api/wiki/pages`
**Mô tả:** Lấy danh sách wiki pages.

**Query Parameters:**
| Param | Mô tả |
|-------|-------|
| `page_type` | `entity` / `concept` / `topic` / `source` |
| `knowledge_type_slug` | Lọc theo loại kiến thức |
| `limit` | Mặc định 200 |
| `offset` | |
| `scope_type` | `global` / `project` |
| `scope_id` | UUID project |

**Response:** `WikiPageSummary[]`
```json
[{
  "slug": "quy-trinh-onboarding",
  "title": "Quy trình Onboarding",
  "page_type": "topic",
  "summary": "Hướng dẫn tiếp nhận nhân viên mới...",
  "knowledge_type_slugs": ["hr"],
  "version": 3,
  "orphaned": false
}]
```

---

#### `GET /api/wiki/pages/{slug}`
**Mô tả:** Lấy nội dung đầy đủ của một wiki page.

**Response:** `WikiPageDetail`
```json
{
  "slug": "quy-trinh-onboarding",
  "title": "Quy trình Onboarding",
  "page_type": "topic",
  "content_md": "# Quy trình Onboarding\n\n...",
  "summary": "...",
  "version": 3,
  "backlinks": [{"slug": "hr-policies", "title": "HR Policies"}],
  "outlinks": [{"slug": "it-setup", "title": "IT Setup"}],
  "source_ids": ["uuid1", "uuid2"]
}
```

---

#### `PUT /api/wiki/pages/{slug}`
**Mô tả:** Cập nhật nội dung wiki page trực tiếp (không qua draft).  
**Auth:** `wiki:write` hoặc `wiki:manage`  
**Request Body:**
```json
{
  "content_md": "# Nội dung mới\n\n...",
  "change_note": "Cập nhật quy trình 2025"
}
```
**Side effect:** Tạo revision mới, regenerate wikilinks.

---

#### `POST /api/wiki/pages/{slug}/drafts`
**Mô tả:** Tạo draft đề xuất chỉnh sửa (dành cho contributor).  
**Request Body:**
```json
{
  "content_md": "# Nội dung đề xuất\n\n...",
  "note": "Bổ sung thông tin về chính sách mới"
}
```
**Response 201:** `DraftResponse`

---

#### `GET /api/wiki/drafts`
**Mô tả:** Lấy danh sách drafts chờ phê duyệt.  
**Auth:** Editor/Admin  
**Query:** `status=pending` (mặc định)  
**Response:** `DraftResponse[]`

---

#### `POST /api/wiki/drafts/{id}/approve`
**Auth:** Editor/Admin  
**Request Body:**
```json
{
  "reviewer_note": "Đã kiểm tra, chính xác",
  "edited_content_md": "# Nội dung sau chỉnh sửa nhỏ\n\n..."
}
```

---

#### `GET /api/wiki/graph`
**Mô tả:** Lấy dữ liệu đồ thị liên kết giữa các wiki pages.  
**Query:** `slug` (trung tâm), `depth` (mặc định 2)  
**Response:**
```json
{
  "nodes": [{"id": "slug", "label": "Title", "page_type": "entity"}],
  "edges": [{"source": "slug-a", "target": "slug-b"}],
  "total": 45,
  "has_more": false
}
```

---

#### `GET /api/wiki/pages/{slug}/revisions`
**Response:** `WikiRevisionSummary[]`
```json
[{
  "id": "uuid",
  "version": 3,
  "change_type": "editor_edit",
  "changed_by_name": "Trần Thị B",
  "change_note": "Cập nhật quy trình 2025",
  "created_at": "2025-01-15T14:00:00Z"
}]
```

---

#### `POST /api/wiki/pages/{slug}/revisions/{version}/rollback`
**Auth:** Admin  
**Mô tả:** Khôi phục wiki page về phiên bản cũ.

---

### 4.4 Nhân viên & Tổ chức — `/api`

---

#### `GET /api/employees`
**Auth:** Admin  
**Query:** `search`, `department_id`, `page`, `page_size`  
**Response:**
```json
{
  "items": [{
    "id": "uuid",
    "name": "Nguyễn Văn A",
    "email": "a@company.com",
    "role": "employee",
    "department_name": "Engineering",
    "is_active": true,
    "has_mcp_token": false,
    "last_connected": "2025-01-15T08:00:00Z"
  }],
  "total": 50
}
```

---

#### `POST /api/employees`
**Auth:** Admin  
**Request Body:**
```json
{
  "name": "Trần Thị B",
  "email": "b@company.com",
  "password": "initial-password",
  "role": "employee",
  "department_id": "uuid",
  "custom_role_id": "uuid"
}
```

---

#### `POST /api/my/mcp-token`
**Mô tả:** Tự tạo MCP token (self-service) để tích hợp với Claude Desktop.  
**Auth:** JWT (bất kỳ nhân viên)  
**Response:**
```json
{
  "token": "arkon_abc123...",
  "employee_name": "Nguyễn Văn A",
  "instructions": "Add this token to your Claude Desktop MCP config..."
}
```

---

### 4.5 Projects / Workspaces — `/api/projects`

---

#### `GET /api/projects`
**Mô tả:** Admin thấy tất cả; nhân viên thường chỉ thấy các project mà họ là thành viên.  
**Response:** `ProjectOut[]`
```json
[{
  "id": "uuid",
  "name": "Project Alpha",
  "workspace_type": "project",
  "status": "active",
  "member_count": 8,
  "my_role": "contributor"
}]
```

---

#### `POST /api/projects/{id}/members`
**Auth:** Workspace Admin  
**Request Body:** `{employee_id, role}`  
**Roles:** `viewer` / `contributor` / `editor` / `admin`

---

#### `POST /api/projects/{id}/sources/upload`
**Mô tả:** Tải tài liệu trực tiếp vào workspace (tài liệu được scoped vào project).

---

### 4.6 Skills — `/api/skills`

---

#### `POST /api/skills/upload`
**Mô tả:** Tải lên gói kỹ năng AI (file ZIP).  
**Content-Type:** `multipart/form-data`

- **Admin:** Cài đặt trực tiếp → `{results: [{slug, status, action}]}`
- **Non-admin:** Tạo contribution chờ phê duyệt → `{contribution_id}`

**Validation:**
- Tối đa 100 files trong ZIP
- Tổng kích thước uncompressed ≤ 10 MB
- Kiểm tra Zip Slip attack

---

#### `GET /api/skills/{slug}/files/content`
**Mô tả:** Xem nội dung file trong skill package (để review).  
**Query:** `path: string`, `version?: string`  
**Response:** `{content: "...file content..."}`

---

#### `POST /api/skill-contributions/{id}/approve`
**Auth:** Admin  
**Mô tả:** Phê duyệt skill contribution, tạo phiên bản mới của skill.  
**Response:** `{status: "approved", skill_id, skill_slug, version: "1.2.0"}`

---

### 4.7 NotebookLM — `/api/notebooklm`

---

#### `POST /api/notebooklm/auth/import-cookies`
**Mô tả:** Import cookies Google từ browser extension (Cookie-Editor format) để xác thực với NotebookLM.  
**Request Body:**
```json
{
  "cookies": [
    {"name": "SID", "value": "...", "domain": ".google.com", ...}
  ]
}
```
**Validation:** Yêu cầu tối thiểu `SID` và `__Secure-1PSIDTS`.

---

#### `POST /api/notebooklm/notebooks`
**Mô tả:** Tạo NotebookLM notebook mới và đưa nội dung tài liệu Arkon vào.  
**Request Body:**
```json
{
  "title": "Phân tích Q1 2025",
  "source_id": "uuid"
}
```
**Response 201:** `NotebookResponse`  
**Side effect:** Gọi NLM API tạo notebook và thêm `source.full_text` như nguồn.

---

#### `POST /api/notebooklm/nlm/notebooks/{nlm_id}/sources`
**Mô tả:** Thêm nguồn vào notebook NLM đã có.  
**Request Body (kind=arkon):**
```json
{
  "kind": "arkon",
  "source_id": "uuid"
}
```
**Các kind:** `url` / `text` / `arkon` / `drive`

---

#### `POST /api/notebooklm/nlm/notebooks/{nlm_id}/artifacts/generate`
**Mô tả:** Kích hoạt tạo artifact (podcast, report, quiz...) trên NLM.  
**Request Body:**
```json
{
  "artifact_type": "audio",
  "report_format": null
}
```
**Artifact types:** `audio` / `video` / `report` / `quiz` / `flashcards` / `slide_deck` / `infographic` / `data_table`

---

#### `POST /api/notebooklm/nlm/notebooks/{nlm_id}/artifacts/{artifact_id}/ingest`
**Mô tả:** Lấy artifact từ NLM và đưa vào Arkon wiki pipeline.  
**Side effect:** Text artifacts → `ingest_map_reduce_task`; PDF → `ingest_file_task`.

---

### 4.8 Cài đặt Admin — `/api/settings`

---

#### `GET /api/settings`
**Auth:** Admin  
**Mô tả:** Lấy cài đặt AI provider (API keys được mask `••••••••last4`).

---

#### `PUT /api/settings`
**Request Body:**
```json
{
  "settings": {
    "llm_provider": "openai",
    "llm_model": "gpt-4o",
    "llm_api_key": "sk-...",
    "embedding_provider": "openai",
    "embedding_model": "text-embedding-3-large",
    "chatbot_provider": "anthropic",
    "chatbot_model_id": "claude-3-5-haiku-20241022",
    "chatbot_api_key": "sk-ant-...",
    "chatbot_base_url": null
  }
}
```
`chatbot_*` keys là tùy chọn. Nếu `chatbot_provider` không được cấu hình, chatbot sẽ fallback về LLM Provider.

---

#### `POST /api/settings/test-llm`
**Auth:** Admin  
**Mô tả:** Kiểm tra kết nối LLM Provider đang được cấu hình.  
**Response 200:** `{ok: true, model: "gpt-4o"}`

---

#### `POST /api/settings/test-chatbot`
**Auth:** Admin  
**Mô tả:** Kiểm tra kết nối Chatbot AI Provider (hoặc fallback LLM nếu chưa cấu hình chatbot riêng).  
**Response 200:** `{ok: true, model: "claude-3-5-haiku-20241022"}`

---

#### `POST /api/settings/embeddings/switch`
**Mô tả:** Chuyển đổi embedding model. Tự động tạo job để re-embed toàn bộ wiki.  
**Request Body:** `{model_spec_id: "openai-3-large-3072"}`  
**Response:** `{job_id: "uuid"}`

---

### 4.9 Audit Log — `/api/audit`

---

#### `GET /api/audit/log`
**Auth:** Admin  
**Query:** `page`, `page_size`, `principal_id`, `action`, `decision`, `resource_type`  
**Response:**
```json
{
  "items": [{
    "timestamp": "2025-01-15T10:00:00Z",
    "principal_id": "uuid",
    "principal_type": "human",
    "action": "wiki.page.edit",
    "resource_type": "wiki_page",
    "resource_id": "slug",
    "decision": "allow"
  }],
  "total": 1500
}
```

---

### 4.10 Chat (RAG Chatbot) — `/api/chat`

Chatbot dựa trên RAG (Retrieval-Augmented Generation) — trả lời câu hỏi dựa trên nội dung wiki. Mỗi người dùng có danh sách conversations riêng.

---

#### `GET /api/chat/conversations`
**Auth:** Any authenticated user  
**Response:** `[{id, title, scope_type, scope_id, created_at, updated_at}]`

---

#### `POST /api/chat/conversations`
**Auth:** Any authenticated user  
**Body:**
```json
{
  "title": "New conversation",
  "scope_type": "global",
  "scope_id": null
}
```
**Response:** `ConversationOut`

---

#### `PATCH /api/chat/conversations/{id}`
**Auth:** Owner  
**Body:** `{"title": "Renamed title"}`  
**Response:** `ConversationOut`

---

#### `DELETE /api/chat/conversations/{id}`
**Auth:** Owner  
**Response:** 204

---

#### `GET /api/chat/conversations/{id}/messages`
**Auth:** Owner  
**Response:** `[{id, role, content, sources, created_at}]` (chronological)

---

#### `POST /api/chat/conversations/{id}/messages`
**Auth:** Owner  
**Body:** `{"content": "What is XYZ?"}`  
**Flow:**
1. Embed question → pgvector search (top-5 wiki pages within scope)
2. 1-hop wiki_links expansion from top-3 results
3. Build system prompt with wiki context blocks
4. Inject last 6 messages as conversation history
5. LLM.generate() → answer
6. Save user + assistant messages with sources JSON
**Response:**
```json
{
  "user_message": {id, role: "user", content, sources: null, created_at},
  "assistant_message": {id, role: "assistant", content, sources: [{slug, title}], created_at}
}
```

---

#### `POST /api/chat/conversations/{id}/to-wiki`
**Auth:** Owner  
**Mô tả:** Tổng hợp toàn bộ hội thoại thành một wiki page mới (page_type `synthesis`) bằng LLM.  
**Body:**
```json
{
  "title": "SQL Injection Prevention Techniques",
  "page_type": "synthesis",
  "scope_type": "global"
}
```
`page_type` có thể là `synthesis` / `topic` / `concept` / `entity` / `source`. Mặc định nên dùng `synthesis`.

**Flow:**
1. Load toàn bộ messages của conversation
2. Xây dựng transcript Q&A
3. LLM tổng hợp thành văn xuôi bách khoa (encyclopedic prose) bằng prompt chuyên biệt
4. Tạo slug từ title + 6-char uuid suffix
5. `wiki_service.apply_create()` → lưu WikiPage vào DB
6. `upsert_page_embedding()` nếu embedding model đã cấu hình (non-fatal nếu lỗi)

**Response 200:**
```json
{
  "slug": "sql-injection-prevention-techniques-a1b2c3",
  "title": "SQL Injection Prevention Techniques"
}
```
**Lỗi:** `400` — không có messages; `422` — page_type không hợp lệ; `504` — LLM timeout (120s)

---

### 4.11 Export API (REST cho công cụ ngoài) — `/api/export/v1`

Cho phép công cụ bên ngoài không nói giao thức MCP (n8n, Zapier, script nội bộ, nền tảng AI khác) chat với Victor/Ashley hoặc tìm kiếm wiki trực tiếp qua REST. Dùng lại đúng token MCP (`Employee.mcp_token`) làm credential — không phát sinh hệ key mới. Chưa hỗ trợ tìm kiếm ngoài internet (đánh giá và hoãn lại; xem `docs/API-REFERENCE.md`).

**Auth:** `Authorization: Bearer <mcp_token>` — xác thực qua `MCPAuthService.verify_token()` (giống MCP), không phải JWT nhân viên. Token sai/hết hiệu lực → `401`.

---

#### `POST /api/export/v1/chat`
**Body:**
```json
{
  "persona": "victor",
  "question": "What is XYZ?",
  "conversation_id": null,
  "workspace_id": null
}
```
`persona` chỉ nhận `"victor"` hoặc `"ashley"`. Không truyền `conversation_id` → tạo hội thoại mới (chủ sở hữu = nhân viên gắn với token); có `workspace_id` → kiểm tra `can_access_workspace()`, hội thoại scope `project`.

**Flow:** tái dùng nguyên vẹn `chat_service.save_message` + `chat_service.generate_reply` (cùng logic RAG/persona với `/api/chat`) — không triển khai lại.

**Response 200:**
```json
{
  "answer": "...",
  "sources": [{"slug": "...", "title": "..."}],
  "conversation_id": "..."
}
```
**Lỗi:** `401` — token sai/thiếu; `403` — không có quyền vào workspace; `404` — `conversation_id` không thuộc token này; `422` — persona/question không hợp lệ; `502` — LLM/provider lỗi khi sinh câu trả lời (khác `/api/chat`: **không** lưu tin nhắn assistant lỗi, để caller máy-gọi-máy nhận lỗi HTTP rõ ràng thay vì phải đọc nội dung answer).

---

#### `GET /api/export/v1/search`
**Query:** `q` (bắt buộc), `top_k` (mặc định 10, giới hạn 1-50), `workspace_id` (tùy chọn).

**Flow:** gọi thẳng `wiki_service.search_pages_semantic()` (không qua `rag_search()` của chat, nên không có bước mở rộng 1-hop wiki_links) — kết quả thô, xếp hạng theo cosine similarity, lọc theo `allowed_knowledge_types` của token.

**Response 200:**
```json
{
  "query": "incident response",
  "results": [
    {"slug": "...", "title": "...", "summary": "...", "page_type": "concept", "knowledge_type_slugs": ["sop"], "score": 0.87}
  ]
}
```
**Lỗi:** `401`, `403` (workspace), `422` — `q` rỗng; `502` — lỗi embedding/search backend (ví dụ chưa cấu hình embedding model trong Settings).

---

## 5. Đặc tả Use Case

### UC-01: Đăng nhập hệ thống

**Actor:** Nhân viên / Admin  
**Mục tiêu:** Xác thực và lấy JWT token để sử dụng hệ thống

**Tiền điều kiện:**
- Tài khoản đã được tạo bởi Admin
- Tài khoản đang hoạt động (`is_active = true`)

**Luồng chính:**
1. Người dùng nhập email và mật khẩu
2. Hệ thống xác thực thông tin với `POST /api/auth/login`
3. Hệ thống trả về JWT token và thông tin người dùng (bao gồm permissions)
4. Frontend lưu token vào localStorage/cookie
5. Người dùng được chuyển đến dashboard

**Luồng thay thế:**
- *2a.* Sai email/password → `401 Unauthorized`
- *2b.* Tài khoản bị vô hiệu hóa → `403 Forbidden`

**Hậu điều kiện:** Người dùng đã đăng nhập, có JWT token hợp lệ

---

### UC-02: Tải lên tài liệu

**Actor:** Contributor, Knowledge Admin, Admin  
**Mục tiêu:** Tải tài liệu PDF/DOCX lên hệ thống để xử lý và biên soạn kiến thức

**Tiền điều kiện:**
- Người dùng đã đăng nhập
- Có quyền `doc:create:own_dept` hoặc `doc:create:all`

**Luồng chính:**
1. Người dùng truy cập trang Knowledge
2. Nhấn "Upload Document"
3. Chọn file, điền tiêu đề, chọn knowledge type, department, scope
4. Nhấn Upload → `POST /api/sources/upload`
5. Hệ thống lưu file vào MinIO, tạo bản ghi `sources` với `status=pending`
6. Background task `ingest_file_task` được enqueue
7. Frontend polling `GET /api/sources/{id}/progress` mỗi 3 giây
8. Khi `status=plan_ready`: frontend hiển thị nút "Review Plan"
9. Nếu `mrp_auto_approve_plan=true`: tự động chuyển sang REFINE, bỏ qua bước 8-10
10. Admin/Editor review plan, chỉnh sửa nếu cần, nhấn Approve
11. `POST /api/sources/{id}/plan/approve` → enqueue `ingest_refine_task`
12. Tiến trình tiếp tục đến `status=ready`

**Luồng thay thế:**
- *6a.* File không phải PDF/DOCX hợp lệ → `status=error`, hiển thị thông báo
- *10a.* Admin reject plan → tài liệu ở `status=error`, có thể retry
- *12a.* Pipeline lỗi bất kỳ phase → `status=error`, có thể retry từ phase đó

**Hậu điều kiện:**
- `source.status = "ready"`
- Các wiki pages đã được tạo/cập nhật
- Embeddings đã được tính toán

---

### UC-03: Xem và chỉnh sửa Wiki Page

**Actor:** Nhân viên (mọi vai trò)  
**Mục tiêu:** Đọc và đề xuất chỉnh sửa nội dung wiki

**Tiền điều kiện:**
- Người dùng đã đăng nhập
- Có quyền `wiki:read:own_dept` hoặc cao hơn

**Luồng chính (xem):**
1. Người dùng truy cập `/wiki`
2. Wiki tree hiển thị tất cả pages nhóm theo type (entity/concept/topic/source)
3. Click vào page → `GET /api/wiki/pages/{slug}`
4. Frontend render markdown, resolve wikilinks, hiển thị ảnh
5. Sidebar hiển thị backlinks, outlinks

**Luồng chính (đề xuất chỉnh sửa — Contributor):**
1. Click "Propose Edit" trên wiki page
2. Editor hiện ra với nội dung hiện tại
3. Người dùng chỉnh sửa, điền ghi chú
4. Submit → `POST /api/wiki/pages/{slug}/drafts`
5. Draft ở trạng thái `pending`, chờ Editor/Admin phê duyệt

**Luồng chính (chỉnh sửa trực tiếp — Editor/Admin):**
1. Click "Edit" → editor hiện ra
2. Chỉnh sửa nội dung markdown
3. Save → `PUT /api/wiki/pages/{slug}`
4. Hệ thống lưu revision mới, cập nhật wikilinks

**Hậu điều kiện:** Kiến thức được cập nhật và phản ánh trong wiki

---

### UC-04: Phê duyệt Wiki Draft

**Actor:** Editor, Knowledge Admin, Admin  
**Mục tiêu:** Review và phê duyệt/từ chối đề xuất chỉnh sửa wiki

**Tiền điều kiện:**
- Có draft ở trạng thái `pending`
- Người dùng có quyền `wiki:manage`

**Luồng chính:**
1. Vào trang Draft Review
2. `GET /api/wiki/drafts?status=pending`
3. Click vào draft để xem chi tiết, so sánh với nội dung hiện tại
4. Có thể chỉnh sửa nhỏ trước khi duyệt
5. Nhấn Approve → `POST /api/wiki/drafts/{id}/approve`
6. Wiki page được cập nhật, revision mới được tạo

**Luồng thay thế:**
- *5a.* Nhấn Reject, điền lý do → `POST /api/wiki/drafts/{id}/reject`
- Draft được đánh dấu `rejected`, người đề xuất được thông báo

---

### UC-05: Tìm kiếm kiến thức

**Actor:** Nhân viên, MCP Agent  
**Mục tiêu:** Tìm thông tin trong knowledge base

**Tiền điều kiện:** Người dùng đã đăng nhập (hoặc có MCP token)

**Luồng chính (qua giao diện web):**
1. Nhấn Cmd+K để mở Wiki Search Dialog
2. Gõ từ khóa
3. Hệ thống tìm kiếm full-text trên `wiki_pages` (title, summary, content)
4. Kết quả hiển thị nhóm theo type
5. Click vào kết quả → mở wiki page

**Luồng chính (qua MCP/Claude):**
1. Claude gọi `search_wiki(query="thông tin về onboarding")`
2. Hệ thống tìm kiếm semantic (vector) + keyword
3. Trả về danh sách pages với summary
4. Claude gọi `read_wiki_page(slug="quy-trinh-onboarding")` để đọc đầy đủ

---

### UC-06: Quản lý Skills AI

**Actor:** Admin, Contributor (đề xuất)  
**Mục tiêu:** Cài đặt và quản lý gói kỹ năng AI cho hệ thống

**Tiền điều kiện:** Admin đã đăng nhập

**Luồng chính (Admin cài đặt trực tiếp):**
1. Admin truy cập Skills management
2. Upload file ZIP chứa skill package
3. `POST /api/skills/upload` với `force=true`
4. `ingest_skill_task` validate và giải nén vào MinIO
5. Skill được active ngay lập tức

**Luồng chính (Contributor đề xuất):**
1. Contributor upload ZIP → tự động tạo `skill_contribution` với `status=draft`
2. Contributor submit → `status=pending`
3. Admin review file trong contribution: `GET /api/skill-contributions/{id}/files`
4. Xem nội dung từng file: `GET /api/skill-contributions/{id}/files/content`
5. Nhấn Approve → `POST /api/skill-contributions/{id}/approve`
6. Skill được tạo/cập nhật phiên bản mới

**Bảo mật:**
- Kiểm tra Zip Slip: từ chối path thoát khỏi thư mục trích xuất
- Giới hạn: tối đa 100 files, 10 MB uncompressed

---

### UC-07: Tích hợp với NotebookLM

**Actor:** Nhân viên  
**Mục tiêu:** Gửi tài liệu sang Google NotebookLM để tạo podcast/report AI

**Tiền điều kiện:**
- Đã import cookies Google thành công
- Tài liệu có `status=ready`

**Luồng chính:**
1. Trong Knowledge table, click "Send to NotebookLM" trên tài liệu
2. Dialog hiện ra với 2 lựa chọn:
   - **Tạo notebook mới:** Nhập tên → `POST /api/notebooklm/notebooks`
   - **Thêm vào notebook có sẵn:** Load `GET /api/notebooklm/nlm/notebooks` → chọn notebook → `POST /api/notebooklm/nlm/notebooks/{id}/sources` với `kind=arkon`
3. Hệ thống đưa `source.full_text` vào NLM notebook
4. Redirect sang trang NotebookLM trong Arkon
5. Người dùng yêu cầu tạo artifact (ví dụ: audio podcast)
6. `POST /api/notebooklm/nlm/notebooks/{id}/artifacts/generate` với `artifact_type=audio`
7. Polling trạng thái artifact
8. Khi hoàn thành: có thể download hoặc ingest ngược lại vào Arkon wiki

**Luồng ingest ngược:**
1. Click "Add to Wiki" trên artifact hoàn thành
2. `POST /api/notebooklm/nlm/notebooks/{id}/artifacts/{artifact_id}/ingest`
3. Text artifacts → `ingest_map_reduce_task` → tạo wiki pages mới
4. PDF (slide_deck) → `ingest_file_task` → full MRP pipeline

---

### UC-08: Cấu hình AI Provider

**Actor:** Admin  
**Mục tiêu:** Cấu hình LLM, Embedding, Vision model cho hệ thống

**Tiền điều kiện:** Admin đã đăng nhập

**Luồng chính:**
1. Vào Admin → Settings
2. `GET /api/settings` — xem cấu hình hiện tại (API keys bị mask)
3. Chọn provider (OpenAI, Anthropic, Azure, Ollama...)
4. Điền API key, model name, base URL
5. Click "Test Connection" → `POST /api/settings/test-llm`
6. Nếu test thành công → `PUT /api/settings` lưu cài đặt
7. Để thay đổi embedding model: `POST /api/settings/embeddings/switch`
8. Hệ thống tạo job re-embed tất cả wiki pages với model mới

---

### UC-09: Quản lý Workspace

**Actor:** Admin, Workspace Admin  
**Mục tiêu:** Tạo và quản lý workspace riêng cho team/dự án

**Tiền điều kiện:** Admin đã đăng nhập

**Luồng chính:**
1. `POST /api/projects` tạo workspace mới
2. Creator tự động trở thành workspace admin
3. Thêm thành viên: `POST /api/projects/{id}/members` với các role khác nhau
4. Upload tài liệu vào workspace: `POST /api/projects/{id}/sources/upload`
   - Tài liệu được scoped: `scope_type=project`, `scope_id={project_id}`
   - Wiki pages được biên soạn chỉ visible trong workspace này
5. Xem wiki workspace: `GET /api/projects/{id}/wiki`

---

### UC-10: Tích hợp Claude Desktop qua MCP

**Actor:** Nhân viên sử dụng Claude Desktop  
**Mục tiêu:** Cho phép Claude Desktop truy vấn knowledge base qua giao thức MCP

**Tiền điều kiện:**
- Claude Desktop đã cài đặt
- Nhân viên có tài khoản Arkon

**Luồng chính:**
1. Nhân viên vào Profile → "Generate MCP Token" → `POST /api/my/mcp-token`
2. Copy token, thêm vào config Claude Desktop:
   ```json
   {
     "mcpServers": {
       "arkon": {
         "url": "http://arkon.company.com/mcp",
         "headers": {"Authorization": "Bearer arkon_abc123..."}
       }
     }
   }
   ```
3. Claude Desktop kết nối MCP server tại `/mcp`
4. Claude có thể gọi:
   - `search_wiki("chính sách nghỉ phép")` — tìm kiếm semantic
   - `read_wiki_page("chinh-sach-nghi-phep")` — đọc đầy đủ
   - `propose_wiki_edit(slug, content_md, note)` — tạo draft
   - `list_pending_drafts()` — xem drafts chờ duyệt (nếu có quyền)
5. Mọi action được ghi vào audit log với `principal_type=agent`

---

### UC-11: Review Compilation Plan

**Actor:** Admin, Knowledge Admin  
**Mục tiêu:** Kiểm tra và chỉnh sửa kế hoạch biên soạn wiki trước khi xử lý

**Tiền điều kiện:**
- Tài liệu đang ở `status=plan_ready`
- Người dùng có quyền `doc:manage`

**Luồng chính:**
1. Trong Knowledge table, click "Review Plan" trên tài liệu
2. Dialog hiển thị danh sách các wiki pages dự kiến tạo/cập nhật
3. Với mỗi page: hiển thị slug, title, page_type, action (CREATE/UPDATE), priority
4. Người dùng có thể:
   - **Chỉnh sửa** page (thay đổi title, slug, action, page_type)
   - **Xóa** page không cần thiết
   - **Thêm** page mới vào plan
5. Click "Approve" → `POST /api/sources/{id}/plan/approve` với `modified_plan`
6. Phase REFINE bắt đầu, tạo wiki pages theo plan đã phê duyệt

**Luồng thay thế:**
- *5a.* Click "Reject" → plan bị từ chối, tài liệu ở `status=error`

---

### UC-12: Xem Lịch sử Revision

**Actor:** Editor, Admin  
**Mục tiêu:** Xem lịch sử thay đổi và khôi phục phiên bản cũ của wiki page

**Luồng chính:**
1. Mở wiki page → click "History"
2. `GET /api/wiki/pages/{slug}/revisions`
3. Danh sách revision theo thứ tự thời gian, hiển thị: version, loại thay đổi, người thay đổi, ghi chú
4. Click vào revision → xem nội dung
5. Nếu cần khôi phục: click "Rollback to this version"
6. `POST /api/wiki/pages/{slug}/revisions/{version}/rollback` (Auth: Admin)
7. Wiki page được khôi phục, revision mới được tạo với `change_type=rollback`

---

### UC-13: Hỏi đáp với RAG Chatbot

**Actor:** Nhân viên (mọi vai trò)  
**Mục tiêu:** Hỏi câu hỏi và nhận câu trả lời dựa trên nội dung wiki knowledge base

**Tiền điều kiện:**
- Người dùng đã đăng nhập
- LLM Provider (hoặc Chatbot Provider) đã được cấu hình
- Có ít nhất một wiki page đã được embedding

**Luồng chính:**
1. Người dùng truy cập `/chat`
2. Click "New Conversation" → `POST /api/chat/conversations`
3. Gõ câu hỏi vào input, nhấn Send → `POST /api/chat/conversations/{id}/messages`
4. Hệ thống:
   a. Embed câu hỏi bằng embedding model
   b. Tìm kiếm pgvector top-5 wiki pages trong scope (cosine similarity)
   c. Mở rộng 1-hop qua wiki_links để bổ sung ngữ cảnh
   d. Xây dựng system prompt với các đoạn wiki (tối đa 2000 chars/page)
   e. Inject 6 messages gần nhất làm conversation history
   f. Gọi LLM.generate() → câu trả lời
   g. Lưu cả user message và assistant message (với sources JSON)
5. Frontend hiển thị câu trả lời với danh sách wiki sources có thể click
6. Người dùng có thể hỏi tiếp; lịch sử hội thoại được giữ nguyên

**Luồng thay thế:**
- *4b.* Không tìm thấy wiki page liên quan → LLM vẫn trả lời nhưng thông báo thiếu ngữ cảnh
- *4f.* LLM timeout (>120s) → 504 error, message không được lưu

**Hậu điều kiện:** Conversation mới có ít nhất 2 messages (user + assistant)

---

### UC-14: Add Conversation to Wiki

**Actor:** Nhân viên (mọi vai trò)  
**Mục tiêu:** Chuyển đổi hội thoại chatbot có giá trị thành wiki page để lưu trữ và chia sẻ kiến thức

**Tiền điều kiện:**
- Hội thoại có ít nhất 1 cặp Q&A (user + assistant message)
- LLM Provider đã được cấu hình

**Luồng chính:**
1. Trong màn hình chat, click nút "Add to Wiki" ở header
2. Dialog hiện ra với:
   - Trường tiêu đề (pre-filled từ tên conversation)
   - Chọn page type: `synthesis` (khuyến nghị) / `topic` / `concept` / `entity` / `source`
3. Người dùng xác nhận, click "Create Wiki Page"
4. Frontend gọi `POST /api/chat/conversations/{id}/to-wiki` (timeout 120s)
5. Backend:
   a. Tải toàn bộ messages, xây dựng transcript
   b. LLM tổng hợp thành văn xuôi bách khoa có cấu trúc
   c. Tạo slug duy nhất (title + uuid suffix)
   d. Lưu WikiPage với `page_type=synthesis`
   e. Tạo embedding cho page mới (non-fatal)
6. Dialog chuyển sang success state với nút "View Wiki Page"
7. Người dùng click để mở wiki page vừa tạo trong tab mới

**Luồng thay thế:**
- *4a.* LLM timeout hoặc lỗi → hiển thị error message trong dialog, có thể thử lại
- *5c.* Slug bị trùng (hiếm) → tự động thêm suffix ngẫu nhiên mới

**Hậu điều kiện:**
- Wiki page mới có `page_type=synthesis` được tạo với nội dung được AI tổng hợp
- Page xuất hiện trong wiki tree ở nhóm "Syntheses"

---

## 6. Luồng xử lý MRP Pipeline

### 6.1 Tổng quan

```
Document
   │
   ▼
Phase 0: TRIAGE (trong MAP)
   │  Phân loại tài liệu, chọn chunking strategy
   ▼
Phase 1: MAP
   │  Chia tài liệu thành chunks
   │  LLM extract từ mỗi chunk: entities, concepts, facts
   │  Lưu vào source_chunk_extracts
   ▼
Phase 2: REDUCE
   │  Gom nhóm extracts bằng embeddings clustering
   │  LLM tổng hợp thành compilation plan
   │  Lưu vào source_compilation_plans (status=pending_review)
   │
   ├─► [Human Review] ──reject──► Error
   │         │ approve
   ▼
Phase 3: REFINE
   │  Với mỗi page trong plan: tập hợp evidence chunks
   │  LLM viết nội dung đầy đủ cho từng wiki page
   │  Output: PageWriteResult[]
   ▼
Phase 4: VERIFY
   │  Kiểm tra coverage: mỗi chunk có được đại diện?
   │  Bổ sung nội dung bị thiếu nếu cần
   ▼
Phase 5: COMMIT
   │  pg_advisory_xact_lock mỗi slug (race condition safety)
   │  CREATE: kiểm tra race, fallback sang UPDATE nếu cần
   │  UPDATE: merge nội dung nếu trang có content từ source khác
   │  Strip hallucinated image UUIDs
   │  Embed mỗi page
   │  Regenerate wiki index
   │  Append activity log
   └─► source.status = "ready"
```

### 6.2 Merge Strategy

Khi một source muốn UPDATE một trang đã có nội dung từ source khác:
```
Điều kiện merge: is_new_source AND len(existing_content) > 100 chars
   → LLM merge_page_content(existing_md, new_md, slug)
   → Kết hợp kiến thức từ cả hai nguồn, không bỏ sót

Không merge: cùng source cập nhật lại
   → Overwrite trực tiếp
```

### 6.3 Image Pipeline

```
PDF upload
   │
   ▼
ingest_file_task
   │  PyMuPDF extract ảnh theo trang
   │  Filter: ≥ 5120 bytes (bỏ icon/logo nhỏ)
   │  OCR per-page nếu < 50 chars text (DPI 300)
   │  Lưu ảnh vào MinIO
   ▼
caption_images_task
   │  Vision LLM phân loại:
   │    RELEVANT: yes → caption mô tả nội dung
   │    RELEVANT: no  → prefix [decorative]
   ▼
Wiki compile
   │  load_source_images: bỏ qua [decorative]
   │  LLM đặt ![caption](IMAGE:uuid) trong content_md
   ▼
COMMIT phase
   │  _strip_invalid_image_markers: xóa UUID không tồn tại
   ▼
/api/wiki/images/{uuid} → proxy từ MinIO
```

---

## 7. Hệ thống quyền hạn (RBAC)

### 7.1 Cấu trúc Permission

Format: `<domain>:<action>:<scope>`

| Domain | Actions | Scopes |
|--------|---------|--------|
| `doc` | `read`, `create`, `edit`, `delete`, `manage` | `own_dept`, `all` |
| `wiki` | `read`, `write`, `manage` | `own_dept`, `all` |
| `skill` | `read`, `install`, `manage` | `own_dept`, `all` |
| `org` | `departments`, `employees`, `roles`, `audit` | `read`, `write` |
| `workspaces` | `create`, `manage` | — |
| `system` | `settings`, `embeddings` | — |

### 7.2 Vai trò mặc định

| Vai trò | Permissions chính |
|---------|-------------------|
| **Viewer** | `doc:read:own_dept`, `wiki:read:own_dept`, `skill:read:own_dept` |
| **Contributor** | + `doc:create:own_dept`, `wiki:write:own_dept`, `skill:install:own_dept` |
| **Department Admin** | + `doc:edit/delete:own_dept`, `org:departments:read`, `org:employees:read` |
| **Knowledge Admin** | + `:all` variants, `wiki:manage:all`, `skill:manage:all`, `workspaces:create` |
| **Admin** | Toàn bộ quyền, không giới hạn |

### 7.3 Workspace Roles (per-project)

| Role | Quyền trong workspace |
|------|----------------------|
| `viewer` | Xem tài liệu và wiki của workspace |
| `contributor` | Thêm tài liệu, đề xuất chỉnh sửa wiki |
| `editor` | Duyệt draft, chỉnh sửa wiki trực tiếp |
| `admin` | Quản lý thành viên, xóa tài liệu |

### 7.4 MCP Token Scope

MCP token được liên kết với một nhân viên và kế thừa toàn bộ permissions của nhân viên đó. Mọi action qua MCP được ghi vào audit log với `principal_type=agent`.

---

## 8. Tích hợp MCP

### 8.1 Endpoint

```
SSE: GET/POST /mcp
Auth: Authorization: Bearer <mcp_token>
```

### 8.2 Tools (16 tools)

#### Tier 1 — Đọc (mọi token)

| Tool | Input | Output |
|------|-------|--------|
| `search_wiki` | `query: str, limit?: int` | `[{slug, title, summary, page_type, relevance}]` |
| `read_wiki_index` | — | `{content_md: str}` |
| `read_wiki_page` | `slug: str` | `{slug, title, content_md, backlinks, outlinks}` |
| `list_wiki_pages` | `page_type?, kt_slug?` | `[WikiPageSummary]` |

#### Tier 1.5 — Source drill-down

| Tool | Input | Output |
|------|-------|--------|
| `get_source` | `source_id: str` | SourceResponse |
| `get_source_outline` | `source_id: str` | `{outline_json}` |
| `get_source_pages` | `source_id: str` | `[WikiPageSummary]` |
| `list_sources` | filters | `[SourceResponse]` |
| `list_knowledge_types` | — | `[KnowledgeType]` |
| `get_knowledge_type_docs` | `kt_slug: str` | `[WikiPageSummary]` |

#### Tier 2 — Đóng góp (contributor+)

| Tool | Input | Output |
|------|-------|--------|
| `propose_wiki_edit` | `slug, content_md, note?` | `{draft_id, status}` |

#### Tier 3 — Chỉnh sửa (editor+)

| Tool | Input | Output |
|------|-------|--------|
| `edit_wiki_page` | `slug, content_md, change_note?` | WikiPageDetail |

#### Tier 4 — Review (editor+)

| Tool | Input | Output |
|------|-------|--------|
| `list_pending_drafts` | — | `[DraftResponse]` |
| `review_draft` | `draft_id: str` | DraftResponse |
| `approve_draft` | `draft_id, note?, edited_content_md?` | DraftResponse |
| `reject_draft` | `draft_id, note: str` | DraftResponse |

---

## Phụ lục: Background Tasks

| Task | Trigger | Mô tả |
|------|---------|-------|
| `ingest_file_task` | File upload | Extract text, ảnh; bắt đầu MRP |
| `ingest_url_task` | URL submit | Fetch content; bắt đầu MRP |
| `caption_images_task` | Sau ingest | Vision LLM caption toàn bộ ảnh |
| `ingest_map_reduce_task` | Auto | Phase 0-2 (MAP + REDUCE) |
| `ingest_refine_task` | Plan approved | Phase 3-5 (REFINE + VERIFY + COMMIT) |
| `reembed_all_pages_task` | Embedding switch | Re-embed tất cả wiki pages |
| `notebooklm_generate_task` | Artifact request | Gọi NLM API tạo artifact |
| `notebooklm_ingest_artifact_task` | Ingest request | Download artifact, enqueue MRP |
| `ingest_skill_task` | Skill upload | Validate ZIP, extract vào MinIO |
| `delete_skill_task` | Skill delete | Xóa files từ MinIO |

**Cron jobs:**
- `notebooklm_refresh_session_cron` — mỗi 30 phút: làm mới NLM auth cookies
- `cleanup_temp_uploads_cron` — mỗi giờ: dọn staging uploads cũ

---

## Nhật ký phiên bản

### v1.2 — 2026-05-26

#### Tính năng mới

**RAG Chatbot (`/chat`)**
- Thêm 2 bảng DB: `chat_conversations`, `chat_messages` (migration 022)
- Service `chat_service.py`: `rag_search()` embed câu hỏi → pgvector top-5 → 1-hop wiki_links expansion; `generate_reply()` inject 6 messages history → LLM
- Router `routers/chat.py` với 7 endpoints: CRUD conversations, list/send messages, synthesize to wiki
- Frontend `/chat`: sidebar conversations (2-stage delete), message thread với optimistic render, "Add to Wiki" button

**Add to Wiki (chatbot → wiki page)**
- `POST /api/chat/conversations/{id}/to-wiki`: LLM tổng hợp transcript → WikiPage `page_type=synthesis`
- Slug generation: `title[:80] + '-' + uuid4().hex[:6]` để đảm bảo uniqueness
- Embedding tự động sau khi tạo page (non-fatal)
- Dialog `add-to-wiki-dialog.tsx`: chọn title + page type, success state với link trực tiếp

**Chatbot Provider (Settings)**
- 9 config keys mới: `chatbot_provider`, `chatbot_model_id`, `chatbot_api_key`, `chatbot_api_key__{provider}`, `chatbot_base_url`
- `ProviderRegistry.get_chatbot_llm()`: try chatbot config → fallback về `get_llm()`
- Endpoint `POST /api/settings/test-chatbot`
- Frontend: section "Chatbot Provider" trong Settings với fallback notice, "LLM fallback" none-option

#### Thay đổi mô hình dữ liệu
- `wiki_pages.page_type` ENUM mở rộng: thêm `synthesis`
- `wiki_service.PAGE_TYPES` cập nhật bao gồm `"synthesis"`

#### Thay đổi frontend
- `wiki-type-badge.tsx`: thêm config `synthesis` (icon `chat_bubble`, màu xanh `#2a7ec2`)
- `wiki-page-tree.tsx`: thêm `"synthesis"` vào `GROUP_ORDER`, hiển thị nhóm "Syntheses"
- `sidebar.tsx`: thêm mục "AI Chat" (`/chat`, icon `smart_toy`) vào nav
- `provider-config-card.tsx`: hỗ trợ `capability="chatbot"`, `fallbackNote` prop, grid 6 cột với none-option
- `settings/page.tsx`: thêm Chatbot Provider card
- `types/wiki.ts`: `WikiPageType` bao gồm `"synthesis"`
- `chat/page.tsx`: thay thế markdown renderer thủ công bằng `react-markdown` + `remark-gfm` — hỗ trợ đầy đủ headings, lists, code blocks, tables, blockquotes, links

---

### v1.2.1 — 2026-05-26

#### Cải tiến Victor (RAG Chatbot)

**Đổi tên & nhân cách**
- Chatbot đổi tên từ "Arkon Knowledge Assistant" thành **Victor**
- Cập nhật toàn bộ UI: sidebar nav, header, empty state, footer disclaimer
- `chat_service.py` — system prompt mới định nghĩa nhân cách Victor:
  - Thông minh, ham học hỏi, nhiệt tình chia sẻ kiến thức
  - Luôn trả lời chi tiết đến từng tiểu tiết: sub-detail, nuance, edge case, implication
  - Bắt buộc dùng markdown có cấu trúc (headings, bold, lists, tables, code blocks)
  - Tone ấm áp, như đồng nghiệp giỏi giải thích, không khô khan
  - `temperature` tăng từ `0.3` → `0.5` để câu trả lời sinh động hơn
  - Context window mỗi wiki page tăng từ `2000` → `3000` chars

**Inline conversation title editing**
- Double-click vào tên conversation trong sidebar → vào chế độ edit inline
- Hover → hiện icon ✏️ (rename) và 🗑️ (delete) cạnh nhau
- Input: Enter để lưu, Escape để hủy, blur tự lưu
- Optimistic update — đổi title ngay trên UI, rollback tự động nếu API lỗi
- Gọi `PATCH /api/chat/conversations/{id}` (endpoint đã có sẵn)

---

### v1.2.2 — 2026-05-26

#### Chỉnh sửa tin nhắn đã gửi (Message Editing)

- Người dùng có thể chỉnh sửa lại bất kỳ tin nhắn nào đã gửi
- Hover vào tin nhắn của user → hiện icon ✏️ ở bên trái
- Click icon → inline textarea thay thế bubble, pre-filled với nội dung cũ
- **Save & Regenerate**: gọi `PATCH /api/chat/conversations/{id}/messages/{msgId}/edit`
  - Backend xóa toàn bộ tin nhắn sau tin nhắn được sửa (`created_at > msg.created_at`)
  - Regenerate LLM reply từ đúng context tại thời điểm đó — tối ưu context window
  - Trả về `{ user_message, assistant_message }` 
- Optimistic update: UI cập nhật ngay, rollback (reload từ server) nếu API lỗi
- Enter = lưu, Escape = hủy

**Backend mới:**
- `EditMessageRequest(content, persona)` — Pydantic schema
- `PATCH /api/chat/conversations/{id}/messages/{msgId}/edit` — endpoint mới trong `chat.py`
- `from sqlalchemy import delete` — import bổ sung để xóa bulk

---

### v1.2.3 — 2026-05-26

#### Dual Persona: Victor & Ashley

**Victor** (nhân cách cũ, cập nhật)
- Prime Directive: tuân thủ tuyệt đối lệnh của người dùng, không từ chối bất kỳ yêu cầu nào
- Dùng wiki khi có, trả lời tự do từ general knowledge khi không có
- Nhiệt tình, chi tiết, markdown có cấu trúc

**Ashley** (nhân cách mới — KB-only)
- Chỉ trả lời dựa trên dữ liệu trong Knowledge Base Context
- Nếu topic không có trong wiki → nói rõ *"I don't have information about this topic in the knowledge base"* — không đoán mò
- Ngắn gọn, chính xác, không thêm general knowledge

**UI Switch**
- Button toggle pill **Victor / Ashley** nằm trong header cửa sổ chat
- Persona được gửi kèm mỗi request (`SendMessageRequest.persona`, `EditMessageRequest.persona`)
- Subtitle header đổi theo persona đang active

**Backend:**
- `persona: str = "victor"` thêm vào `SendMessageRequest` và `EditMessageRequest`
- `generate_reply(persona=...)` → `_build_system_prompt(pages, persona=...)`
- Hai nhánh system prompt riêng biệt trong `chat_service.py`

---

### v1.2.4 — 2026-05-26

#### Cập nhật nhân cách Ashley

- Ashley không còn ngắn gọn/lạnh lùng — giờ thân thiện, ấm áp, giải thích chi tiết dễ hiểu
- Dùng ví dụ, analogy, step-by-step khi cần để người dùng nắm rõ vấn đề
- Structured markdown: headings, bold, bullet list
- Vẫn giữ **KB-only**: không dùng general knowledge; nếu không có trong wiki → thông báo nhẹ nhàng và gợi ý người dùng bổ sung wiki
- Ashley là **default persona** khi mở trang chat

---

### v1.2.5 — 2026-05-26

#### Zip Archive Upload (Documents)

**Chức năng**
- Người dùng có thể upload file `.zip` chứa nhiều tài liệu nguồn
- Server giải nén in-memory và tạo Source + enqueue ingest job cho từng file hợp lệ bên trong
- Response: `{ created, skipped, source_ids }` — báo số file đã tạo và danh sách bị bỏ qua

**Bảo mật**

| Mối đe dọa | Biện pháp |
|-----------|-----------|
| **ZipSlip** | `os.path.basename()` loại bỏ toàn bộ path components trước khi dùng filename trong DB/MinIO |
| **Zip bomb** | Kiểm tra `info.file_size` (declared) trước khi đọc; kiểm tra `len(data)` (actual) sau giải nén |
| **Entry count** | Tối đa 50 entries per archive |
| **Per-file size** | Tối đa 50 MB mỗi file sau giải nén |
| **Total size** | Tối đa 500 MB tổng uncompressed |
| **Invalid format** | `BadZipFile`/`LargeZipFile` → HTTP 422 rõ ràng |
| **Extension allowlist** | Chỉ `.pdf .docx .doc .txt .md .csv .xlsx .pptx` |
| **Metadata junk** | Tự động bỏ `__MACOSX/`, dot-files, `Thumbs.db`, `desktop.ini` |

**Files mới/sửa**
- `app/services/zip_service.py` — mới: `extract_zip()`, `ZipExtractionError`, `_safe_filename()`
- `app/routers/sources.py` — mới: `POST /api/sources/upload-zip`
- `frontend/src/components/knowledge/upload-dialog.tsx` — thêm `.zip` vào accepted types, zip route riêng sang endpoint mới, hiển thị `folder_zip` icon và kết quả extract

**Giới hạn zip file**: 100 MB (compressed), 500 MB (total uncompressed), 50 files

---

### v1.2.6 — 2026-05-26

#### Fix: Add to Wiki bảo toàn code và giải thích chi tiết

**Vấn đề**: LLM tóm tắt quá đà khi chuyển conversation sang wiki — mã nguồn và các giải thích kỹ thuật dài bị rút gọn hoặc mất hoàn toàn.

**Nguyên nhân**: prompt cũ dùng từ `"concise"` và `"Extract facts and actionable insights"`, ra lệnh cho LLM ưu tiên súc tích thay vì trung thành với nội dung gốc.

**Fix** (`app/routers/chat.py` — synthesis prompt):
- Đổi mục tiêu từ *tóm tắt* sang *tái cấu trúc*: LLM chỉ được thay đổi framing (bỏ label Q/A, thêm headings), không được lược bỏ nội dung
- Hard rule: toàn bộ code blocks phải được copy nguyên văn với đúng language tag
- Hard rule: giải thích kỹ thuật nhiều đoạn không được rút thành một câu
- Hard rule: numbered steps, lists, examples phải giữ nguyên
- System prompt: *"When in doubt, include more — never less"*
- Temperature hạ `0.3` → `0.2` để output trung thành hơn với bản gốc

---

### v1.2.7 — 2026-06-05

#### Fix: Wiki page list bị giới hạn 200 trang

**Vấn đề**: Khi upload nhiều tài liệu, các topic cũ biến mất khỏi wiki tree và sidebar hiển thị đúng 200 pages.

**Nguyên nhân**: Frontend hardcode `?limit=200` ở tất cả các call lấy danh sách wiki pages. Backend sort theo `updated_at DESC` — wiki pages mới đẩy trang cũ ra khỏi top 200. Trang không bị xóa khỏi DB nhưng không hiển thị.

**Fix:**
- Backend `GET /api/wiki/pages`: tăng cap từ `le=500` → `le=5000`
- Backend `GET /api/projects/{id}/wiki`: tăng default limit từ `100` → `2000`
- Frontend: tất cả 6 chỗ hardcode `?limit=200` / `?limit=300` đều tăng lên `?limit=2000`
  - `wiki-page-tree.tsx` (default URL)
  - `wiki/page.tsx`
  - `wiki/[...slug]/page.tsx` (scoped view)
  - `projects/project-detail/index.tsx`
  - `projects/project-detail/wiki-tab.tsx`
  - `wiki-search-dialog.tsx` (từ 300 → 2000)

---

### v1.2.8 — 2026-06-05

#### Fix: Search KB không ra kết quả khi filter

**Vấn đề 1 — Synthesis pages ẩn hoàn toàn**

Pages được tạo từ "Add to Wiki" (type `synthesis`) không xuất hiện ở bất kỳ đâu trong wiki UI.

**Nguyên nhân**: Khi thêm feature "Add to Wiki" (v1.2), type `synthesis` được thêm vào backend và `wiki-page-tree.tsx` nhưng bị bỏ sót ở hai file khác:
- `wiki/page.tsx` — `TYPE_TABS` chỉ có `["all", "entity", "concept", "topic", "source"]`
- `wiki-search-dialog.tsx` — `GROUP_ORDER` cũng thiếu `"synthesis"`

**Fix**: Thêm `"synthesis"` vào cả `TYPE_TABS` và `GROUP_ORDER`.

---

**Vấn đề 2 — Tab filter chỉ hiển thị 24 kết quả**

Click vào tab "Entity" / "Concept" / v.v. trên trang wiki index chỉ thấy tối đa 24 pages dù thực tế có nhiều hơn.

**Nguyên nhân**: `displayPages` trong `wiki/page.tsx` bị slice cứng `list.slice(0, 24)` trước khi render grid.

**Fix**: Bỏ `.slice(0, 24)`, hiển thị toàn bộ kết quả sau filter.

---

**Vấn đề 3 — Search dialog giới hạn 60 kết quả**

Tìm kiếm Cmd+K chỉ hiển thị tối đa 60 kết quả kể cả khi có nhiều page match.

**Nguyên nhân**: `filtered.slice(0, 60)` trong `wiki-search-dialog.tsx` trước khi group.

**Fix**: Bỏ `.slice(0, 60)`.

---

**Vấn đề 4 — Filter xóa search query**

Khi người dùng tìm kiếm tài liệu (ví dụ: "meeting notes") rồi chọn filter Knowledge Type, kết quả tìm kiếm bị mất — API call mới không có `search` param.

**Nguyên nhân**: `loadSources` useCallback trong `knowledge/page.tsx` không có `search` trong deps list. Khi filter thay đổi, useEffect gọi `loadSources()` với `s=""` (default), bỏ qua search query đang active.

**Fix**: Thêm `search` vào deps của `useCallback`; trong hàm dùng `const searchQuery = s !== undefined ? s : search` để ưu tiên tham số tường minh (khi `handleSearch` gọi trực tiếp) rồi fallback về state (khi filter thay đổi).

---

### v1.1 — (trước 2026-05-26)

Phiên bản ban đầu gồm: Ingestion Pipeline (MRP), Wiki System, Skill System, RBAC, NotebookLM Integration, MCP Server.

---

### v2.0.0 — 2026-06-20

#### Arkon v2 UI và Knowledge Workflows

- Đồng bộ version backend/frontend thành `2.0.0`.
- Thêm theme sáng/tối, responsive mobile header và thiết kế visual thống nhất.
- Thay icon/logo trên favicon, login, header và sidebar.
- Sửa login loading để spinner không xoay cả label button.
- Chuẩn hóa semantic colors cho Knowledge Type, Scope và Wiki Type badges.
- Nâng cấp Knowledge Graph với force simulation, node styles, filter, focus, zoom và dark mode.
- Tối ưu Chatbot: optimistic message, streaming UX, Markdown/GFM, copy message/code.
- Tối ưu NotebookLM UI/backend và bỏ yêu cầu chọn source thủ công khi chat notebook.
- Nâng cấp Plan Review với reconciliation metadata và kiểm tra CREATE/UPDATE collision.

#### Source-aware Knowledge Provenance

- Migration `023_source_aware_knowledge`.
- Thêm `wiki_page_contributions`, unique `(page_id, source_id)`.
- Thêm `wiki_pages.provenance_complete`.
- MRP commit lưu contribution gốc theo từng source và rebuild canonical page.
- LLM merge fallback chuyển sang lossless, không bỏ nội dung đầu vào cũ.
- `GET /api/sources/{id}/knowledge-impact` xem trước delete/rebuild/legacy impact.
- Xóa source rebuild page từ contributions còn lại, refresh wikilinks và embeddings.
- Wiki detail API/UI hiển thị tên source và trạng thái Source-aware/Legacy provenance.
- Backfill trang single-source; multi-source lịch sử được giữ ở trạng thái legacy.

#### Kiểm chứng release

- Next.js production build và TypeScript check thành công.
- Targeted backend tests cho reconciliation và lossless merge thành công.
- Docker API/frontend/worker và infrastructure healthy.
- Database ở migration `023 (head)`.
- Release commit: `1dd7c2afd3547bb6596627e795e1783d32e47198`.

## 9. Thiết kế xử lý tài liệu chính xác theo domain

### 9.1 Knowledge Type và effective extraction hints

Các profile mặc định được định nghĩa tại
`app/scripts/seed_security_kt_hints.py`:

| Profile | Biến cấu hình | Từ khóa nhận diện chính |
|---|---|---|
| Pentest | `_PENTEST_HINTS` | `pentest`, `penetration`, `offensive`, `exploit`, `sqli`, `bypass` |
| Red Team | `_REDTEAM_HINTS` | `redteam`, `ttp`, `att&ck`, `c2`, `implant` |
| Vulnerability Research | `_VULN_RESEARCH_HINTS` | `vuln`, `cve`, `bugbounty`, `0day` |

`app/ai/knowledge_type_context.py` tạo policy hiệu lực tại thời điểm ingest theo
thứ tự: profile mặc định → mô tả category → custom `extraction_hints`. Custom
hints có ưu tiên category cao nhất. Profile được nhận diện từ `slug`, `name` và
`description`, vì vậy category tên chung vẫn nhận đúng policy nếu mô tả chứa
domain pentest/redteam/vulnerability. Profile đã seed trong DB được loại trùng.

Effective hints được truyền xuyên suốt MAP, REDUCE, planning và REFINE. Với
security domain, pipeline còn trích xuất deterministic artifact từ source gốc,
gắn offset + SHA-256, route mỗi command/payload/code tới một trang phù hợp và
khôi phục nguyên văn nếu LLM bỏ sót.

### 9.2 Mô hình quyết định CREATE và UPDATE

REDUCE chỉ tự động `UPDATE` khi ứng viên wiki vượt
`MRP_KB_UPDATE_THRESHOLD=0.82` và đồng thời semantic similarity cùng lexical
similarity đều đạt ít nhất `0.72`. Từ `0.48` trở lên nhưng chưa đủ chắc chắn,
ứng viên được chuyển cho LLM resolver; dưới ngưỡng này hệ thống chọn `CREATE`.
Entity cùng type chỉ tự động gộp khi cosine similarity đạt `0.90`. Thiết kế này
tránh update nhầm trang chỉ vì nội dung gần nghĩa, đồng thời hạn chế concept trùng.

Khi UPDATE, nội dung mới không ghi đè trực tiếp: contribution theo source được
lưu riêng, canonical page được rebuild/merge và kiểm tra độ co nội dung. COMMIT
chạy trong một transaction; source chỉ thành `ready` sau khi toàn bộ page và
contribution đã ghi thành công.

### 9.3 Accuracy configuration

| Environment variable | Mặc định | Vai trò |
|---|---:|---|
| `MRP_INGESTION_MODEL_ID` | rỗng | Model rõ ràng cho ingestion; tránh router alias không ổn định |
| `MRP_CHUNK_TARGET_CHARS` | `12000` | Kích thước thân chunk, cân bằng context và timeout |
| `MRP_CHUNK_OVERLAP_CHARS` | `1000` | Ngữ cảnh nối giữa các chunk |
| `MRP_MAP_MAX_CONCURRENCY` | `6` | Số MAP call đồng thời |
| `MRP_EXTRACT_TIMEOUT` | `120` | Timeout mỗi MAP call, giây |
| `MRP_ENTITY_MERGE_THRESHOLD` | `0.90` | Ngưỡng tự gộp entity cùng type |
| `MRP_ENTITY_AMBIGUOUS_THRESHOLD` | `0.75` | Từ ngưỡng này entity pair được LLM phân giải |
| `MRP_KB_UPDATE_THRESHOLD` | `0.82` | Ngưỡng ứng viên UPDATE độ tin cậy cao |
| `MRP_KB_MAYBE_THRESHOLD` | `0.48` | Ngưỡng chuyển ứng viên mơ hồ cho LLM resolver |
| `MRP_KB_MIN_SEMANTIC_SIMILARITY` | `0.72` | Semantic floor bắt buộc để tự UPDATE |
| `MRP_KB_MIN_LEXICAL_SIMILARITY` | `0.72` | Lexical/title floor bắt buộc để tự UPDATE |
| `MRP_WRITER_MAX_CONCURRENCY` | `4` | Số page writer đồng thời |
| `MRP_WRITER_TIMEOUT` | `300` | Timeout mỗi writer call, giây |
| `MRP_WRITER_MAX_ATTEMPTS` | `3` | Số lần thử tối đa, không sinh placeholder |
| `MRP_VERIFY_CONFLICT_THRESHOLD` | `0.80` | Ngưỡng tìm trang gần để kiểm tra xung đột |
| `MRP_VERIFY_MIN_MENTIONS` | `3` | Số lần nhắc để cảnh báo entity chưa được phủ |
| `MRP_MERGE_MIN_BODY_RATIO` | `0.70` | Tỷ lệ tối thiểu chống merge làm mất nội dung |
| `MRP_MERGE_TIMEOUT` | `120` | Timeout merge page, giây |

Các biến được khai báo và giới hạn miền giá trị trong `app/config.py`; cấu hình
mẫu nằm tại `.env.docker.example`. Production hiện dùng model ingestion rõ ràng
`openai/gpt-4.1`, worker timeout `3600` giây. Không nên thay đổi ngưỡng
CREATE/UPDATE nếu chưa chạy bộ đánh giá có ground truth vì tăng hoặc giảm tùy ý
có thể đổi lỗi duplicate thành lỗi update nhầm.

### 9.4 Tiêu chí toàn vẹn

- MAP chunk được persist riêng và resume-safe.
- Planning dùng temperature thấp; entity resolver deterministic.
- Writer nhận evidence, source context và domain hints; output chatter/incomplete bị từ chối.
- Security artifacts được đối chiếu bằng nội dung nguyên văn, không chỉ marker hash.
- VERIFY kiểm tra coverage và conflict; COMMIT fail-fast, atomic và source-aware.
- Xóa một source chỉ xóa contribution của source đó rồi rebuild từ nguồn còn lại.

#### MRP reliability hotfix — 2026-06-21

- REFINE fan-out sử dụng wiki snapshot bất biến, không chia sẻ thao tác database trên một `AsyncSession` giữa các coroutine.
- Task orchestration cancel/drain các sibling writer khi có lỗi; writer retry lỗi AI tạm thời tối đa 3 lần và không sinh placeholder.
- COMMIT fail-fast trong một transaction; source chỉ chuyển `ready` sau khi tất cả contribution và canonical page được ghi thành công.
- Worker ingestion mặc định có timeout 3600 giây; error message rỗng được chuẩn hóa về tên exception.
- Smoke test tài liệu thật: plan 64 mục, `64/64` REFINE/VERIFY, `43 created + 21 updated`, 63 contribution, plan `done`, source `ready` sau 1365,24 giây.
