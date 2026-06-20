# Troubleshooting & FAQ

---

## Chẩn đoán nhanh

Khi có vấn đề, chạy theo thứ tự:

```bash
# 1. Kiểm tra containers đang chạy
docker compose ps

# 2. Kiểm tra health
curl http://localhost:5055/health

# 3. Xem logs của service bị lỗi
docker compose logs --tail=50 api
docker compose logs --tail=50 worker
docker compose logs --tail=50 frontend
```

---

## Vấn đề khởi động

### Containers không start được

**Triệu chứng:** `docker compose up -d` chạy nhưng container bị `Exit` hoặc không lên `healthy`

**Kiểm tra:**
```bash
docker compose ps          # Xem status
docker compose logs api    # Xem lỗi cụ thể
```

**Nguyên nhân thường gặp:**

| Lỗi | Giải pháp |
|---|---|
| `port is already allocated` | Port 5055/3119/9002 đang bị dùng. Tắt ứng dụng khác hoặc đổi port trong docker-compose.yml |
| `database connection refused` | PostgreSQL chưa healthy. Đợi thêm 30s |
| `connection to redis failed` | Redis chưa healthy. Kiểm tra REDIS_PASSWORD |
| `SECRET_KEY not set` | Thêm SECRET_KEY vào .env.docker |

### API health check fail

**Triệu chứng:** `curl http://localhost:5055/health` trả về lỗi hoặc `degraded`

```bash
# Kiểm tra từng service
curl http://localhost:5055/api/health
# → {"api": "healthy", "database": "error", "worker": "error"}
```

Nếu `database: error`:
```bash
docker compose logs postgres
# Thường do POSTGRES_PASSWORD không khớp với DATABASE_URL
```

Nếu `worker: error`:
```bash
docker compose logs redis
# Kiểm tra REDIS_PASSWORD trong .env.docker
```

---

## Vấn đề Upload & Xử lý tài liệu

### Upload bị lỗi 500

**Triệu chứng:** Upload file → ngay lập tức nhận lỗi 500

```bash
docker compose logs api | grep "ERROR"
```

**Nguyên nhân thường gặp:**
- MinIO chưa ready → `curl http://localhost:5055/health` kiểm tra minio status
- File quá lớn qua Next.js proxy → Upload trực tiếp lên port 5055:
  ```
  Bình thường upload đã bypass proxy rồi — nếu vẫn lỗi kiểm tra apiUpload() trong api.ts
  ```

### Tài liệu mãi ở trạng thái `pending`

**Triệu chứng:** Upload thành công nhưng status không thay đổi sau 5 phút

```bash
# Kiểm tra worker đang chạy
docker compose ps worker

# Xem worker logs
docker compose logs -f worker

# Kiểm tra Redis queue
docker exec -it arkon_redis redis-cli -a <REDIS_PASSWORD> LLEN arq:queue:default
```

**Giải pháp:**
```bash
docker compose restart worker
```

### Tài liệu bị lỗi ở `processing`

**Triệu chứng:** Status = `error`, có error_message trong UI

```bash
docker compose logs worker | grep -A 5 "ERROR"
```

**Lỗi thường gặp:**

| Lỗi | Nguyên nhân | Giải pháp |
|---|---|---|
| `No llm provider configured` | Chưa cấu hình LLM trong Settings | Vào Settings → cấu hình LLM provider |
| `CharacterNotInRepertoire` | File PDF có null bytes | Đã được fix trong kb_service.py — restart worker |
| `UniqueViolationError source_images` | Re-ingest tạo duplicate ảnh | Đã được fix — restart worker rồi retry |
| `Connection refused to ollama` | Ollama không chạy | Khởi động Ollama trên máy host |
| `Invalid API key` | API key sai | Kiểm tra lại API key trong Settings |

### Tài liệu mãi ở `plan_review`

**Triệu chứng:** Status = `plan_review`, không tự tiến lên

Đây là **hành vi bình thường** khi `MRP_AUTO_APPROVE_PLAN=false`.

**Giải pháp:** Vào Knowledge Base → nhấn **Review Plan** → **Approve**

Hoặc bật auto-approve:
```bash
# Trong .env.docker
MRP_AUTO_APPROVE_PLAN=true

# Restart api
docker compose restart api
```

---

## Vấn đề AI Provider

### Test LLM thất bại

**Triệu chứng:** Settings → Test LLM → `success: false`

**Kiểm tra theo provider:**

| Provider | Kiểm tra |
|---|---|
| Google | API key bắt đầu `AIza`, dùng Google AI Studio (không phải Cloud API) |
| OpenAI | API key bắt đầu `sk-`, kiểm tra billing/quota |
| Anthropic | API key bắt đầu `sk-ant-`, kiểm tra quota |
| Ollama | Ollama đang chạy? `ollama ps`, Base URL = `http://host.docker.internal:11434/v1` |

