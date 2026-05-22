# Arkon — Quick Start (30 phút)

Hướng dẫn này đưa bạn từ zero đến hệ thống Arkon đầy đủ chức năng trong khoảng 30 phút.

---

## Bước 1 — Chuẩn bị (5 phút)

### Yêu cầu

- Docker Desktop đang chạy
- Có ít nhất 1 trong các API key: Google AI, OpenAI, hoặc Anthropic
- (Hoặc Ollama cài trên máy để chạy hoàn toàn offline)

### Clone project

```bash
cd E:\AI-CLAUDE
# Arkon đã có tại E:\AI-CLAUDE\arkon
cd arkon
```

---

## Bước 2 — Cấu hình (5 phút)

Copy file cấu hình mẫu:

```bash
copy .env.docker.example .env.docker
```

Mở `.env.docker` và sửa các giá trị **bắt buộc**:

```env
# Tạo SECRET_KEY bằng lệnh:
# python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=<dán kết quả vào đây>

# Tài khoản admin đầu tiên
DEFAULT_ADMIN_EMAIL=admin@company.com
DEFAULT_ADMIN_PASSWORD=mat-khau-manh-123

# Mật khẩu database (tùy chọn, mặc định là arkon_secret)
POSTGRES_PASSWORD=arkon_secret
REDIS_PASSWORD=arkon_secret
MINIO_SECRET_KEY=minioadmin123

# CORS — thêm IP/domain của bạn nếu truy cập từ nơi khác
CORS_ORIGINS=http://localhost:3119
```

> **Lưu ý Windows**: Nếu dùng Ollama local, không cần API key của bất kỳ nhà cung cấp nào.

---

## Bước 3 — Khởi động (5 phút)

```bash
# Build và khởi động toàn bộ hệ thống
docker compose build
docker compose up -d

# Kiểm tra trạng thái
docker compose ps
```

Chờ khoảng 30-60 giây rồi kiểm tra:

```bash
# Phải trả về {"status": "healthy", ...}
curl http://localhost:5055/health
```

Nếu thấy `healthy` → tiếp tục bước 4.

---

## Bước 4 — Đăng nhập lần đầu (2 phút)

Mở trình duyệt: **http://localhost:3119**

Đăng nhập với:
- Email: giá trị `DEFAULT_ADMIN_EMAIL` đã đặt
- Mật khẩu: giá trị `DEFAULT_ADMIN_PASSWORD` đã đặt

---

## Bước 5 — Cấu hình AI Provider (5 phút)

Đây là bước **quan trọng nhất** — không có AI provider, hệ thống không xử lý được tài liệu.

Vào **Settings** (góc trái dưới sidebar):

### Nếu dùng Google (Gemini) — khuyến nghị cho chất lượng tốt nhất

```
Embedding Provider: Google
Embedding Model:    text-embedding-004
API Key:            AIza... (lấy từ Google AI Studio)

LLM Provider:       Google  
LLM Model:          gemini-2.5-flash
API Key:            AIza... (cùng key)

Vision Provider:    Google
Vision Model:       gemini-2.0-flash
```

### Nếu dùng OpenAI

```
Embedding: OpenAI → text-embedding-3-small → sk-...
LLM:       OpenAI → gpt-4o-mini → sk-...
Vision:    OpenAI → gpt-4o → sk-...
```

### Nếu dùng Ollama (offline, không cần internet)

Đảm bảo Ollama đang chạy trên máy host:

```bash
ollama pull qwen2.5:14b
ollama pull nomic-embed-text
```

Rồi trong Settings:

```
Embedding: Ollama → nomic-embed-text
           Base URL: http://host.docker.internal:11434/v1

LLM:       Ollama → qwen2.5:14b  
           Base URL: http://host.docker.internal:11434/v1

Vision:    (không hỗ trợ với Ollama — để trống)
```

Nhấn **Save Settings** → nhấn **Test LLM** để xác nhận kết nối.

---

## Bước 6 — Thiết lập cơ bản (5 phút)

### Tạo phòng ban

**Admin → Departments → New Department**

Ví dụ: `IT`, `HR`, `Legal`, `Engineering`

