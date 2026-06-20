# Arkon — Tài liệu Phân tích Thiết kế Hệ thống

> Phiên bản: 0.1.0 · Cập nhật: 2026-05-16

---

## Mục lục

1. [Tổng quan hệ thống](#1-tổng-quan-hệ-thống)
2. [Kiến trúc tổng thể](#2-kiến-trúc-tổng-thể)
3. [Hướng dẫn Build & Triển khai](#3-hướng-dẫn-build--triển-khai)
4. [Backend — FastAPI Application](#4-backend--fastapi-application)
5. [AI Pipeline — MRP (Map-Reduce-Plan)](#5-ai-pipeline--mrp-map-reduce-plan)
6. [Frontend — Next.js Portal](#6-frontend--nextjs-portal)
7. [Cơ sở dữ liệu — PostgreSQL + pgvector](#7-cơ-sở-dữ-liệu--postgresql--pgvector)
8. [Hàng đợi công việc — arq + Redis](#8-hàng-đợi-công-việc--arq--redis)
9. [Lưu trữ tệp — MinIO](#9-lưu-trữ-tệp--minio)
10. [MCP Server — Tích hợp Claude](#10-mcp-server--tích-hợp-claude)
11. [Phân quyền & Bảo mật](#11-phân-quyền--bảo-mật)
12. [Luồng xử lý dữ liệu](#12-luồng-xử-lý-dữ-liệu)
13. [Cấu hình môi trường](#13-cấu-hình-môi-trường)
14. [Mô hình dữ liệu](#14-mô-hình-dữ-liệu)
15. [Nhà cung cấp AI](#15-nhà-cung-cấp-ai)

---

## 1. Tổng quan hệ thống

**Arkon** là nền tảng quản lý tri thức doanh nghiệp (Enterprise Knowledge Base) tích hợp AI. Hệ thống tự động xử lý tài liệu (PDF, DOCX, URL...) thành một wiki tri thức có cấu trúc, và phơi lộ tri thức đó cho các AI client (Claude Desktop, Claude Code) thông qua giao thức MCP (Model Context Protocol).

### Vấn đề giải quyết

| Vấn đề truyền thống | Cách Arkon giải quyết |
|---|---|
| Tài liệu rải rác, không có cấu trúc | LLM tự động biên soạn thành wiki có slug/liên kết |
| RAG chunk-based không hiểu ngữ cảnh | Pipeline MRP phân tích toàn bộ tài liệu, tổng hợp kiến thức |
| Claude không biết nội dung nội bộ | MCP Server cung cấp 14 tools để Claude truy vấn KB |
| Không có kiểm soát quyền truy cập | Dual-realm RBAC: phòng ban + workspace |

### Giá trị cốt lõi

- **Tự lưu trữ (Self-hosted)**: toàn bộ chạy trên Docker, không có dữ liệu gửi ra ngoài (trừ LLM API calls)
- **Provider-agnostic**: đổi nhà cung cấp AI (OpenAI, Google, Anthropic, Ollama) không cần thay code
- **Wiki sống động**: mỗi lần upload tài liệu mới, wiki tự cập nhật/mở rộng theo slug
- **Audit trail đầy đủ**: mọi thao tác đều được ghi log

---

## 2. Kiến trúc tổng thể

```
+---------------------------------------------------------------------+
|                          NGUOI DUNG                                  |
|                                                                      |
|  +------------------+          +----------------------------------+  |
|  |  Trinh duyet Web |          |  Claude Desktop / Claude Code   |  |
|  |  (Admin Portal)  |          |  (MCP Client)                   |  |
|  +--------+---------+          +----------------+----------------+  |
+-----------|---------------------------------------|------------------+
            | HTTP :3119                            | HTTP /mcp
            v                                       v
+---------------------------------------------------------------------+
|                        Docker Network                                |
|                                                                      |
|  +---------------------+      +----------------------------------+  |
|  |  Frontend (Next.js)  |      |        API (FastAPI)             |  |
|  |  Port: 3119          |----->|        Port: 5055                |  |
|  |  Next.js 15          |      |  +---------+ +---------------+  |  |
|  |  React 19            |      |  | REST API| |  MCP Server   |  |  |
|  +---------------------+      |  | /api/*  | |  /mcp         |  |  |
|                                |  +----+----+ +-------+-------+  |  |
|                                |       |              |           |  |
|  +---------------------+      |  +----v--------------v--------+  |  |
|  |  Worker (arq)        |      |  |     Services Layer         |  |  |
|  |  - ingest_file       |      |  |  auth / wiki / kb / perm   |  |  |
|  |  - ingest_mrp        |      |  +----------------------------+  |  |
|  |  - ingest_refine     |      +----------------------------------+  |
|  |  - caption_images    |                      |                     |
|  |  - reembed_all       |      +---------------v-----------------+  |
|  +----------+-----------+      |           Data Layer             |  |
|             |                  |  +----------+ +------+ +------+  |  |
|             | arq jobs         |  |PostgreSQL| |Redis | |MinIO |  |  |
|             +----------------->|  |+pgvector | |(Queue)| |(S3) |  |  |
|                                |  +----------+ +------+ +------+  |  |
|                                +---------------------------------+  |  |
+---------------------------------------------------------------------+
```

### Cac dich vu Docker

| Container | Image | Cong ngoai | Vai tro |
|---|---|---|---|
| `arkon_postgres` | pgvector/pgvector:pg16 | — | Luu tru toan bo du lieu + vector embeddings |
| `arkon_redis` | redis:7-alpine | — | Hang doi cong viec (arq) |
| `arkon_minio` | minio/minio:latest | 9002 (API), 9003 (Console) | Luu tep goc, anh, skill packages |
| `arkon_api` | arkon-backend:latest | 5055 | FastAPI REST + MCP server |
| `arkon_worker` | arkon-backend:latest | — | Xu ly ingestion + MRP pipeline |
| `arkon_worker_skills` | arkon-backend:latest | — | Xu ly skill packages rieng biet |
| `arkon_frontend` | arkon-frontend:latest | 3119 | Next.js UI |

---

## 3. Huong dan Build & Trien khai

### 3.1 Yeu cau he thong

| Thanh phan | Yeu cau toi thieu |
|---|---|
| Docker Engine | >= 24.0 voi Docker Compose v2 |
| RAM | 4 GB (8 GB khuyen nghi cho Ollama) |
| CPU | 2 cores (4+ neu dung mo hinh local) |
| O dia | 20 GB du lieu + dung luong tai lieu |

### 3.2 Cau truc thu muc

```
arkon/
├── app/                    # Backend Python (FastAPI)
│   ├── ai/                 # AI providers + MRP pipeline
│   │   ├── mrp/            # Map-Reduce-Plan phases (mapper, reducer, writer, verifier, merger)
│   │   └── providers/      # Google, OpenAI, Anthropic, Ollama
│   ├── database/           # ORM models + session factory
│   ├── mcp/                # MCP server + 14 tools
│   ├── routers/            # 13 API router files
│   ├── services/           # Business logic (wiki, kb, auth, perm, storage, embed)
│   ├── scripts/            # Startup scripts (seed_skills)
│   ├── utils/              # Utilities (progress tracker, text cleaning)
│   ├── config.py           # Pydantic Settings tu env variables
│   ├── main.py             # FastAPI entry point
│   └── worker.py           # arq background task definitions
├── frontend/               # Next.js 15 (TypeScript)
│   ├── src/
│   │   ├── app/            # 19 trang (App Router)
│   │   ├── components/     # ~80 React components
│   │   └── lib/            # api.ts, auth.tsx, hooks
│   ├── Dockerfile
│   └── next.config.ts
├── alembic/                # 17 database migrations
├── skills/                 # Built-in skill packages (.zip)
├── Dockerfile              # Backend container (Python 3.12-slim)
├── docker-compose.yml      # 7 services
├── entrypoint.sh           # Tu dong migrate + seed khi container start
├── pyproject.toml          # 28 Python dependencies
└── .env.docker             # Environment variables
```

### 3.3 Cau hinh moi truong (.env.docker)

Tao tu `.env.docker.example`:

```bash
# Database
POSTGRES_USER=arkon
POSTGRES_PASSWORD=<mat_khau_manh>
POSTGRES_DB=arkon

# Auth — BAT BUOC doi truoc khi deploy len production
SECRET_KEY=<64-char random hex>
DEFAULT_ADMIN_EMAIL=admin@company.com
DEFAULT_ADMIN_PASSWORD=<mat_khau_admin>

# MinIO Storage
MINIO_ACCESS_KEY=<access_key>
MINIO_SECRET_KEY=<secret_key>
MINIO_ENDPOINT=minio:9000           # Docker internal hostname
MINIO_PUBLIC_ENDPOINT=localhost:9000

# Redis
REDIS_PASSWORD=<mat_khau_redis>

# CORS (danh sach domain duoc phep truy cap)
CORS_ORIGINS=http://localhost:3119,http://100.x.x.x:3119

# Frontend
NEXT_PUBLIC_API_URL=              # De trong = dung relative URL (khuyen nghi)
INTERNAL_API_URL=http://api:5055  # Next.js proxy toi API (build-time ARG)

# MRP Pipeline
MRP_AUTO_APPROVE_PLAN=false       # true = bo qua buoc human review
```

> **NEXT_PUBLIC_API_URL de trong**: Frontend dung `/api/*` relative URL de hoat dong tu moi IP/hostname ma khong can rebuild image. Neu dat gia tri cu the (vd: `http://192.168.1.100:5055`), frontend se bi loi khi truy cap tu IP khac.

### 3.4 Build lan dau

```bash
cd E:\AI-CLAUDE\arkon

# Build backend image (dung chung cho api, worker, worker_skills)
docker compose build api

# Build frontend image (bake INTERNAL_API_URL vao luc build)
docker compose build frontend

# Khoi dong toan bo stack
docker compose up -d

# Theo doi logs de xac nhan khoi dong thanh cong
docker compose logs -f api
```

**Qua trinh khoi dong tu dong:**

```
entrypoint.sh chay moi khi container start:
  1. alembic upgrade head     # Ap dung 17 migrations
  2. seed_skills.py           # Nap built-in skills

FastAPI lifespan (main.py):
  3. ensure_bucket()          # Tao MinIO bucket "arkon" neu chua co
  4. seed_default_admin()     # Tao admin mac dinh tu env neu chua co
  5. seed_builtin_skills()    # Idempotent — khong tao trung
  6. Warning neu SECRET_KEY hoac password chua doi
```

### 3.5 Update code sau khi chinh sua

```bash
# Chi thay doi backend:
docker compose build api
docker compose up -d api worker worker_skills

# Chi thay doi frontend:
docker compose build frontend
docker compose up -d frontend

# Thay doi ca hai:
docker compose build api frontend && docker compose up -d
```

### 3.6 Kiem tra suc khoe he thong

```bash
curl http://localhost:5055/health

# Ket qua mong doi:
{
  "status": "healthy",
  "services": {
    "database": "healthy",
    "redis": "healthy",
    "minio": "healthy"
  }
}
```

### 3.7 Backup

```bash
# Backup PostgreSQL
docker exec arkon_postgres pg_dump -U arkon arkon > backup_$(date +%Y%m%d).sql

# Restore PostgreSQL
docker exec -i arkon_postgres psql -U arkon arkon < backup_20260516.sql

# MinIO data (bind mount F:\arkon-data\minio\) — copy truc tiep
# Khong can container de backup MinIO khi dung bind mount
```

---

## 4. Backend — FastAPI Application

### 4.1 Khoi tao ung dung (app/main.py)

```python
# Thu tu khoi tao:
1. Tao MCP server (FastMCP) — mount vao /mcp
2. FastAPI app voi lifespan context manager
3. CORS middleware (whitelist tu CORS_ORIGINS env)
4. Mount MCP: app.mount("/mcp", mcp_http_app)
5. Include 13 routers tai /api/*
6. Health endpoints: /health va /api/health
```

Hai MCP server chay **ben trong cung mot process** FastAPI — khong phai service rieng.

### 4.2 Configuration (app/config.py)

Pydantic Settings tu doc tu environment variables:

| Setting | Key env | Ghi chu |
|---|---|---|
| Database URL | `DATABASE_URL` | postgresql+asyncpg://... |
| JWT Secret | `SECRET_KEY` | 256-bit random hex |
| Redis | `REDIS_HOST/PORT/PASSWORD/DB` | Ket noi den Redis |
| MinIO | `MINIO_ENDPOINT/ACCESS_KEY/...` | Ket noi MinIO |
| CORS | `CORS_ORIGINS` | Comma-separated whitelist |
| MRP auto-approve | `MRP_AUTO_APPROVE_PLAN` | Bo qua human review |
| Admin defaults | `DEFAULT_ADMIN_EMAIL/PASSWORD` | Seed lan dau |

### 4.3 API Routers (13 modules)

| Router file | Cac endpoint chinh | Ghi chu |
|---|---|---|
| `auth.py` | POST /login, /logout | JWT-based |
| `sources.py` | CRUD, /upload, /approve, /status | Progress polling |
| `wiki.py` | CRUD wiki pages, search, graph | Semantic + full-text |
| `wiki_drafts.py` | Propose/review/approve drafts | Contribution workflow |
| `wiki_images.py` | Resolve + proxy images | JWT via ?token= |
| `skills.py` | Browse/manage, versioning | Skill marketplace |
| `skill_contributions.py` | PR-style skill updates | Review workflow |
| `projects.py` | Workspace CRUD + membership | Scoped access |
| `rbac.py` | Employee/department CRUD | User management |
| `roles.py` | Custom role + permissions | RBAC management |
| `knowledge_types.py` | Taxonomy CRUD | Document classification |
| `admin_settings.py` | AI provider config | LLM/embedding/vision |
| `admin_embeddings.py` | Embedding model migration | Re-embed jobs |
| `audit.py` | Audit log queries | Compliance |

### 4.4 Services Layer

**`kb_service.py`** — Ingestion pipeline:
- Extract text: PDF (PyMuPDF per-page), DOCX (python-docx/mammoth), TXT/MD, URL (content-core)
- Strip null bytes (`\x00`) — tranh loi PostgreSQL CharacterNotInRepertoire
- Xoa SourceImage cu truoc re-ingest — tranh UniqueViolation

**`wiki_service.py`** — Wiki CRUD:
- `apply_create()` / `apply_update()` — atomic upsert voi PostgreSQL advisory lock theo slug
- `extract_wikilinks()` — parse `[[slug]]` patterns tu markdown
- `refresh_links()` — rebuild WikiLink graph sau moi commit

**`embedding_storage.py`** — Da chieu vector:
- Ho tro 768d, 1024d, 1536d, 3072d (halfvec cho 3072d)
- `upsert_page_embedding()` — skip re-embed neu content_hash khong doi (SHA-256)
- `search_similar()` — cosine similarity voi pgvector HNSW index

**`storage_service.py`** — MinIO wrapper: upload, download, presigned URLs, prefix ops

**`permission_engine.py`** — Dual-realm RBAC (xem Phan 11)

**`auth_service.py`** — JWT:
- bcrypt password hashing
- JWT 24h: `sub`=employee_id, `role`, `name`
- `get_current_user` — Authorization header
- `get_current_user_image` — chap nhan header HOAC `?token=` (cho `<img>` tags)

---

## 5. AI Pipeline — MRP (Map-Reduce-Plan)

Day la **trai tim cua Arkon**. Khac voi RAG truyen thong (chunk → embed → retrieve), MRP tong hop kien thuc tu toan bo tai lieu.

### 5.1 So sanh voi RAG truyen thong

```
RAG truyen thong:                  Arkon MRP Pipeline:
─────────────────                  ─────────────────────────────────────
Tai lieu → chia chunk nho          Tai lieu → phan tich toan bo
Chunks → embeddings vector         → Trich xuat entities/facts co cau truc
Query → Tim top-K chunks tuong tu  → Tong hop + phat hien mau thuan
Chunks → LLM → Tra loi             → Viet wiki pages co slug
                                   → Cap nhat/mo rong KB hien co

Uu diem RAG: Nhanh, don gian       Uu diem MRP: Hieu ngu canh toan bo
Nhuoc diem: Mat ngu canh lien doan  tai lieu, wiki nhat quan va co cau truc
```

### 5.2 6 Phases cua Pipeline

**Phase 0 — TRIAGE** (`classify_strategy` trong `mapper.py`)
```
Input:  full_text (string) + outline_json (TOC)
Logic:  Chon chien luoc dua tren do dai full_text:
          single_pass:   < 10.000 chars  → 1 LLM call duy nhat
          standard:      10k - 200k      → Chia chunks, xu ly song song
          hierarchical:  > 200.000 chars → Chunks theo chapter, merge dan
Output: Source.pipeline_strategy duoc cap nhat
```

**Phase 1 — MAP** (`run_map_phase` trong `mapper.py`)
```
Input:  full_text chia thanh chunks ~20.000 chars
        Overlap 1.000 chars giu ngu canh giua cac chunk
        Separator "[...context from previous section...]"
Logic:  Goi LLM song song (toi da 6 concurrent, timeout 120s/call)
        Moi LLM call trich xuat:
          - Entities (cong ty, nguoi, san pham, khai niem)
          - Facts (thong tin co the)
          - Procedures (quy trinh, cac buoc)
          - Policies (quy dinh, chinh sach)
        Moi piece duoc gan slug (concept/, entity/, topic/, source/)
Output: SourceChunkExtract rows trong DB
        Resume-safe: neu crash, chi lam lai chunks chua xong
```

**Phase 2 — REDUCE** (`run_reduce_phase` trong `reducer.py`)
```
Input:  Tat ca SourceChunkExtract cua source
Logic:  Dedup entities xuat hien o nhieu chunks
        Reconcile voi wiki pages hien co trong DB
        Phat hien xung dot (same entity, thong tin khac nhau)
        Tao danh sach operations: create / update cho tung page
Output: SourceCompilationPlan (JSONB)
        Neu MRP_AUTO_APPROVE_PLAN=true → tu dong enqueue ingest_refine_task
        Neu false → source.status = "plan_review" → cho human duyet
```

**Phase 3 — REFINE** (`run_refine_phase` trong `writer.py`)
```
Input:  SourceCompilationPlan
Logic:  Voi moi page trong plan:
          LLM viet noi dung markdown hoan chinh
          Tich hop image captions vao dung vi tri
          Tao wikilinks [[slug]] sang cac pages lien quan
Output: List[PageWriteResult] (slug + markdown content)
```

**Phase 4 — VERIFY** (`run_verify_phase` trong `verifier.py`)
```
Input:  PageWriteResult list + source goc
Logic:  Fact-check: so sanh noi dung voi source goc
        Phat hien hallucination hoac thong tin bia dat
        Danh dau cac doan can review
Output: Verified PageWriteResult list
```

**Phase 5 — COMMIT** (`run_commit_phase` trong `pipeline.py`)
```
Input:  Verified pages
Logic:  Voi moi page:
          - PostgreSQL advisory lock theo slug (tranh race condition)
          - apply_create() hoac apply_update() (idempotent upsert)
          - merge_page_content() neu xung dot version (LLM merge)
        Commit tat ca trong 1 transaction
        Upsert embeddings (theo content_hash, skip neu khong doi)
        Refresh WikiLink graph
Output: source.status = "ready"
        Wiki pages published va co the tim kiem
```

### 5.3 Slug System — Co che tu cap nhat wiki

Slug la identifier URL-safe duy nhat cho moi wiki page.

```
Convention:
  concept/<ten-khai-niem>     # Khai niem, dinh nghia ky thuat
  entity/<ten-thuc-the>       # Cong ty, nguoi, san pham cu the
  topic/<chu-de>              # Chu de, linh vuc, domain
  source/<tieu-de-tai-lieu>   # Trang tong hop tu 1 tai lieu cu the

Khi upload tai lieu moi ve cung chu de:
  1. Phase 2 (REDUCE) phat hien slug da ton tai trong DB
  2. Dua vao plan voi operation "update" thay vi "create"
  3. Phase 5 (COMMIT) merge noi dung moi vao page hien co
  Ket qua: wiki duoc lam giau them, KHONG tao trang trung
```

### 5.4 Image Processing (Song song voi MRP)

```
ingest_file_task
  |---> enqueue ingest_map_reduce_task   # MRP pipeline
  +---> enqueue caption_images_task      # Xu ly anh SONG SONG

caption_images_task:
  Voi moi SourceImage:
    Vision LLM → generate caption text
    Save SourceImage.caption

Phase 3 (REFINE):
  Doc SourceImage.caption
  Inline vao markdown tai dung vi tri
  Tao image://<uuid> reference trong content_md
```

---

## 6. Frontend — Next.js Portal

### 6.1 Kien truc Frontend

```
frontend/src/
├── app/                       # Next.js App Router
│   ├── layout.tsx             # Root: fonts, AuthProvider, TooltipProvider
│   ├── login/page.tsx         # Trang dang nhap
│   └── (portal)/              # Protected route group (yeu cau JWT)
│       ├── layout.tsx         # Sidebar + Header
│       ├── page.tsx           # Dashboard
│       ├── knowledge/         # Upload + quan ly tai lieu
│       ├── wiki/              # Wiki browser + graph
│       │   └── [...slug]/     # Dynamic wiki page
│       ├── skills/            # Skill marketplace
│       ├── projects/          # Workspace listing
│       ├── workspaces/[id]/   # Workspace hub (sources/members/wiki)
│       ├── admin/             # Admin tools
│       ├── departments/       # Department RBAC
│       ├── employees/         # User management
│       ├── roles/             # Custom role editor
│       ├── audit/             # Audit log
│       ├── profile/           # User profile + MCP token
│       └── settings/          # AI provider config (admin only)
├── components/                # ~80 React components theo domain
│   ├── shared/                # page-header, empty-state, stat-card
│   ├── knowledge-table/       # Source list + plan review dialog
│   ├── wiki-*/                # Wiki viewer, editor, graph (D3.js)
│   ├── settings/              # provider-config-card, embedding-settings-card
│   └── ui/                    # Shadcn/ui (25 base components)
└── lib/
    ├── api.ts                 # HTTP client (fetch wrapper + auth injection)
    ├── auth.tsx               # AuthContext + useAuth hook
    └── hooks/
        └── use-image-resolver.ts  # Resolve image://<uuid> → proxy URL + ?token=
```

### 6.2 API Proxy Architecture

```
Browser → Next.js (:3119)
            └──> /api/* (rewrites trong next.config.ts)
                 └──> INTERNAL_API_URL/api/*
                      INTERNAL_API_URL = http://api:5055 (Docker internal)
                      GIA TRI NAY DUOC BAKE VAO LUC BUILD (build ARG)

Ngoai le — File upload bypass proxy:
  apiUpload() → http://<window.location.hostname>:5055/api/sources/upload
  Ly do: Next.js body parser gioi han 10MB, files co the lon hon
```

### 6.3 Authentication Flow

```
1. User → POST /api/login → JWT token (expire 24h)
2. Token luu: localStorage("arkon_token")
3. Moi request api() → inject: Authorization: Bearer <token>
4. <img> tags → /api/wiki/images/<uuid>?token=<jwt>
   Backend get_current_user_image: chap nhan header HOAC ?token= param
5. AuthContext: tu dong redirect /login neu nhan 401
```

---

## 7. Co so du lieu — PostgreSQL + pgvector

### 7.1 Tong quan Schema

```
NHOM TAI LIEU
  sources
    |-- source_departments (M2M → departments, kiem soat visibility)
    |-- source_images       (anh extracted tu documents)
    |-- source_chunk_extracts    (Phase 1 MAP output)
    +-- source_compilation_plans (Phase 2 REDUCE output)

NHOM WIKI
  wiki_pages
    |-- wiki_page_contributions (noi dung thuoc tung source)
    |-- wiki_links            (graph edges: slug A → slug B)
    |-- wiki_page_drafts      (contribution proposals cho review)
    |-- wiki_page_revisions   (version history sau moi update)
    |-- wiki_page_embeddings_768   (VECTOR 768d)
    |-- wiki_page_embeddings_1024  (VECTOR 1024d)
    |-- wiki_page_embeddings_1536  (VECTOR 1536d)
    +-- wiki_page_embeddings_3072  (HALFVEC 3072d)

NHOM PHAN QUYEN
  employees → departments (department_id)
  employees → roles (custom_role_id)
  roles → JSONB permissions (list of "resource:action:scope" strings)
  project_members (employees × projects, voi workspace_role)

NHOM SKILLS
  skills → skill_versions (MinIO storage)
  skills → skill_departments (M2M)
  skill_contributions (PR workflow: draft→pending→approved/rejected)

NHOM HE THONG
  app_config     (key-value AI provider settings)
  knowledge_types (taxonomy cho document classification)
  audit_logs      (immutable audit trail)
  embedding_jobs  (tracking re-embedding migrations)
  notes           (lightweight personal notes)
```

### 7.2 Bang quan trong — chi tiet

**`sources`** — Tai lieu goc:

| Cot | Kieu | Mo ta |
|---|---|---|
| `id` | UUID PK | Primary key |
| `title` | VARCHAR(500) | Tieu de |
| `full_text` | TEXT | Noi dung text da extract |
| `source_type` | VARCHAR(50) | "file" hoac "url" |
| `scope_type` | VARCHAR(20) | "global" hoac "project" |
| `scope_id` | UUID nullable | project.id khi scope=project |
| `knowledge_type_id` | UUID FK | Taxonomy |
| `status` | VARCHAR(50) | pending/processing/plan_review/ready/error |
| `progress` | INTEGER | 0-100% |
| `pipeline_strategy` | VARCHAR(20) | single_pass/standard/hierarchical |
| `pipeline_phase` | VARCHAR(30) | map/reduce/plan_review/refine/verify/commit |
| `outline_json` | JSONB | TOC tree |
| `minio_key` | VARCHAR(500) | S3 path trong MinIO |

**`wiki_pages`** — Noi dung wiki:

| Cot | Kieu | Mo ta |
|---|---|---|
| `id` | UUID PK | Primary key |
| `slug` | VARCHAR(500) UNIQUE | URL-safe: "concept/jwt-auth" |
| `title` | VARCHAR(500) | Tieu de trang |
| `content_md` | TEXT | Noi dung Markdown |
| `scope_type` | VARCHAR(20) | "global" hoac "project" |
| `knowledge_type_id` | UUID FK | Taxonomy |
| `version` | INTEGER | Tang dan moi lan update |
| `source_ids` | UUID[] | Chi muc nhanh cac source dong gop |
| `provenance_complete` | BOOLEAN | Co the rebuild chinh xac tu contributions |

**`wiki_page_contributions`** — Source-aware knowledge provenance:

| Cot | Kieu | Mo ta |
|---|---|---|
| `id` | UUID PK | Primary key |
| `page_id` | UUID FK | Wiki page canonical |
| `source_id` | UUID FK | Source so huu contribution |
| `content_md` | TEXT | Noi dung source dong gop cho page |
| `summary` | TEXT | Tom tat contribution |
| `source_title` | VARCHAR(500) | Snapshot ten source |
| `knowledge_type_slug` | VARCHAR(200) | Taxonomy cua contribution |

Unique constraint `(page_id, source_id)` dam bao re-ingest cung source se update
contribution thay vi tao ban trung lap.

**`app_config`** — AI provider settings (key-value):

| Key | Mo ta |
|---|---|
| `llm_provider` | google/openai/anthropic/ollama |
| `llm_model_id` | e.g. gemini-2.5-pro, gpt-4o |
| `llm_api_key` | API key (mask khi read) |
| `llm_base_url` | Cho Ollama: http://host.docker.internal:11434/v1 |
| `vision_provider/model/key/url` | Tuong tu cho vision |
| `embedding_provider` | Provider for embeddings |
| `embedding_model_id` | e.g. text-embedding-3-large |
| `embedding_api_key__<provider>` | Key rieng theo provider |

**`employees`** — Nguoi dung:

| Cot | Kieu | Mo ta |
|---|---|---|
| `id` | UUID PK | |
| `email` | VARCHAR UNIQUE | |
| `password_hash` | VARCHAR | bcrypt hash |
| `role` | VARCHAR(20) | "admin" hoac "employee" |
| `department_id` | UUID FK | |
| `custom_role_id` | UUID FK | RBAC permissions |
| `is_active` | BOOLEAN | Tai khoan co hoat dong khong |
| `mcp_token` | VARCHAR | Token cho MCP access |
| `mcp_token_scopes` | JSONB | allowed_source_ids, allowed_knowledge_types |

### 7.3 Source Status State Machine

```
pending
  └──> processing (ingest_file_task bat dau)
         |──> plan_review ──[human approve]──> refining ──> ready
         +──> error (bat ky phase nao that bai)
```

### 7.4 Indexes quan trong

```sql
-- Full-text search
CREATE INDEX ON sources USING GIN(to_tsvector('english', full_text));

-- Semantic search (pgvector HNSW — nhanh cho approximate nearest neighbor)
CREATE INDEX ON wiki_page_embeddings_768 USING hnsw(embedding vector_cosine_ops);
CREATE INDEX ON wiki_page_embeddings_3072 USING hnsw(embedding halfvec_cosine_ops);

-- Audit log performance
CREATE INDEX ON audit_logs(principal_id, created_at DESC);
CREATE INDEX ON audit_logs(resource_type, resource_id);
```

### 7.5 Migration files (theo thu tu thoi gian)

```
001  initial_schema          Sources, notes, wiki pages, basic employees
002  rbac                    Roles, departments, custom role M2M
002  add_progress            Source.progress + progress_message
003  add_fulltext_index      GIN index cho full-text search
004  add_projects            Workspace/project tables
005  add_custom_roles        Custom role assignment
006  wiki_pivot              Wiki layer: SourceChunkExtract, CompilationPlan
007  scope_rbac              Source scoping (global vs project)
008  workspace_type          Project type enum
009  drop_contacts           Schema cleanup
010  workspace_wiki_scope    Wiki page scoping
011  permission_v2           Permission format: resource:action:scope
012  add_skill               Skills, versions, contributions
013  wiki_user_contributions WikiPageDraft (contribution proposals)
014  wiki_draft_revision     Review workflow + WikiPageRevision history
015  multi_dim_embeddings    4 embedding tables (768/1024/1536/3072d)
016  source_images           SourceImage (anh extracted tu documents)
017  skill_contributions     Contribution workflow cho skills
018  drop_skill_description  Schema cleanup
019  skill_is_system         Danh dau built-in skill
020  mrp_pipeline            MRP state va compilation tables
021  notebooklm              NotebookLM notebooks, sources, artifacts
022  chat                    Chat conversations va messages
023  source_aware_knowledge  Contribution provenance + legacy backfill
```

### 7.6 Source-aware rebuild

`wiki_pages.content_md` la ban canonical de doc va search. Noi dung co quyen so
huu theo source nam trong `wiki_page_contributions`. Khi source bi xoa:

1. Xoa contribution cua source.
2. Neu khong con contribution, xoa wiki page.
3. Neu con contribution, merge lai canonical content.
4. Refresh `wiki_links` va vector embedding.
5. Trang multi-source cu khong the backfill chinh xac duoc danh dau legacy.

---

## 8. Hang doi cong viec — arq + Redis

### 8.1 Tong quan

arq la async job queue tren Redis, thuan asyncio (khong dung multiprocessing nhu Celery).

```
FastAPI (producer):
  await pool.enqueue_job("ingest_file_task", source_id)
    └──> Ghi vao Redis list: arq:queue:default

arq Worker (consumer):
  Poll Redis lien tuc
  Lay job → chay async task function
  Cap nhat DB: source.status, source.progress
  Ghi ket qua vao Redis hash
```

### 8.2 Hai worker instances

**`WorkerSettings`** (container `arkon_worker`):
- Max 3 jobs chay song song, timeout 30 phut/job
- Cron: `cleanup_temp_uploads_cron` moi 1 gio

| Task | Mo ta |
|---|---|
| `ingest_file_task` | Download MinIO → extract text → outline → enqueue MRP |
| `ingest_url_task` | Tuong tu nhung tu URL |
| `ingest_map_reduce_task` | Phase 0-2 (TRIAGE + MAP + REDUCE) |
| `ingest_refine_task` | Phase 3-5 (REFINE + VERIFY + COMMIT) |
| `caption_images_task` | Caption anh bang Vision LLM (song song voi MRP) |
| `reembed_all_pages_task` | Migration embedding model (batch 50 pages) |

**`SkillWorkerSettings`** (container `arkon_worker_skills`):
- Queue rieng de skill processing khong block ingestion

| Task | Mo ta |
|---|---|
| `ingest_skill_task` | Unzip + validate + store skill package trong MinIO |
| `delete_skill_task` | Xoa skill va toan bo files tu MinIO |

### 8.3 Progress Tracking thoi gian thuc

```python
tracker = ProgressTracker(source_id)

# Worker cap nhat DB moi buoc:
await tracker.update(10, "Extracting text...")
await tracker.update(30, "Running MAP phase...")
await tracker.update(60, "Running REDUCE phase...")
await tracker.update(90, "Committing wiki pages...")

# Frontend poll /api/sources/{id} moi 2s → hien thi progress bar
```

---

## 9. Luu tru tep — MinIO

### 9.1 Cau truc bucket "arkon"

```
arkon/
├── sources/{source_id}/{filename}           # Tep goc: PDF, DOCX...
├── source-images/{source_id}/{image_id}.ext # Anh extracted tu document
├── skills/{skill_id}/versions/{n}/**        # Skill package files
└── skill-contributions/{contribution_id}.zip # Pending skill PRs
```

### 9.2 Tai sao khong dung presigned URL truc tiep

**Van de**: MinIO presigned URL bake hostname vao HMAC signature.
- URL tao trong container: `http://minio:9000/...?X-Amz-Signature=abc`
- Browser truy cap tu: `localhost` — URL KHONG HOP LE (hostname khac)

**Giai phap — Image Proxy**:
```
Browser: <img src="/api/wiki/images/{uuid}?token={jwt}">
  └──> GET /api/wiki/images/{uuid}?token={jwt}
         └──> get_current_user_image() xac thuc JWT (header HOAC ?token=)
         └──> can_access_document() kiem tra quyen
         └──> storage_service.download_file(minio_key) — Docker internal
         └──> StreamingResponse(media_type=content_type)
         └──> Cache-Control: private, max-age=3600
```

### 9.3 Du lieu luu tru (Bind Mounts)

```
F:\arkon-data\
├── postgres\   # PostgreSQL data files (auto-managed)
├── redis\      # Redis RDB persistence files
└── minio\      # MinIO objects (tep that su)

MinIO khong co gioi han dung luong — phu thuoc o dia host.
Hien tai: dung luong F:\ la gioi han thuc te.
```

---

## 10. MCP Server — Tich hop Claude

### 10.1 Kien truc MCP

```
Claude Desktop/Code (MCP Client)
  └──> HTTP Streamable: http://localhost:5055/mcp
         └──> FastMCP server (app/mcp/server.py)
                Duoc mount TRONG FastAPI process:
                app.mount("/mcp", mcp_http_app)
                
                Khi Claude goi tool:
                  1. Verify MCP token (mcp_auth_service.py)
                  2. Check scopes (allowed_source_ids/knowledge_types)
                  3. Execute tool (app/mcp/tools.py)
                  4. Filter results theo scopes
                  5. Return response
```

### 10.2 MCP Authentication

```
Admin Portal → Tao MCP token cho employee
  → employees.mcp_token (hash)
  → employees.mcp_token_scopes: {
      "allowed_source_ids": ["uuid1", "uuid2"],
      "allowed_knowledge_types": ["security", "hr"]
    }

Claude config (claude_desktop_config.json):
  {
    "mcpServers": {
      "arkon": {
        "url": "http://localhost:5055/mcp",
        "headers": { "Authorization": "Bearer <mcp_token>" }
      }
    }
  }
```

### 10.3 14 MCP Tools

| Tool | Mo ta |
|---|---|
| `search_wiki` | Full-text + semantic search trong KB |
| `read_wiki_page` | Doc trang wiki theo slug |
| `list_wiki_pages` | Duyet catalog voi bo loc |
| `read_wiki_index` | Doc trang index (_index slug) |
| `get_wiki_backlinks` | Lien ket nguoc vao mot trang |
| `propose_wiki_edit` | De xuat chinh sua (tao draft) |
| `edit_wiki_page` | Ap dung chinh sua da duoc duyet |
| `get_source_outline` | Muc luc tai lieu goc |
| `get_source_pages` | Doc noi dung goc theo trang/offset |
| `list_sources` | Danh sach tai lieu |
| `list_knowledge_types` | Danh sach taxonomy |
| `get_knowledge_type_docs` | Tai lieu theo knowledge type |

---

## 11. Phan quyen & Bao mat

### 11.1 Dual-Realm RBAC

Arkon dung mo hinh phan quyen hai tang doc lap:

**Global Realm** — Dua tren Custom Role:
```
Custom Role → JSONB permissions (list chuoi "resource:action:scope")

Format: resource:action:scope
  doc:read:own_dept     → chi xem sources cua phong ban minh
  doc:read:all          → xem tat ca sources toan cong ty
  wiki:write:own_dept   → edit wiki trong pham vi phong ban
  wiki:write:all        → edit moi wiki page
  org:departments:manage   → quan ly phong ban
  org:settings:manage      → cau hinh AI providers (chi admin)

Department visibility:
  Source co source_departments → chi employees trong dept do
  Source khong co dept → global (ai co :any deu thay)
```

**Workspace Realm** — Dua tren Project Membership:
```
ProjectMember.workspace_role:
  viewer < contributor < editor < admin  (quyen tang dan)

Quy tac quan trong:
  - Global role KHONG tu dong grant workspace access
  - Admin he thong (role="admin") thay tat ca workspaces
  - Phai la member moi co quyen trong workspace
```

### 11.2 Permission Check trong FastAPI

```python
@router.get("/api/wiki/pages")
async def list_wiki(user = Depends(require_permission("doc:read"))):
    # require_permission("doc:read") thuc hien:
    # 1. Neu admin → pass ngay lap tuc
    # 2. Load employee.custom_role.permissions tu DB
    # 3. Kiem tra "doc:read:own_dept" HOAC "doc:read:all"
    # 4. Neu khong co → 403 Forbidden
    pass
```

### 11.3 Security Implementation

| Diem bao mat | Implementation |
|---|---|
| Password hashing | bcrypt (cost factor mac dinh 12) |
| JWT signing | HS256, SECRET_KEY 256-bit tu env, expire 24h |
| SQL injection | SQLAlchemy ORM parameterized queries |
| XSS | React JSX tự động escape |
| Zip Slip (skill upload) | Validate paths khong co `../` |
| Non-root container | `useradd appuser && USER appuser` trong Dockerfile |
| API key masking | Config service tra ve `•••••` thay vi gia tri that |
| Audit trail | Append-only `audit_logs` cho moi write operation |
| CORS | Strict whitelist tu `CORS_ORIGINS` env |
| Secret warnings | Startup warning neu SECRET_KEY hoac password chua doi |

---

## 12. Luong xu ly du lieu

### 12.1 Upload tai lieu (End-to-End)

```
[Browser]
  POST http://<host>:5055/api/sources/upload  (bypass Next.js proxy)
  multipart/form-data: file + knowledge_type_id + scope_type + ...
    |
    v [API: sources.py]
  Validate file type + size
  Upload to MinIO: sources/{source_id}/{filename}
  Create Source(status="pending")
  enqueue("ingest_file_task", source_id)
  Return {id, status, ...}
    |
    v [Worker: ingest_file_task]
  source.status = "processing", progress = 5
  Download file tu MinIO
  Extract text:
    PDF   → PyMuPDF (per-page) + strip null bytes
    DOCX  → python-docx / mammoth
    URL   → content-core library
  Build outline_json (TOC tu headings)
  Extract images → SourceImage rows + upload to MinIO
  progress = 20
  enqueue("ingest_map_reduce_task", source_id)
  enqueue("caption_images_task", source_id)  [song song]
    |
    v [Worker: ingest_map_reduce_task]
  Phase 0: classify_strategy() → single_pass/standard/hierarchical
  Phase 1: MAP → 6 concurrent LLM calls → SourceChunkExtract rows
           progress = 20 → 60
  Phase 2: REDUCE → SourceCompilationPlan
           progress = 70
  Neu MRP_AUTO_APPROVE_PLAN=true: enqueue("ingest_refine_task")
  Neu false: source.status = "plan_review"

  [Admin xem plan trong UI → POST /api/sources/{id}/plan/approve]

    |
    v [Worker: ingest_refine_task]
  Phase 3: REFINE (LLM compose pages)  progress = 75
  Phase 4: VERIFY (fact-check)         progress = 90
  Phase 5: COMMIT:                     progress = 95 → 100
    - Advisory lock theo slug
    - apply_create() / apply_update() (upsert)
    - merge_page_content() neu xung dot
    - Upsert embeddings (skip neu content_hash khong doi)
    - Refresh WikiLink graph
  source.status = "ready"
```

### 12.2 Semantic Search

```
[User nhap query] → POST /api/wiki/search?q=...
  [API]
    1. Embed query → vector (dung embedding provider hien tai)
    2. pgvector: SELECT ... ORDER BY embedding <=> $vec LIMIT 10
    3. Full-text: ts_vector search (PostgreSQL built-in)
    4. Merge + re-rank ket qua theo combined score
  Return: [{slug, title, excerpt, score}, ...]
```

### 12.3 Claude truy van KB (MCP)

```
[User hoi Claude] "Quy trinh onboarding nhan vien moi?"
  |
  v [Claude → MCP tool call]
search_wiki("onboarding quy trinh")
  [MCP Server]
    Verify MCP token → check scopes
    Semantic search wiki_pages
    Filter theo allowed_source_ids / allowed_knowledge_types
  Return: [{slug, title, excerpt}, ...]
  |
  v [Claude → MCP tool call]
read_wiki_page("topic/onboarding-process")
  [MCP Server]
    Check scope → fetch full content_md
  Return: {title, content_md, backlinks, version}
  |
  v [Claude tra loi user dua tren wiki content]
```

### 12.4 Wiki Draft Contribution

```
[Employee de xuat chinh sua via MCP]
propose_wiki_edit("topic/security-policy", "Cap nhat policy moi...")
  └──> Tao WikiPageDraft(status="pending")

[Editor review trong UI]
  GET /api/wiki/{slug}/drafts → xem diff

[Neu approve]
  POST /api/wiki/{slug}/drafts/{id}/approve
    └──> apply_update() → wiki_pages (version++)
    └──> Tao WikiPageRevision (version history)
    └──> Re-embed trang
    └──> Refresh wikilinks
```

---

## 13. Cau hinh moi truong

### 13.1 Toan bo bien moi truong

```bash
# DATABASE
POSTGRES_USER=arkon
POSTGRES_PASSWORD=<mat_khau_manh>
POSTGRES_DB=arkon
DATABASE_URL=postgresql+asyncpg://arkon:<pass>@postgres:5432/arkon

# AUTH (BAT BUOC doi truoc deploy)
SECRET_KEY=<64-char random hex>
DEFAULT_ADMIN_EMAIL=admin@company.com
DEFAULT_ADMIN_PASSWORD=<mat_khau_admin>

# REDIS
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<mat_khau_redis>
REDIS_DB=0

# MINIO
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=<access_key>
MINIO_SECRET_KEY=<secret_key>
MINIO_BUCKET=arkon
MINIO_SECURE=false

# CORS
CORS_ORIGINS=http://localhost:3119,http://100.x.x.x:3119

# MRP
MRP_AUTO_APPROVE_PLAN=false

# FRONTEND (build-time)
NEXT_PUBLIC_API_URL=              # DE TRONG
INTERNAL_API_URL=http://api:5055  # Next.js → API (build ARG)
```

### 13.2 Cau hinh AI Provider (qua Admin UI → Settings)

Sau deploy, vao Settings → cau hinh:

```
LLM Provider:
  Google    → model: gemini-2.5-pro | gemini-2.5-flash
              API key: AIza...
  OpenAI    → model: gpt-4o | gpt-4o-mini
              API key: sk-...
  Anthropic → model: claude-sonnet-4 | claude-haiku-4
              API key: sk-ant-...
  Ollama    → model: qwen2.5:14b | llama3.1:8b
              Base URL: http://host.docker.internal:11434/v1

Vision Provider (caption anh trong documents):
  Google (Gemini Flash) hoac OpenAI (GPT-4V)

Embedding:
  Google         → text-embedding-004 (768d, task-aware)
  OpenAI large   → text-embedding-3-large (3072d)
  OpenAI small   → text-embedding-3-small (1536d)
  Ollama         → nomic-embed-text (768d, local)
```

### 13.3 Generate SECRET_KEY

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

---

## 14. Mo hinh du lieu

### 14.1 Entity Relationship Diagram

```
employees ─── department_id ───> departments
    |                                |
    | custom_role_id                 | (M2M: source_departments)
    v                                v
  roles ── JSONB permissions      sources ── knowledge_type_id ──> knowledge_types
                                    |
                        +-----------+---------------------+
                        v           v                      v
                source_images  source_chunk_        source_compilation
                               extracts             _plans

wiki_pages <─── wiki_links ───> wiki_pages  [self-ref graph]
    |
    |── wiki_page_drafts ─── employee_id ──> employees
    |── wiki_page_revisions
    +── wiki_page_embeddings_{768,1024,1536,3072}

projects ─── project_members ─── employees [workspace_role]
    |
    +── sources (scope_type=project, scope_id=project.id)

skills ─── skill_versions ─── MinIO
    |── skill_departments ─── departments
    +── skill_contributions [draft→pending→approved/rejected]
```

### 14.2 Skill Contribution Workflow

```
Employee upload .zip → SkillContribution(status=DRAFT)
  └──> Submit → status=PENDING → Admin review

Admin:
  Approve → promote to SkillVersion → status=APPROVED
  Reject  → status=REJECTED + reviewer_note
```

---

## 15. Nha cung cap AI

### 15.1 Provider Registry Pattern

Khong import AI SDK truc tiep trong business logic — tat ca qua `ProviderRegistry`:

```python
# Trong worker task hoac MCP tool:
registry = ProviderRegistry(session)    # Load config tu app_config table trong DB
llm = await registry.get_llm()         # → DungProvider class phu hop
embedding = await registry.get_embedding()
vision = await registry.get_vision()

# De doi provider: chi can cap nhat qua Admin UI (Settings)
# Khong can sua code, khong can restart workers
# Config doc moi khi job chay
```

### 15.2 Ma tran kha nang Provider

| Provider | Embedding | LLM | Vision | Ghi chu |
|---|---|---|---|---|
| **Google (Gemini)** | co (task-aware) | co | co | Day du tinh nang |
| **OpenAI** | co | co | co | Azure-compatible qua base_url |
| **Anthropic** | khong | co | khong | Claude 3.5+ |
| **Ollama** | co | co | khong | Local, khong can internet |
| **Voyage** | Planned | khong | khong | Chua implement |
| **Cohere** | Planned | khong | khong | Chua implement |

### 15.3 Embedding Dimensions

4 muc chieu vector luu SONG SONG trong DB, khong xoa khi doi model:

| Dimension | Table | pgvector Type | Model vi du |
|---|---|---|---|
| 768d | `wiki_page_embeddings_768` | `VECTOR(768)` | nomic-embed-text, text-embedding-004 |
| 1024d | `wiki_page_embeddings_1024` | `VECTOR(1024)` | Voyage, Cohere |
| 1536d | `wiki_page_embeddings_1536` | `VECTOR(1536)` | text-embedding-3-small |
| 3072d | `wiki_page_embeddings_3072` | `HALFVEC(3072)` | text-embedding-3-large |

> `HALFVEC` (16-bit float) dung cho 3072d giam 50% dung luong so voi `VECTOR` (32-bit float).

### 15.4 Ollama — Chay hoan toan offline

```bash
# Tren may host (Windows):
ollama pull qwen2.5:14b
ollama pull nomic-embed-text

# Arkon Settings:
LLM Provider:    ollama
LLM Model:       qwen2.5:14b
LLM Base URL:    http://host.docker.internal:11434/v1

Embedding:       ollama/nomic-embed-text (768d)
Embedding URL:   http://host.docker.internal:11434/v1
```

`host.docker.internal` la DNS dac biet cua Docker Desktop tro ve IP may host,
cho phep containers goi Ollama dang chay tren Windows ma khong can biet IP.

---

## Phu luc — Lenh thuong dung

```bash
# Xem logs realtime
docker compose logs -f api
docker compose logs -f worker

# Restart service
docker compose restart api
docker compose restart worker worker_skills

# Vao shell container
docker exec -it arkon_api bash
docker exec -it arkon_postgres psql -U arkon arkon

# Du lieu
docker system df                        # Dung luong Docker
du -sh F:\arkon-data\*                  # Dung luong data tren host

# Database
docker exec arkon_postgres pg_dump -U arkon arkon > backup.sql
docker exec -i arkon_postgres psql -U arkon arkon < backup.sql
docker compose exec api alembic history  # Xem lich su migration
docker compose exec api alembic upgrade head  # Chay migration thu cong

# Redis job queue
docker exec -it arkon_redis redis-cli -a <password>
LLEN arq:queue:default    # So job dang cho
KEYS arq:job:*            # Jobs dang chay

# Generate SECRET_KEY
python -c "import secrets; print(secrets.token_hex(32))"
```

---

*Tai lieu duoc tao tu phan tich source code Arkon va cap nhat cho release
v2.0.0. Cap nhat khi co thay doi kien truc quan trong.*
