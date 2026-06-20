# Báo cáo phát triển hệ thống Arkon v2

> Phiên bản báo cáo: 2.0<br>
> Ngày cập nhật: 20/06/2026<br>
> Phạm vi: các thay đổi từ nhánh v1.2.x đến commit `1dd7c2a`

## 1. Tóm tắt điều hành

Arkon v2 phát triển từ một nền tảng biên soạn tài liệu thành wiki thành một **Enterprise AI Knowledge Hub** hoàn chỉnh. Đợt phát triển tập trung vào bốn mục tiêu:

1. Nâng độ chính xác khi cập nhật wiki từ nhiều tài liệu.
2. Hoàn thiện trải nghiệm Chatbot, NotebookLM và Knowledge Graph.
3. Chuẩn hóa giao diện, nhận diện và khả năng hiển thị đa thiết bị.
4. Bảo đảm vận hành an toàn bằng migration, Docker health check và cơ chế fallback không làm mất tri thức.

Kết quả chính là cơ chế **Source-aware Knowledge Provenance**: hệ thống lưu riêng phần tri thức do từng tài liệu đóng góp, sau đó tổng hợp thành wiki page. Khi xóa tài liệu, Arkon có thể xóa đúng contribution tương ứng và dựng lại trang từ các nguồn còn lại.

## 2. Phạm vi chức năng hiện tại

| Phân hệ | Năng lực hiện tại |
|---|---|
| Knowledge Base | Upload file, URL, ZIP; phân loại, scope, phòng ban, theo dõi tiến trình |
| MRP Pipeline | Map → Reduce → Plan Review → Refine → Verify → Commit |
| Wiki | Entity, concept, topic, source, synthesis; revision; draft; backlink |
| Source-aware Knowledge | Provenance theo source, rebuild khi xóa, preview tác động |
| Chatbot AI | RAG theo wiki, hội thoại, streaming UI, Markdown/code, copy, Add to Wiki |
| NotebookLM | Quản lý notebook, gửi source, chat, tạo artifact và import lại Arkon |
| Knowledge Graph | Graph 2D tương tác, lọc loại node, zoom, focus, scoped graph |
| AI Skills | Quản lý skill, version, contribution workflow và scope truy cập |
| MCP | Cung cấp tri thức Arkon cho Claude Desktop/Code theo quyền |
| Quản trị | Employee, department, role, workspace, audit và provider settings |

## 3. Kiến trúc triển khai

```text
Browser
  │
  ▼
Nginx :3119
  ├── Next.js frontend
  ├── FastAPI /api + /mcp
  └── MinIO file gateway

FastAPI ── PostgreSQL + pgvector
   │       ├── nguồn tài liệu
   │       ├── wiki + contribution provenance
   │       └── vector embedding
   │
   ├── Redis ── arq ingestion worker
   ├── arq skill worker
   ├── MinIO object storage
   └── AI providers / NotebookLM
```

Các container production hiện gồm: `arkon_nginx`, `arkon_frontend`, `arkon_api`, `arkon_worker`, `arkon_worker_skills`, `arkon_postgres`, `arkon_redis`, `arkon_minio`.

## 4. Hạng mục phát triển nổi bật

### 4.1 Source-aware Knowledge Provenance

#### Vấn đề trước đây

`wiki_pages.source_ids` chỉ cho biết trang wiki liên quan đến source nào. Nội dung của nhiều source đã được merge thành một khối Markdown duy nhất, nên không thể xác định chính xác câu/đoạn nào thuộc tài liệu nào. Khi xóa source B khỏi trang dùng chung với source A, hệ thống chỉ bỏ ID của B nhưng nội dung do B đóng góp có thể vẫn còn.

#### Giải pháp v2

- Thêm bảng `wiki_page_contributions`, unique theo `(page_id, source_id)`.
- Lưu nội dung, summary, source title và knowledge type của từng contribution.
- `wiki_pages` trở thành canonical synthesis; `source_ids` là chỉ mục denormalized.
- Thêm `wiki_pages.provenance_complete` để phân biệt trang có thể rebuild chính xác và trang legacy.
- Khi compile lại cùng source, contribution được upsert thay vì cộng dồn trùng lặp.
- Khi source mới cập nhật concept chung, trang được dựng từ toàn bộ contribution hiện hành.
- Khi LLM merge lỗi hoặc output bị co ngắn bất thường, fallback ghép lossless giữ cả hai đầu vào.

