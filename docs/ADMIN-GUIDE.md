# Hướng dẫn Quản trị viên (Admin Guide)

Tài liệu này dành cho quản trị viên hệ thống Arkon — người có tài khoản với `role=admin`.

---

## Mục lục

1. [Tổng quan quyền Admin](#1-tổng-quan-quyền-admin)
2. [Quản lý Phòng ban](#2-quản-lý-phòng-ban)
3. [Quản lý Nhân viên](#3-quản-lý-nhân-viên)
4. [Quản lý Role & Phân quyền](#4-quản-lý-role--phân-quyền)
5. [Cấu hình AI Provider](#5-cấu-hình-ai-provider)
6. [Quản lý Knowledge Types](#6-quản-lý-knowledge-types)
7. [Quản lý Tài liệu (Sources)](#7-quản-lý-tài-liệu-sources)
8. [Quản lý Wiki](#8-quản-lý-wiki)
9. [Quản lý Skills](#9-quản-lý-skills)
10. [Quản lý Workspaces](#10-quản-lý-workspaces)
11. [MCP Tokens](#11-mcp-tokens)
12. [Audit Log](#12-audit-log)
13. [Bảo trì hệ thống](#13-bảo-trì-hệ-thống)

---

## 1. Tổng quan quyền Admin

Admin hệ thống (`role=admin`) có quyền:
- Bypass **tất cả** permission checks
- Xem và quản lý mọi phòng ban, workspace
- Cấu hình AI providers
- Quản lý toàn bộ nhân viên và role
- Xem audit log đầy đủ
- Xóa tài liệu và wiki pages của bất kỳ ai

> Admin khác với **Workspace Admin** (chỉ có quyền trong workspace cụ thể). Tài liệu này nói về system admin.

---

## 2. Quản lý Phòng ban

### Tạo phòng ban

**Admin Portal → Departments → New Department**

| Trường | Bắt buộc | Mô tả |
|---|---|---|
| Name | Có | Tên phòng ban (vd: `IT`, `Nhân sự`, `Kỹ thuật`) |
| Description | Không | Mô tả ngắn về phòng ban |

### Vai trò của phòng ban

Phòng ban quyết định visibility của tài liệu:
- Tài liệu được gán cho phòng ban → chỉ nhân viên phòng đó thấy (nếu có `doc:read:own_dept`)
- Tài liệu không gán phòng ban → **global** (mọi người thấy)

### Sửa / Xóa phòng ban

Nhấn biểu tượng edit hoặc delete trên card phòng ban. Lưu ý: Xóa phòng ban không xóa nhân viên, chỉ bỏ gán phòng ban.

---

## 3. Quản lý Nhân viên

### Tạo nhân viên mới

**Admin Portal → Employees → New Employee**

| Trường | Bắt buộc | Mô tả |
|---|---|---|
| Name | Có | Họ tên nhân viên |
| Email | Có | Email đăng nhập (unique) |
| Password | Có | Mật khẩu ban đầu |
| Department | Có | Phòng ban |
| Role | Không | Custom role (nếu trống = quyền mặc định) |

**Quyền mặc định** khi không có custom role:
```
doc:read:own_dept    → Đọc tài liệu phòng ban
doc:create:own_dept  → Upload tài liệu
wiki:read:own_dept   → Đọc wiki
wiki:write:own_dept  → Đề xuất sửa wiki
skill:read:own_dept  → Dùng skills
```

### Kích hoạt / Vô hiệu hóa tài khoản

Tìm nhân viên → Toggle **Active** trên card. Tài khoản bị vô hiệu hóa không thể đăng nhập và MCP token của họ bị vô hiệu lực.

### Đổi mật khẩu nhân viên

**Admin Portal → Employees → [tên] → Change Password**

### Gán lại phòng ban hoặc role

Edit employee → thay đổi Department hoặc Custom Role → Save.

---

## 4. Quản lý Role & Phân quyền

### Tạo Role mới

**Admin Portal → Roles → New Role**

1. Đặt tên role (vd: `Editor Nội dung`, `Quản lý Phòng ban`)
2. Chọn các permission cần thiết
3. Save

### Các permission có sẵn

**Tài liệu (doc)**

| Permission | Mô tả |
|---|---|
| `doc:read:own_dept` | Đọc tài liệu phòng ban mình |
| `doc:read:all` | Đọc toàn bộ tài liệu |
| `doc:create:own_dept` | Upload tài liệu cho phòng ban |
| `doc:create:all` | Upload tài liệu cho bất kỳ phòng ban |
| `doc:edit:own_dept` | Sửa metadata tài liệu phòng ban |
| `doc:edit:all` | Sửa metadata bất kỳ tài liệu |
| `doc:delete:own_dept` | Xóa tài liệu phòng ban |
| `doc:delete:all` | Xóa bất kỳ tài liệu |

**Wiki**

| Permission | Mô tả |
|---|---|
| `wiki:read:own_dept` | Đọc wiki global + phòng ban |
| `wiki:read:all` | Đọc toàn bộ wiki |
| `wiki:write:own_dept` | Đề xuất chỉnh sửa wiki (tạo draft) |
| `wiki:write:all` | Chỉnh sửa trực tiếp + approve/reject drafts |
| `wiki:delete:own_dept` | Xóa wiki pages phòng ban |
| `wiki:delete:all` | Xóa bất kỳ wiki page |

**Skills**

| Permission | Mô tả |
|---|---|
| `skill:read:own_dept` | Dùng skills phòng ban + global |
| `skill:read:all` | Dùng tất cả skills |
| `skill:create:own_dept` | Upload skill cho phòng ban |
| `skill:create:all` | Upload skill cho bất kỳ phòng ban |

**Tổ chức (org)**

| Permission | Mô tả |
|---|---|
| `org:departments:read` | Xem danh sách phòng ban |
| `org:departments:manage` | Tạo/sửa/xóa phòng ban |
| `org:employees:read` | Xem danh sách nhân viên |
| `org:employees:manage` | Tạo/sửa/vô hiệu hóa nhân viên |
| `org:roles:read` | Xem roles |
| `org:roles:manage` | Tạo/sửa/xóa roles |
| `org:settings:manage` | Cấu hình AI providers |
| `org:audit:read` | Xem audit log |

### Role presets gợi ý

**Nhân viên cơ bản** (đọc và đề xuất):
```
doc:read:own_dept, wiki:read:own_dept, wiki:write:own_dept,
skill:read:own_dept, doc:create:own_dept
```

**Biên tập viên** (chỉnh sửa trực tiếp):
```
Tất cả quyền nhân viên cơ bản +
wiki:write:all, doc:edit:own_dept
```

**Quản lý phòng ban**:
```
Tất cả quyền biên tập viên +
doc:edit:own_dept, doc:delete:own_dept,
org:employees:read, org:departments:read
```

**Knowledge Manager** (quản lý toàn bộ KB):
```
doc:*:all, wiki:*:all, skill:*:all, org:*:read
```

---

## 5. Cấu hình AI Provider

**Admin Portal → Settings**

> Chỉ admin hoặc người có `org:settings:manage` mới xem được trang này.

### LLM Provider (Bắt buộc)

Dùng để biên soạn wiki từ tài liệu. Cần model có **context window lớn**.

| Provider | Model khuyến nghị | Ghi chú |
|---|---|---|
| Google | `gemini-2.5-pro` | Chất lượng tốt nhất, context 1M tokens |
| Google | `gemini-2.5-flash` | Nhanh hơn, rẻ hơn |
| OpenAI | `gpt-4o` | Ổn định, 128k context |
| Anthropic | `claude-sonnet-4-20250514` | Tốt cho văn bản tiếng Việt |
| Ollama | `qwen2.5:14b` | Local, miễn phí, offline |

### Embedding Provider (Bắt buộc cho tìm kiếm)

Dùng để tạo vector embeddings cho semantic search.

| Provider | Model | Chiều | Ghi chú |
|---|---|---|---|
| Google | `text-embedding-004` | 768d | Task-aware, tốt nhất |
| OpenAI | `text-embedding-3-large` | 3072d | Chính xác cao |
| OpenAI | `text-embedding-3-small` | 1536d | Nhanh hơn |
| Ollama | `nomic-embed-text` | 768d | Local, offline |

> **Lưu ý**: Sau khi đổi embedding model, cần chạy **Re-embed all pages** (nút trong trang Settings) để cập nhật toàn bộ wiki. Quá trình này có thể mất vài phút với lượng lớn tài liệu.

### Vision Provider (Không bắt buộc)

Dùng để tạo caption cho ảnh trong tài liệu PDF. Nếu không cấu hình, ảnh vẫn được extract nhưng không có mô tả.

| Provider | Model khuyến nghị |
|---|---|
| Google | `gemini-2.0-flash` |
| OpenAI | `gpt-4o` |

### Test kết nối

Sau khi lưu settings, nhấn **Test LLM** để xác nhận:
- `success: true` → Kết nối thành công
- `success: false` → Kiểm tra API key và Base URL

---

## 6. Quản lý Knowledge Types

Knowledge Types là **taxonomy** để phân loại tài liệu. LLM sử dụng hai trường để định hướng cách biên soạn wiki: `description` (nhãn ngắn) và `extraction_hints` (hướng dẫn extraction chi tiết).

### Tạo Knowledge Type

**Admin Portal → Knowledge Types → New Type**

| Trường | Mô tả |
|---|---|
| Name | Tên ngắn (vd: `SOP`, `Chính sách`, `Kỹ thuật`) |
| Slug | URL-safe, tự động tạo (vd: `sop`, `chinh-sach`) |
| Description | Nhãn ngắn hiển thị UI, label cho LLM (1-3 câu) |
| Extraction Hints | **Mới**: Hướng dẫn chi tiết cho LLM — cái gì KEEP, cái gì DROP, cấu trúc wiki page. Ghi đè rule chung khi có xung đột. Để trống với domain thông thường. |
| Color | Màu badge trên UI |

### Hai cấp độ guidance cho LLM

**`description`** — label ngắn, đủ để LLM nhận diện danh mục:
```
Pentest và kỹ thuật tấn công bảo mật — bypass, exploit, redteam TTPs.
```

**`extraction_hints`** — hướng dẫn đầy đủ cho domain chuyên biệt:
```markdown
KEEP lệnh platform-specific nguyên văn (EXEC xp_cmdshell, INTO OUTFILE...)
KEEP mỗi bypass theo từng platform = concept page riêng biệt
KEEP CVE IDs, CVSS, tool commands đầy đủ flag
KHÔNG generalize — tính cụ thể là giá trị
```

> Với domain thông thường (SOP, chính sách, kỹ thuật), chỉ cần `description` — pipeline mặc định xử lý đủ tốt. Chỉ cần `extraction_hints` với domain chuyên biệt mà rule chung hay lọc mất thông tin quan trọng.

### Auto-seed cho security KTs

Khi khởi động, Arkon **tự động seed** `extraction_hints` cho knowledge types có slug chứa các từ khóa bảo mật:

| Pattern slug | Hints được seed |
|---|---|
| `pentest`, `offensive`, `exploit`, `bypass`, `sqli`, `injection` | Pentest hints — giữ platform-specific technique, CVE, payload |
| `redteam`, `red-team`, `ttp`, `c2`, `implant` | Redteam hints — MITRE ATT&CK IDs, C2 config, OPSEC |
| `vuln`, `vulnerability`, `cve`, `bugbounty`, `0day` | Vuln research hints — version ranges, PoC, CVSS vector |

Seed chỉ set khi `extraction_hints` đang NULL — **không ghi đè** nếu admin đã chỉnh.

### Gợi ý Knowledge Types cho doanh nghiệp

```
SOP / Quy trình vận hành
  → Description: Tài liệu quy trình, hướng dẫn nghiệp vụ từng bước
  → Extraction hints: (không cần)

Chính sách / Nội quy
  → Description: Quy định nội bộ, chính sách nhân sự, nội quy công ty
  → Extraction hints: (không cần)

Tài liệu kỹ thuật
  → Description: Tài liệu kỹ thuật, API docs, hướng dẫn cài đặt
  → Extraction hints: (không cần với kỹ thuật thông thường)

Pentest / Redteam
  → Description: Tài liệu pentest, kỹ thuật tấn công và bypass bảo mật
  → Extraction hints: Auto-seed khi slug chứa "pentest"

An ninh mạng (Defensive)
  → Description: Tài liệu bảo mật phòng thủ, CVE advisories, incident response
  → Extraction hints: (tùy chọn — có thể thêm nếu cần giữ IOC format cụ thể)

Pháp lý / Hợp đồng
  → Description: Hợp đồng mẫu, điều khoản, tài liệu pháp lý
  → Extraction hints: (tùy chọn — nếu cần giữ nguyên văn các điều khoản)
```

### Tại sao `extraction_hints` quan trọng với security domain?

Pipeline mặc định có rule "drop source-specific framing" để bỏ các đoạn như "In section 3 below..." hay "As mentioned earlier...". Nhưng với tài liệu pentest, câu như _"On SQL Server, run EXEC master..xp_cmdshell"_ bị coi là "platform-specific framing" → bị lọc → wiki chỉ còn "stored procedure bypass" mà không có cú pháp thực thi.

`extraction_hints` override rule này, nói với LLM: **tính cụ thể của từng platform là nội dung cốt lõi, không phải noise**.

> Description tốt → Wiki pages tốt hơn. Hãy viết description rõ ràng, ít nhất 2-3 câu.

---

## 7. Quản lý Tài liệu (Sources)

### Upload tài liệu

**Knowledge Base → Upload**

**Định dạng hỗ trợ**: PDF, DOCX, TXT, MD, URL web

**Metadata khi upload:**

| Trường | Mô tả |
|---|---|
| Knowledge Type | Phân loại (quan trọng cho chất lượng wiki) |
| Scope | Global (mọi người) hoặc Project (workspace cụ thể) |
| Department | Giới hạn visibility (để trống = global) |

### Xem trạng thái xử lý

Danh sách tài liệu hiển thị:

| Status | Ý nghĩa |
|---|---|
| `pending` | Đang chờ worker |
| `processing` | Worker đang xử lý (có progress bar) |
| `plan_review` | Cần human review compilation plan |
| `ready` | Hoàn thành, wiki đã được tạo |
| `error` | Xử lý thất bại (xem error message) |

### Review Compilation Plan

Khi status = `plan_review`:
1. Nhấn **Review Plan** trên tài liệu đó
2. Xem danh sách wiki pages sẽ được tạo/cập nhật
3. Kiểm tra: tên trang, loại (entity/concept/topic), entities được cover
4. **Approve** hoặc **Reject with note**

> Bật `MRP_AUTO_APPROVE_PLAN=true` trong `.env.docker` để bỏ qua bước này.

### Xóa tài liệu

Xóa tài liệu **không** tự động xóa wiki pages đã được tạo từ nó. Wiki pages liên quan sẽ được đánh dấu `orphaned=true`.

### Re-ingest tài liệu

Nếu muốn xử lý lại tài liệu:
1. API: `POST /api/sources/{id}/retry`
2. Hoặc xóa và upload lại

Khi re-ingest, ảnh cũ sẽ được xóa và trích xuất lại để tránh duplicate.

---

## 8. Quản lý Wiki

### Xem tất cả wiki pages (kể cả orphaned)

**Wiki → Browse** → Tìm kiếm hoặc duyệt tree

### Chỉnh sửa trực tiếp (Admin hoặc wiki:write:all)

**Wiki → [tên page] → Edit**

### Xem lịch sử phiên bản

**Wiki → [tên page] → History**

### Rollback về phiên bản trước

**History → Chọn version → Rollback**

Rollback tạo revision mới (`change_type=rollback`) — lịch sử không bị xóa.

### Xóa wiki page

**Wiki → [tên page] → Delete**

> Admin có thể xóa bất kỳ trang. Thận trọng với trang có nhiều backlinks.

### Quản lý Draft đang chờ

**Admin → Wiki Drafts** (hoặc thông qua Wiki page → Drafts tab)

Approve hoặc reject với reviewer note.

---

## 9. Quản lý Skills

### Xem tất cả skills

**Admin Portal → Skills**

### Upload skill mới

**Skills → Upload Skill**

File upload phải là `.zip` chứa skill package hợp lệ.

### Review Skill Contributions

Nhân viên có thể đề xuất skill mới hoặc cập nhật skill hiện có.

**Admin → Skill Contributions** → Xem pending → Approve hoặc Reject

### Quản lý visibility

**Skills → [tên skill] → Edit → Departments**

Chọn phòng ban được phép dùng. Để trống = global (mọi người).

---

## 10. Quản lý Workspaces

> Chỉ system admin mới tạo và xóa workspace được.

### Tạo workspace

**Workspaces → New Workspace**

| Trường | Mô tả |
|---|---|
| Name | Tên workspace (vd: `Dự án Alpha`, `Nhóm Security`) |
| Description | Mô tả mục đích |

### Thêm member vào workspace

**Workspaces → [tên] → Members → Add Member**

Gán workspace role:
- `viewer` — chỉ đọc
- `contributor` — đọc + đề xuất wiki edits
- `editor` — chỉnh sửa trực tiếp + approve drafts
- `admin` — quản lý members + mọi quyền editor

### Xóa workspace

**Workspaces → [tên] → Settings → Delete**

Xóa workspace xóa: membership, workspace-scoped sources, workspace wiki pages.

---

## 11. MCP Tokens

### Tạo token cho nhân viên

**Admin Portal → Employees → [tên] → Generate Token**

Token format: `ark_xxxxxxxxxxxxxxxxxxxx`

> Token chỉ hiển thị **một lần** khi tạo. Nhân viên cần copy ngay.

### Thu hồi token

**Admin Portal → Employees → [tên] → Revoke Token**

Token bị thu hồi ngay lập tức — mọi request đang dùng token đó sẽ nhận 401.

### Phạm vi token (Scopes)

MCP token tự động kế thừa permission của nhân viên:
- `allowed_knowledge_types` — knowledge types nhân viên được phép access
- `allowed_source_ids` — nguồn tài liệu trong phạm vi
- Department scope — tương ứng với role phòng ban

---

## 12. Audit Log

**Admin Portal → Audit**

Audit log ghi lại mọi hành động quan trọng:
- Upload, xóa tài liệu
- Tạo/sửa/xóa wiki pages
- Approve/reject drafts
- Tạo/thu hồi MCP tokens
- Thay đổi cấu hình

### Lọc audit log

| Bộ lọc | Mô tả |
|---|---|
| Date range | Khoảng thời gian |
| Actor | Nhân viên thực hiện hành động |
| Resource type | source, wiki_page, employee, token... |
| Action | create, update, delete, approve... |

### Xuất audit log (API)

```bash
GET /api/audit?from=2026-01-01&to=2026-05-16&limit=1000
Authorization: Bearer <admin_token>
```

---

## 13. Bảo trì hệ thống

### Kiểm tra sức khỏe

```bash
curl http://localhost:5055/health
# Kỳ vọng: {"status": "healthy", "services": {"database": "healthy", "redis": "healthy", "minio": "healthy"}}
```

### Restart services

```bash
# Restart tất cả
docker compose restart

# Restart từng service
docker compose restart api
docker compose restart worker worker_skills
```

### Xem logs

```bash
# Logs API (xem lỗi xử lý)
docker compose logs -f api

# Logs worker (xem tiến trình ingestion)
docker compose logs -f worker

# Logs gần đây (50 dòng)
docker compose logs --tail=50 api
```

### Backup

```bash
# Backup database
docker exec arkon_postgres pg_dump -U arkon arkon > backup_$(date +%Y%m%d).sql

# MinIO data nằm ở F:\arkon-data\minio\ — copy thủ công khi cần
```

### Cập nhật hệ thống

```bash
cd E:\AI-CLAUDE\arkon

# Pull code mới (nếu có)
git pull

# Rebuild và restart
docker compose build api frontend
docker compose up -d

# Chạy migration nếu có schema mới
docker compose exec api alembic upgrade head
```

### Dọn dẹp

```bash
# Xóa Docker images không dùng
docker image prune -f

# Xóa temp uploads cũ (worker tự động dọn mỗi 1h)
docker compose exec api python -c "from app.worker import cleanup_temp_uploads_cron; import asyncio; asyncio.run(cleanup_temp_uploads_cron({}))"
```

### Re-embed tất cả wiki pages

Cần thiết sau khi đổi embedding model:

**Admin Portal → Settings → Re-embed All Pages**

Hoặc qua API:
```bash
POST /api/admin/embeddings/reembed-all
Authorization: Bearer <admin_token>
```

---

## Câu hỏi thường gặp

**Q: Tài liệu bị stuck ở `processing` bao lâu?**  
A: Bình thường 2-15 phút tùy kích thước. Nếu sau 30 phút không thay đổi, kiểm tra `docker compose logs worker`.

**Q: Wiki page không được tạo dù tài liệu status = ready?**  
A: Kiểm tra LLM provider đã cấu hình chưa. Xem logs worker để tìm lỗi cụ thể.

**Q: Làm sao biết bao nhiêu tài liệu đang trong queue?**  
A: `docker exec -it arkon_redis redis-cli -a <password> LLEN arq:queue:default`

**Q: Có thể có nhiều admin không?**  
A: Có. Set `role=admin` cho bất kỳ nhân viên nào cần quyền admin.

**Q: Đổi API key LLM có cần restart không?**  
A: Không. Config được đọc mỗi khi job chạy — thay đổi có hiệu lực ngay với job tiếp theo.

**Q: Embedding model đang dùng chiều nào?**  
A: Xem trong Settings → Embedding → Model. Chiều tương ứng: nomic-embed-text=768d, text-embedding-3-small=1536d, text-embedding-3-large=3072d.
