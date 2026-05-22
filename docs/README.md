# Arkon — Tài liệu hệ thống

> Enterprise AI Knowledge Base · v0.1.0

---

## Bắt đầu nhanh

| Tôi muốn... | Đọc tài liệu... |
|---|---|
| Cài đặt và chạy lần đầu | [QUICKSTART.md](QUICKSTART.md) |
| Deploy bằng Docker | [SETUP.md](SETUP.md) |
| Chạy local để phát triển | [HOW_TO_RUN.md](HOW_TO_RUN.md) |
| Kết nối Claude Desktop | [MCP.md](MCP.md) |
| Hiểu kiến trúc hệ thống | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Quản lý nhân viên, phòng ban, role | [ADMIN-GUIDE.md](ADMIN-GUIDE.md) |
| Cấu hình phân quyền | [ACCESS-CONTROL.md](ACCESS-CONTROL.md) |
| Làm việc với Wiki & pipeline | [WIKI.md](WIKI.md) |
| Quản lý Skills AI | [SKILLS.md](SKILLS.md) |
| Quản lý Workspaces | [WORKSPACES.md](WORKSPACES.md) |
| Tạo Knowledge Types | [KNOWLEDGE-TYPES.md](KNOWLEDGE-TYPES.md) |
| Tham chiếu API đầy đủ | [API-REFERENCE.md](API-REFERENCE.md) |

---

## Tổng quan hệ thống

```
Nhân viên upload tài liệu (PDF, DOCX, URL)
       │
       ▼
LLM Pipeline (MRP) xử lý tự động
       │
       ▼
Wiki tri thức có cấu trúc
       │
       ▼
Claude Desktop/Code truy vấn qua MCP
       │
       ▼
Trả lời dựa trên tri thức nội bộ
```

**Arkon** biến tài liệu thô của tổ chức thành tri thức có cấu trúc mà Claude có thể hiểu và trả lời.

---

## Cấu trúc tài liệu

```
docs/
├── README.md           # File này — index tổng hợp
├── QUICKSTART.md       # Hướng dẫn nhanh (30 phút từ zero đến chạy)
├── SETUP.md            # Deploy Docker (production + server)
├── HOW_TO_RUN.md       # Chạy local cho phát triển
├── ARCHITECTURE.md     # Phân tích thiết kế kỹ thuật
├── ADMIN-GUIDE.md      # Hướng dẫn quản trị viên
├── ACCESS-CONTROL.md   # Hệ thống phân quyền
├── MCP.md              # Tích hợp Claude qua MCP
├── WIKI.md             # Wiki system & MRP pipeline
├── SKILLS.md           # AI Skills management
├── WORKSPACES.md       # Workspaces (project scoping)
├── KNOWLEDGE-TYPES.md  # Knowledge type taxonomy
├── API-REFERENCE.md    # Tham chiếu API đầy đủ
└── assets/             # Hình ảnh, diagrams
```

---

## Luồng làm việc điển hình

### Quản trị viên (lần đầu cài đặt)

```
1. Deploy Docker (SETUP.md)
2. Cấu hình AI Provider → Settings → LLM + Embedding
3. Tạo phòng ban → Admin → Departments
4. Tạo Knowledge Types → Admin → Knowledge Types
5. Tạo nhân viên + cấp MCP token → Admin → Employees
6. Upload tài liệu đầu tiên → Knowledge Base → Upload
7. Phê duyệt Compilation Plan (nếu auto_approve = false)
8. Kiểm tra wiki đã được tạo → Wiki
```

### Nhân viên (hàng ngày)

```
1. Hỏi Claude: "Quy trình onboarding là gì?"
   → Claude dùng MCP tool search_wiki()
   → Claude đọc wiki page và trả lời

2. Upload tài liệu mới → Knowledge Base → Upload
   → Pipeline tự động xử lý và cập nhật wiki

3. Đề xuất chỉnh sửa wiki → Wiki page → Propose Edit
   → Editor review và approve
```

---

## Yêu cầu hệ thống

| Thành phần | Yêu cầu |
|---|---|
| Docker Engine | >= 24.0 + Compose v2 |
| RAM | 4 GB tối thiểu (8 GB khuyến nghị) |
| Ổ đĩa | 20 GB + dung lượng tài liệu |
| AI Provider | Google / OpenAI / Anthropic / Ollama |

---

## Các URL quan trọng (sau khi deploy)

| URL | Mục đích |
|---|---|
| `http://localhost:3119` | Admin Portal (web UI) |
| `http://localhost:5055/docs` | API Swagger documentation |
| `http://localhost:5055/health` | Health check |
| `http://localhost:5055/mcp` | MCP endpoint cho Claude |
| `http://localhost:9003` | MinIO Console (file storage) |
