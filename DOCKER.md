# Arkon — Docker Commands

Tất cả lệnh Docker cho project này. Luôn dùng flag `-f` với đường dẫn đầy đủ vì
`cd` không persist giữa các lần chạy PowerShell.

---

## Khởi động / Build

```powershell
# Build và khởi động toàn bộ stack
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build

# Build và restart một service cụ thể (thường dùng nhất)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build api
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build frontend
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build worker
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build worker_skills

# Build nhiều service cùng lúc
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build api frontend
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build api worker worker_skills

# Khởi động lại không build (dùng image cũ)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d
```

---

## Dừng / Xóa

```powershell
# Dừng tất cả container (giữ lại volumes)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" down

# Dừng và xóa volumes (reset DB, Redis, MinIO — KHÔNG THỂ HOÀN TÁC)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" down -v

# Dừng một service cụ thể
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" stop api
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" stop frontend

# Restart một service (không build lại)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" restart api
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" restart worker
```

---

## Xem trạng thái

```powershell
# Xem tất cả container và trạng thái
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" ps

# Xem các service được định nghĩa
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" config --services
```

---

## Logs

```powershell
# Tail log realtime
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f api
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f frontend
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f worker
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f worker_skills
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f nginx
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f postgres
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f redis

# Log N dòng cuối (không tail)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs --tail=100 api

# Log tất cả service cùng lúc
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f
```

---

## Exec vào container

```powershell
# Mở shell tương tác
docker exec -it arkon_api bash
docker exec -it arkon_frontend sh
docker exec -it arkon_postgres psql -U arkon -d arkon

# Chạy lệnh một lần
docker exec arkon_api python -c "from app.routers.chat import router; print('OK')"
docker exec arkon_api python -m pytest app/tests/ -x -q
```

---

## Alembic (DB migrations)

```powershell
# Áp dụng migration mới nhất
docker exec arkon_api alembic upgrade head

# Xem migration hiện tại
docker exec arkon_api alembic current

# Xem lịch sử migration
docker exec arkon_api alembic history

# Tạo migration mới (chạy xong phải kiểm tra file sinh ra)
docker exec arkon_api alembic revision --autogenerate -m "ten_migration"
```

---

## Danh sách container

| Container | Service | Vai trò |
|-----------|---------|---------|
| `arkon_api` | `api` | FastAPI backend |
| `arkon_frontend` | `frontend` | Next.js (build + serve) |
| `arkon_worker` | `worker` | arq worker — document ingestion |
| `arkon_worker_skills` | `worker_skills` | arq worker — AI skills |
| `arkon_postgres` | `postgres` | PostgreSQL + pgvector |
| `arkon_redis` | `redis` | Redis (arq job queue) |
| `arkon_minio` | `minio` | MinIO object storage |
| `arkon_nginx` | `nginx` | Reverse proxy, port 3119 |

---

## URL truy cập

| Dịch vụ | URL |
|---------|-----|
| App (qua nginx) | `http://localhost:3119` |
| API trực tiếp | `http://localhost:5055` (không expose mặc định) |
| MinIO console | `http://localhost:9001` (không expose mặc định) |

---

## Workflow thường dùng

```powershell
# Sau khi sửa backend Python
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build api

# Sau khi sửa frontend TypeScript/TSX
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build frontend

# Sau khi sửa worker (app/worker.py)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build worker worker_skills

# Sau khi thêm migration DB
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build api
docker exec arkon_api alembic upgrade head

# Full rebuild (sau khi thay đổi dependencies hoặc Dockerfile)
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build
```