### Tạo Knowledge Type

**Admin → Knowledge Types → New Type**

Ví dụ:
- Name: `Quy trình` · Description: `SOPs, hướng dẫn nghiệp vụ`
- Name: `Chính sách` · Description: `Nội quy, quy định công ty`
- Name: `Kỹ thuật` · Description: `Tài liệu kỹ thuật, API docs`

---

## Bước 7 — Upload tài liệu đầu tiên (3 phút)

**Knowledge Base → Upload**

1. Kéo thả file PDF/DOCX hoặc dán URL
2. Chọn **Knowledge Type** (vừa tạo ở bước 6)
3. Chọn **Scope**: Global (mọi người) hoặc Project (workspace cụ thể)
4. Nhấn Upload

Hệ thống sẽ bắt đầu xử lý. Quan sát **progress bar** trong danh sách tài liệu.

**Timeline xử lý điển hình:**
- File PDF 50 trang: ~3-5 phút
- URL/website: ~1-3 phút
- Tài liệu lớn (>200 trang): ~10-15 phút

---

## Bước 8 — Xem kết quả (2 phút)

Khi status chuyển sang **Ready** (hoặc **Plan Review** nếu chưa bật auto-approve):

### Nếu thấy "Plan Review"

Nhấn **Review Plan** → xem danh sách wiki pages sẽ được tạo → nhấn **Approve**.

### Xem Wiki đã được tạo

**Wiki** → Tìm kiếm hoặc duyệt các trang vừa được tạo tự động.

---

## Bước 9 — Kết nối Claude (3 phút)

### Tạo MCP token

**Admin → Employees → [tên nhân viên] → Generate Token**

Sao chép token bắt đầu bằng `ark_...`

### Cấu hình Claude Desktop

Mở file `claude_desktop_config.json`:
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

```json
{
  "mcpServers": {
    "arkon": {
      "url": "http://localhost:5055/mcp",
      "headers": {
        "Authorization": "Bearer ark_xxxx..."
      }
    }
  }
}
```

Khởi động lại Claude Desktop.

### Kiểm tra

Hỏi Claude: *"Tìm trong knowledge base về [chủ đề trong tài liệu bạn vừa upload]"*

Claude sẽ tự động gọi `search_wiki` và trả lời dựa trên nội dung đã biên soạn.

---

## Checklist hoàn thành

- [ ] Docker stack chạy và healthy
- [ ] Đăng nhập thành công vào Admin Portal
- [ ] Cấu hình AI Provider và Test LLM thành công
- [ ] Tạo ít nhất 1 phòng ban
- [ ] Tạo ít nhất 1 Knowledge Type
- [ ] Upload 1 tài liệu thử nghiệm
- [ ] Wiki pages được tạo sau khi xử lý
- [ ] Claude Desktop kết nối và trả lời được câu hỏi về tài liệu

---

## Bước tiếp theo

| Muốn làm gì | Xem tài liệu |
|---|---|
| Thêm nhân viên và phân quyền | [ADMIN-GUIDE.md](ADMIN-GUIDE.md) |
| Tạo workspace cho nhóm | [WORKSPACES.md](WORKSPACES.md) |
| Tùy chỉnh knowledge types | [KNOWLEDGE-TYPES.md](KNOWLEDGE-TYPES.md) |
| Hiểu pipeline MRP chi tiết | [WIKI.md](WIKI.md) |
| Deploy lên server | [SETUP.md](SETUP.md) |

---

## Xử lý sự cố nhanh

| Vấn đề | Giải pháp |
|---|---|
| Container không khởi động | `docker compose logs api` xem lỗi |
| Health check fail | Đợi thêm 30s, Redis/MinIO cần thêm thời gian |
| Upload xong nhưng status mãi `pending` | Worker không chạy: `docker compose logs worker` |
| Test LLM fail | Kiểm tra API key và Base URL trong Settings |
| Claude không thấy tools | Khởi động lại Claude Desktop sau khi sửa config |
| Không truy cập được từ IP khác | Thêm IP vào `CORS_ORIGINS` trong `.env.docker` rồi restart |