#### Xóa source

```text
Yêu cầu xóa source
   │
   ├── Preview: GET /api/sources/{id}/knowledge-impact
   │      ├── delete_page
   │      ├── rebuild_page
   │      └── detach_legacy
   │
   └── DELETE /api/sources/{id}
          ├── xóa contribution của source
          ├── xóa page nếu không còn contribution
          ├── rebuild page nếu còn source khác
          ├── refresh wikilinks
          └── refresh embedding
```

Migration `023` backfill lossless các trang single-source. Trang multi-source lịch sử không thể tách ngược nội dung một cách chắc chắn nên được đánh dấu `provenance_complete=false`; hệ thống không tự đoán và xóa nội dung của các trang này.

### 4.2 Tối ưu Compilation Plan

- Reconciliation đối chiếu entity/concept mới với wiki theo slug, title, lexical similarity và semantic candidates.
- Plan `CREATE` được chuyển thành `UPDATE` khi tìm thấy trang tương ứng.
- Nhóm chứa cả concept cũ và mới được tách thành update/create riêng.
- Kiểm tra lại collision tại thời điểm approve để xử lý race condition.
- Review Plan UI hiển thị lý do match, confidence và cho phép chỉnh sửa plan.

Kết quả: giảm số concept trùng lặp và tăng tỷ lệ cập nhật đúng trang cũ.

### 4.3 Chatbot AI

- Tin nhắn người dùng được optimistic render ngay khi gửi.
- Phản hồi hiển thị theo luồng, giảm cảm giác chờ toàn bộ response.
- Renderer dùng `react-markdown` và `remark-gfm`: heading, list, table, quote và code block.
- Có copy toàn bộ message và copy nhanh từng code block.
- RAG tìm wiki bằng embedding, mở rộng qua wiki links và đưa context vào LLM.
- Hỗ trợ lịch sử hội thoại, đổi tên, sửa/regenerate và Add to Wiki.
- Add to Wiki bảo toàn code block, quy trình, ví dụ và giải thích kỹ thuật.
- Hai persona Victor/Ashley; Ashley là lựa chọn mặc định và vẫn tuân thủ KB-only.

### 4.4 NotebookLM

- Import và kiểm tra session Google NotebookLM.
- Tạo, liệt kê, mở và xóa notebook trong giao diện Arkon.
- Gửi source Arkon sang notebook mà không bắt buộc chọn thủ công ở màn hình chat.
- Chat với notebook và quản lý lịch sử theo notebook.
- Tạo artifact: audio, video, report, quiz, flashcards, slide deck, infographic, data table.
- Import artifact/report về Arkon wiki với namespace riêng.
- Worker nền xử lý job dài và duy trì session.

NotebookLM là enrichment layer, không thay thế MRP và không ghi đè trực tiếp contribution của MRP.

### 4.5 Giao diện Arkon v2

- Version backend/frontend đồng bộ `2.0.0`.
- Theme sáng/tối, lưu preference và tránh flash theme khi tải trang.
- Responsive header/sidebar và mobile header.
- Login animation chỉ xoay indicator; label button không còn xoay theo spinner.
- Chuẩn hóa màu badge theo semantic type, tránh phối màu foreground/background xung đột.
- Thay icon/logo Arkon ở favicon, app icon, header và sidebar.
- Chuẩn hóa card, input, table, empty state, page header và shadow.

### 4.6 Knowledge Graph v2

- Chuyển sang force graph 2D tương tác.
- Node style theo page type, có label, glow/selection state và dark-mode colors.
- Zoom, pan, fit graph, focus node, neighborhood và tooltip.
- Bộ lọc node type, legend, thống kê và loading state.
- Graph toàn cục và graph theo workspace dùng cùng concept UI.

### 4.7 Upload và quản lý tài liệu

- Upload ZIP tạo nhiều source và enqueue riêng từng ingestion job.
- Chống ZipSlip, zip bomb, file quá lớn, metadata junk và extension không hợp lệ.
- Wiki list tăng giới hạn để không ẩn trang cũ khi hệ thống có nhiều hơn 200 pages.
- Sửa lỗi search bị mất khi kết hợp Knowledge Type filter.
- Hiển thị synthesis pages trong tab và search dialog.

