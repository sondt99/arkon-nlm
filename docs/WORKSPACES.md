# Workspaces — Không gian làm việc nhóm

Workspace (hay Project) là không gian làm việc riêng biệt cho một nhóm hoặc dự án cụ thể. Tài liệu và wiki trong workspace chỉ visible với members của workspace đó.

---

## Workspace vs. Global

| | Global | Workspace |
|---|---|---|
| Tài liệu | Mọi người có `doc:read` | Chỉ workspace members |
| Wiki pages | Mọi người có `wiki:read` | Chỉ workspace members |
| Kiểm soát | Phòng ban (RBAC) | Membership roles |
| Tạo bởi | — (mặc định) | System admin |
| Ví dụ | Chính sách công ty, SOP chung | Dự án Alpha, Team Security |

---

## Tạo và quản lý Workspace

> Chỉ **system admin** mới có thể tạo và xóa workspace.

### Tạo workspace

**Admin Portal → Workspaces → New Workspace**

```
Name:        Tên workspace (vd: "Dự án Alpha 2026")
Description: Mô tả mục đích
```

### Thêm member

**Workspaces → [tên] → Members → Add Member**

Chọn nhân viên và gán **workspace role**:

| Workspace Role | Quyền trong workspace |
|---|---|
| **Viewer** | Đọc wiki, xem danh sách tài liệu và members |
| **Contributor** | + Đề xuất chỉnh sửa wiki (tạo draft) |
| **Editor** | + Chỉnh sửa wiki trực tiếp, approve/reject drafts, upload tài liệu |
| **Admin** | + Thêm/xóa members, thay đổi workspace roles |

> **Lưu ý**: System admin (`role=admin`) luôn có quyền truy cập đầy đủ vào mọi workspace mà không cần là member.

### Bảo vệ last admin

Workspace admin cuối cùng **không thể** bị xóa hoặc downgrade. Phải assign admin khác trước.

---

## Upload tài liệu vào Workspace

Khi upload, chọn **Scope = Project** và chọn workspace:

```
Knowledge Base → Upload
  → Scope: Project
  → Project: [chọn workspace]
```

Tài liệu workspace:
- Chỉ members của workspace thấy
- Wiki pages được tạo ra cũng scoped vào workspace đó
- Không xuất hiện trong global wiki search của người ngoài

---

## Wiki trong Workspace

### Workspace wiki

**Workspaces → [tên] → Wiki tab**

Wiki của workspace là subset của global wiki — chỉ các trang được compile từ tài liệu workspace đó.

### Quyền chỉnh sửa wiki workspace

| Hành động | Quyền cần |
|---|---|
| Đọc wiki | Viewer+ |
| Đề xuất draft | Contributor+ |
| Chỉnh sửa trực tiếp | Editor+ |
| Approve/reject drafts | Editor+ |
| Xóa wiki page | Editor+ (hoặc system admin) |

Workspace roles **độc lập** với global roles. Một nhân viên có `wiki:write:all` global vẫn **không** tự động có quyền trong workspace nếu không phải member.

---

## Workspace Sources

**Workspaces → [tên] → Sources tab**

Xem và quản lý tài liệu trong workspace:
- Upload tài liệu mới vào workspace
- Theo dõi trạng thái xử lý
- Xóa tài liệu khỏi workspace

---

## Knowledge Graph Workspace

**Workspaces → [tên] → Graph tab**

Hiển thị knowledge graph chỉ với các wiki pages thuộc workspace — giúp nhóm hiểu cấu trúc tri thức của dự án.

---

## MCP và Workspace

Khi nhân viên connect Claude qua MCP, họ chỉ thấy:
- Global resources phù hợp với role của họ
- Workspace resources của các workspace họ là member

Không cần cấu hình thêm — workspace scoping tự động áp dụng.

---

## Xóa Workspace

> Chỉ system admin mới xóa được workspace.

**Workspaces → [tên] → Settings → Delete Workspace**

Xóa workspace sẽ xóa:
- Tất cả membership records
- Tài liệu scoped vào workspace này
- Wiki pages scoped vào workspace này

**Không bị ảnh hưởng**: nhân viên là member (chỉ xóa membership, không xóa tài khoản).

---

## Use Cases điển hình

### Workspace cho Dự án

```
Workspace: "Dự án XYZ Q2 2026"
Members:
  - Project Manager → Editor
  - Developers (3 người) → Contributor
  - Stakeholders → Viewer

Tài liệu trong workspace:
  - Project brief, requirements
  - Meeting notes
  - Technical specs

Wiki tự động được tạo về: requirements, architecture decisions,
stakeholders, timelines
```

### Workspace cho Nhóm bảo mật

```
Workspace: "Security Team"
Members:
  - CISO → Admin
  - Security analysts → Editor
  - IT ops → Contributor

Tài liệu trong workspace:
  - Threat intelligence reports (confidential)
  - Incident reports
  - Penetration test results

Wiki: threat landscape, incident history, playbooks
(Không ai bên ngoài nhóm thấy được)
```

### Workspace cho khách hàng cụ thể

```
Workspace: "Khách hàng Acme Corp"
Members:
  - Account manager → Admin
  - Support team → Editor
  - Onboarding team → Contributor

Tài liệu: contracts, meeting notes, custom requirements
Wiki: customer profile, integration specs, history
```

---

## Câu hỏi thường gặp

**Q: Nhân viên có thể thuộc nhiều workspace không?**  
A: Có, không giới hạn số workspace.

**Q: Workspace role ảnh hưởng đến global permissions không?**  
A: Không — hai realm hoàn toàn độc lập.

**Q: Admin tạo workspace có tự động là member không?**  
A: System admin luôn có full access mà không cần là member. Nhưng nếu muốn thấy workspace trong UI như một member bình thường, cần tự add vào.

**Q: Có thể chuyển tài liệu từ global sang workspace không?**  
A: Qua API: `PATCH /api/sources/{id}` với `scope_type=project, scope_id=<workspace_id>`. Hoặc xóa và upload lại với scope = project.

**Q: Workspace wiki có bị index vào global search không?**  
A: Không — workspace wiki chỉ tìm kiếm được trong workspace wiki search, không xuất hiện trong global search của người ngoài workspace.