### Ollama không kết nối được từ container

```bash
# Test từ trong container
docker exec -it arkon_api curl http://host.docker.internal:11434/api/tags

# Nếu fail — Ollama không listen trên 0.0.0.0
# Sửa Ollama service để bind 0.0.0.0:
# Windows: set OLLAMA_HOST=0.0.0.0
```

### Wiki không được tạo dù tài liệu ready

```bash
docker compose logs worker | grep "LLM\|llm\|provider"
```

Thường do LLM provider trả về lỗi silent — kiểm tra API quota.

---

## Vấn đề Hiển thị

### Ảnh wiki không hiển thị

**Triệu chứng:** Wiki page có ảnh nhưng hiển thị broken image

```bash
# Test trực tiếp (thay uuid và token)
curl -v "http://localhost:5055/api/wiki/images/<uuid>?token=<jwt>"
```

**Nguyên nhân:**

| Lỗi | Giải pháp |
|---|---|
| `401 Unauthorized` | Token hết hạn → đăng nhập lại |
| `404 Not Found` | Image chưa được extract → check SourceImage trong DB |
| `502 Bad Gateway` | MinIO lỗi → kiểm tra `docker compose logs minio` |

### Không truy cập được từ IP khác (Tailscale/LAN)

**Triệu chứng:** Từ localhost OK, từ IP khác bị CORS error hoặc 503

**Giải pháp:**
```bash
# Thêm IP vào .env.docker
CORS_ORIGINS=http://localhost:3119,http://100.x.x.x:3119

# Rebuild frontend (NEXT_PUBLIC_API_URL là build-time var)
docker compose build frontend
docker compose up -d frontend
```

> **Quan trọng**: Nếu `NEXT_PUBLIC_API_URL` đang set = localhost, frontend sẽ không hoạt động từ IP khác. Để trống để dùng relative URLs.

### Frontend hiển thị "Failed to fetch"

```bash
# 1. Kiểm tra API chạy không
curl http://localhost:5055/health

# 2. Kiểm tra INTERNAL_API_URL trong container
docker exec arkon_frontend env | grep INTERNAL

# 3. Test proxy
docker exec arkon_frontend wget -q -O- http://api:5055/health
```

---

## Vấn đề Database

### Migration fail khi restart

```bash
docker compose logs api | grep "alembic"
```

**Lỗi thường gặp:**

| Lỗi | Giải pháp |
|---|---|
| `relation does not exist` | Chạy migration thủ công: `docker compose exec api alembic upgrade head` |
| `column already exists` | Đã chạy migration rồi, bỏ qua |
| `connection refused` | PostgreSQL chưa healthy, đợi và retry |

### Database full

```bash
# Kiểm tra dung lượng
du -sh F:\arkon-data\postgres\

# Xem kích thước các bảng lớn
docker exec -it arkon_postgres psql -U arkon arkon -c "
SELECT relname as table, pg_size_pretty(pg_relation_size(relid)) as size
FROM pg_catalog.pg_statio_user_tables
ORDER BY pg_relation_size(relid) DESC
LIMIT 10;"
```

---

## Vấn đề MinIO

### MinIO SignatureDoesNotMatch

**Nguyên nhân:** MINIO_ACCESS_KEY hoặc MINIO_SECRET_KEY không khớp với container đang chạy.

MinIO chỉ đọc credentials lần **đầu tiên** khởi tạo volume. Sau đó thay đổi trong .env không có tác dụng.

**Giải pháp:**
```bash
# Option 1: Dùng đúng credentials cũ
# Tìm lại credentials đã dùng khi tạo container lần đầu

# Option 2: Reset MinIO (MẤT DỮ LIỆU)
docker compose down
rmdir /s F:\arkon-data\minio
mkdir F:\arkon-data\minio
docker compose up -d
```

### MinIO "Invalid Request (invalid hostname)"

**Nguyên nhân:** MINIO_ENDPOINT chứa underscore (vd: `arkon_minio`)

**Giải pháp:** Dùng service name không có underscore:
```bash
MINIO_ENDPOINT=minio:9000  # Tên service trong docker-compose.yml
```

---

## Vấn đề Hiệu suất

### Xử lý tài liệu quá chậm

**Nguyên nhân có thể:**

1. **Model LLM chậm** → Dùng model nhanh hơn (gemini-flash thay gemini-pro)
2. **Tài liệu quá lớn** → Bình thường — tài liệu 500 trang có thể mất 20-30 phút
3. **Worker đang xử lý nhiều job** → Tối đa 3 job song song (WorkerSettings.max_jobs=3)