## 5. Thay đổi dữ liệu và migration

| Migration | Nội dung |
|---|---|
| `021` | NotebookLM integration |
| `022` | Chat conversations và chat messages |
| `023` | `wiki_page_contributions`, `provenance_complete`, backfill provenance |

Schema mới quan trọng:

```text
sources
   │ 1
   │
   │ N
wiki_page_contributions
   │ N
   │
   │ 1
wiki_pages ── wiki_links / revisions / embeddings
```

## 6. API mới hoặc thay đổi

| Method | Endpoint | Mục đích |
|---|---|---|
| GET | `/api/sources/{id}/knowledge-impact` | Xem trước ảnh hưởng khi xóa source |
| DELETE | `/api/sources/{id}` | Xóa contribution và rebuild tri thức còn lại |
| GET | `/api/wiki/pages/{slug}` | Trả thêm provenance và danh sách source có tên |
| POST | `/api/sources/upload-zip` | Upload nhiều tài liệu trong ZIP |
| POST | `/api/chat/conversations/{id}/messages` | RAG chatbot response |
| POST | `/api/chat/conversations/{id}/to-wiki` | Chuyển hội thoại thành synthesis page |

## 7. Kiểm thử và triển khai

Các kiểm tra đã thực hiện cho release Source-aware Knowledge:

- Python source và migration compile thành công.
- Targeted tests cho reconciliation và lossless merge đạt.
- Next.js production build và TypeScript check đạt.
- Docker images backend/frontend build thành công.
- Migration database đạt `023 (head)`.
- API, frontend và các service phụ trợ ở trạng thái healthy.
- Commit local và Gitea được xác minh cùng hash `1dd7c2afd3547bb6596627e795e1783d32e47198`.

## 8. Tác động đạt được

| Tiêu chí | Trước v2 | Sau v2 |
|---|---|---|
| Truy vết tri thức | Theo page/source ID | Theo contribution của từng source |
| Xóa tài liệu dùng chung | Có thể để lại nội dung | Rebuild từ nguồn còn lại |
| Lỗi LLM merge | Có nguy cơ mất đầu vào cũ | Lossless fallback |
| Review plan | Chủ yếu tạo concept mới | Reconcile CREATE/UPDATE |
| Chat UX | Chờ response, format hạn chế | Optimistic, streaming, Markdown/copy |
| Graph | Khó theo dõi ở tập lớn | Tương tác, filter, focus, semantic style |
| Theme/mobile | Chưa đồng nhất | Light/dark và responsive |

## 9. Hạn chế và rủi ro còn lại

- Các trang multi-source tạo trước migration `023` chỉ có provenance legacy; cần re-ingest source để đạt provenance đầy đủ.
- NotebookLM phụ thuộc session cookie Google và có thể hết hạn.
- LLM merge có chi phí và độ trễ; fallback bảo toàn dữ liệu nhưng có thể kém mượt về văn phong.
- Wiki list hiện tăng limit lớn; quy mô rất lớn nên chuyển sang pagination/virtualization.
- Cần bổ sung integration test tự động với PostgreSQL, Redis, MinIO và worker thật trong CI.

## 10. Đề xuất giai đoạn tiếp theo

1. Re-ingest có kiểm soát các source legacy để hoàn thiện provenance.
2. Thêm màn hình provenance diff theo từng source và từng wiki page.
3. Chạy source rebuild bất đồng bộ cho các trang có nhiều contribution.
4. Bổ sung citation từ đoạn wiki về source/chunk gốc trong chatbot.
5. Thiết lập CI: migration test, backend tests, frontend lint/build và Docker smoke test.
6. Bổ sung dashboard chất lượng: duplicate concepts, orphan pages, legacy provenance và retrieval hit rate.

## 11. Mốc source code

| Commit | Nội dung |
|---|---|
| `1dd7c2a` | Source-aware knowledge provenance |
| `9d583d6` | Arkon v2 UI và knowledge workflows |
| `36d409b` | Wiki limit và search/filter changelog |
| `31fee50` | Bảo toàn nội dung khi Add to Wiki |
| `12f1d65` | ZIP archive upload an toàn |
