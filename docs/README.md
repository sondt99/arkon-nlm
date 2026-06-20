# Arkon — Tài liệu hệ thống

> Enterprise AI Knowledge Hub · v2.0.0 · cập nhật 20/06/2026

## Báo cáo phát triển

| Tài liệu | Nội dung |
|---|---|
| [DEVELOPMENT-REPORT-V2.md](DEVELOPMENT-REPORT-V2.md) | Báo cáo tổng hợp phát triển Arkon v2, kết quả, kiểm thử và roadmap |
| [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md) | Thiết kế chức năng và lịch sử thay đổi chi tiết |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Kiến trúc kỹ thuật, database và luồng xử lý |

## Hướng dẫn theo nhu cầu

| Tôi muốn... | Đọc tài liệu... |
|---|---|
| Cài đặt và chạy lần đầu | [QUICKSTART.md](QUICKSTART.md) |
| Deploy bằng Docker | [SETUP.md](SETUP.md) |
| Chạy local để phát triển | [HOW_TO_RUN.md](HOW_TO_RUN.md) |
| Kết nối Claude Desktop/Code | [MCP.md](MCP.md) |
| Quản trị người dùng và role | [ADMIN-GUIDE.md](ADMIN-GUIDE.md) |
| Cấu hình phân quyền | [ACCESS-CONTROL.md](ACCESS-CONTROL.md) |
| Hiểu Wiki, MRP và provenance | [WIKI.md](WIKI.md) |
| Tích hợp NotebookLM | [NOTEBOOKLM-INTEGRATION.md](NOTEBOOKLM-INTEGRATION.md) |
| Quản lý AI Skills | [SKILLS.md](SKILLS.md) |
| Quản lý Workspaces | [WORKSPACES.md](WORKSPACES.md) |
| Tạo Knowledge Types | [KNOWLEDGE-TYPES.md](KNOWLEDGE-TYPES.md) |
| Tra cứu endpoint | [API-REFERENCE.md](API-REFERENCE.md) |
| Xử lý lỗi vận hành | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |

## Tổng quan hệ thống

```text
Tài liệu PDF/DOCX/TXT/MD/URL/ZIP
              │
              ▼
MRP: Map → Reduce → Plan → Refine → Verify → Commit
              │
              ▼
Source-aware contributions → Wiki + Graph + Embeddings
              │
       ┌──────┼────────┐
       ▼      ▼        ▼
    Chatbot   MCP   NotebookLM
```

## Cấu trúc thư mục tài liệu

```text
docs/
├── README.md                    # Mục lục
├── DEVELOPMENT-REPORT-V2.md    # Báo cáo phát triển v2
├── DESIGN_DOCUMENT.md          # Thiết kế và changelog
├── ARCHITECTURE.md             # Kiến trúc kỹ thuật
├── API-REFERENCE.md            # API reference
├── WIKI.md                     # Wiki, MRP, source-aware provenance
├── NOTEBOOKLM-INTEGRATION.md   # NotebookLM
├── ACCESS-CONTROL.md           # RBAC
├── ADMIN-GUIDE.md              # Quản trị
├── QUICKSTART.md               # Bắt đầu nhanh
├── SETUP.md                    # Triển khai
├── HOW_TO_RUN.md               # Phát triển local
├── MCP.md                      # MCP integration
├── SKILLS.md                   # AI Skills
├── WORKSPACES.md               # Workspace
├── KNOWLEDGE-TYPES.md          # Taxonomy
└── TROUBLESHOOTING.md          # Xử lý sự cố
```

## Trạng thái release v2

- Backend và frontend: `2.0.0`
- Database migration: `023`
- Production entrypoint: `http://localhost:3119`
- Health check: `GET /health` qua API container
- Source release: commit `1dd7c2a`