```bash
# Xem jobs đang chạy
docker exec -it arkon_redis redis-cli -a <password>
> KEYS arq:job:*
> HGETALL arq:job:<job_id>
```

### Tìm kiếm wiki chậm

```bash
# Kiểm tra pgvector index
docker exec -it arkon_postgres psql -U arkon arkon -c "
SELECT indexname, indexdef
FROM pg_indexes
WHERE tablename LIKE 'wiki_page_embeddings%';"
```

Nếu không có index HNSW → Migration 015 chưa chạy:
```bash
docker compose exec api alembic upgrade head
```

---

## Câu hỏi thường gặp

**Q: Có giới hạn kích thước file upload không?**

Backend API: không giới hạn (phụ thuộc MinIO storage).  
Nếu upload qua UI (Next.js proxy): mặc định bypass proxy, upload thẳng lên port 5055. Nếu gặp lỗi 413, kiểm tra `apiUpload()` trong `frontend/src/lib/api.ts`.

---

**Q: Có thể backup và restore không mất dữ liệu không?**

```bash
# Backup đầy đủ
docker exec arkon_postgres pg_dump -U arkon arkon > db_backup.sql
xcopy F:\arkon-data\minio\ backup_minio\ /E /I

# Restore
docker exec -i arkon_postgres psql -U arkon arkon < db_backup.sql
xcopy backup_minio\ F:\arkon-data\minio\ /E /I
```

---

**Q: Đổi embedding model có mất dữ liệu wiki không?**

Không. Mỗi dimension có bảng riêng (768d, 1024d, 1536d, 3072d). Đổi model chỉ cần chạy **Re-embed all pages** để tạo embeddings mới trong bảng mới. Dữ liệu wiki (content_md) không bị ảnh hưởng.

---

**Q: Claude không tìm thấy thông tin dù đã upload tài liệu?**

Kiểm tra:
1. Tài liệu status = `ready` (không phải `processing` hay `error`)
2. Wiki pages đã được tạo: **Wiki → Browse**
3. MCP token của Claude có `allowed_knowledge_types` phù hợp
4. Embedding đã được tạo: tìm kiếm thử trong Wiki search

---

**Q: Làm sao xem tài liệu nào đang được xử lý?**

```bash
# Xem jobs đang chạy
docker compose logs -f worker | grep "ingest"

# Đếm jobs trong queue
docker exec -it arkon_redis redis-cli -a <password> LLEN arq:queue:default
```

---

**Q: Worker crash giữa chừng, tài liệu bị kẹt ở phase nào?**

`source.pipeline_phase` lưu phase cuối hoàn thành. Worker sẽ **tự resume** từ phase đó khi restart. Không cần làm gì thêm.

Nếu muốn restart thủ công từ đầu:
```bash
# Qua API
curl -X POST "http://localhost:5055/api/sources/{id}/retry" \
  -H "Authorization: Bearer <token>"
```

**Lưu ý về dữ liệu đã lưu:** log `REFINE complete` hoặc `VERIFY complete` chỉ xác nhận kết quả tạm trong bộ nhớ. Wiki và provenance chỉ được lưu khi có `MRP COMMIT complete`, source ở trạng thái `ready`, progress `100` và contribution đã xuất hiện. COMMIT là transaction atomic; nếu một page lỗi, toàn bộ thay đổi của lần commit được rollback.

---

**Q: Log có `InvalidRequestError: This session is provisioning a new connection`?**

Đây là lỗi của phiên bản cũ khi nhiều writer song song dùng chung một SQLAlchemy `AsyncSession`. Bản hotfix 2026-06-21 đã thay bằng snapshot wiki đọc trước khi fan-out. Hãy rebuild/restart `api` và `worker`, sau đó retry source lỗi:

```bash
docker compose build api
docker compose up -d api worker worker_skills
```

Với tài liệu lớn, cấu hình `WORKER_JOB_TIMEOUT=3600` hoặc cao hơn. Khi AI gateway trả 504, writer tự retry tối đa 3 lần; nếu vẫn lỗi, job dừng và không tạo trang placeholder.

---

**Q: Tại sao cần cả hai worker (worker và worker_skills)?**

Worker chính xử lý document ingestion + MRP pipeline (nặng, lâu).  
Worker skills xử lý skill packages (.zip) — được tách riêng để upload skill không block ingestion tài liệu quan trọng.

---

**Q: Có thể chạy nhiều worker không?**

Không khuyến nghị với setup hiện tại — một số operations dùng advisory locks theo slug để tránh race condition. Nhiều worker cùng COMMIT phase có thể bị deadlock.

Nếu cần scale, giải pháp tốt hơn là tăng `max_jobs` trong `WorkerSettings`.
