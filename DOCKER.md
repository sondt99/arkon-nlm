# Arkon — Docker Commands

Tất cả lệnh Docker cho project này. Chạy từ **thư mục gốc của repo** và dùng đường dẫn tương đối —
không hardcode đường dẫn tuyệt đối (phiên bản trước của file này pin `E:\AI-CLAUDE\arkon`, và mọi
lệnh trong đó đã hỏng khi repo được chuyển đi).

> **Luôn truyền `--env-file .env.docker`.** Các service `postgres` / `redis` / `minio` resolve
> `${POSTGRES_PASSWORD}`, `${REDIS_PASSWORD}`, `${MINIO_SECRET_KEY}`, `${NGINX_PORT}` … qua Compose
> variable substitution, và cơ chế này **chỉ** đọc shell environment hoặc file được chỉ định bởi
> `--env-file`. Khai báo `env_file: [.env.docker]` trên các backend service **không** cung cấp giá
> trị cho substitution — nó chỉ inject biến vào process bên trong container đó.
>
> Nếu thiếu `--env-file`, Compose âm thầm dùng placeholder password trong `docker-compose.yml`
> (`change-me-postgres-password`, `change-me-redis-password`, …) cho postgres/redis/minio, trong khi
> api/worker vẫn dùng password thật từ `.env.docker`. Kết quả: auth thất bại và worker crash-loop
> dưới `restart: always`. Đã kiểm chứng bằng `docker compose config`.
>
> Kiểm tra nhanh: `docker compose --env-file .env.docker config | grep -i password` phải hiện giá trị
> thật, không phải `change-me-*`.

---

## Khởi động / Build

```bash
# Build và khởi động toàn bộ stack
docker compose --env-file .env.docker up -d --build

# Build và restart một service cụ thể (thường dùng nhất)
docker compose --env-file .env.docker up -d --build api
docker compose --env-file .env.docker up -d --build frontend
docker compose --env-file .env.docker up -d --build worker
docker compose --env-file .env.docker up -d --build worker_skills

# Build nhiều service cùng lúc
docker compose --env-file .env.docker up -d --build api frontend
docker compose --env-file .env.docker up -d --build api worker worker_skills

# Khởi động lại không build (dùng image cũ)
docker compose --env-file .env.docker up -d
```

Lần đầu trên máy mới, cần tạo network và file config trước:

```bash
docker network create arkon_default    # một lần cho mỗi máy
cp .env.docker.example .env.docker     # rồi sửa — xem "Quick start" trong README.md
```

---

## Dừng / Xóa

```bash
# Dừng tất cả container (giữ lại volumes)
docker compose --env-file .env.docker down

# Dừng và xóa volumes (reset DB, Redis, MinIO — KHÔNG THỂ HOÀN TÁC)
docker compose --env-file .env.docker down -v

# Dừng một service cụ thể
docker compose --env-file .env.docker stop api
docker compose --env-file .env.docker stop frontend

# Restart một service (không build lại)
docker compose --env-file .env.docker restart api
docker compose --env-file .env.docker restart worker
```

> **Lưu ý:** `entrypoint.sh` chạy `alembic upgrade head` mỗi lần container backend start — kể cả khi
> chỉ `restart`. Nghĩa là `restart worker` cũng chạy migration lên database production. Nếu một
> migration lỗi, `set -e` cộng `restart: always` tạo thành crash-loop không có circuit breaker.

---

## Xem trạng thái

```bash
# Xem tất cả container và trạng thái
docker compose --env-file .env.docker ps

# Xem các service được định nghĩa
docker compose --env-file .env.docker config --services

# Xem config sau khi substitution (dùng để verify .env.docker đã được đọc)
docker compose --env-file .env.docker config
```

---

## Logs

```bash
# Tail log realtime
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f frontend
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker logs -f worker_skills
docker compose --env-file .env.docker logs -f nginx
docker compose --env-file .env.docker logs -f postgres
docker compose --env-file .env.docker logs -f redis

# Log N dòng cuối (không tail)
docker compose --env-file .env.docker logs --tail=100 api

# Log tất cả service cùng lúc
docker compose --env-file .env.docker logs -f
```

---

## Exec vào container

```bash
# Mở shell tương tác
docker exec -it arkon_api bash
docker exec -it arkon_frontend sh
docker exec -it arkon_postgres psql -U arkon -d arkon

# Chạy lệnh một lần
docker exec arkon_api python -c "from app.routers.chat import router; print('OK')"
```

> `arkon_api` chạy với `read_only: true`. Lệnh nào cần ghi file trong container sẽ fail, trừ khi ghi
> vào một trong các `tmpfs` mount đã cấu hình.

**Không chạy pytest trong container.** Thư mục `tests/` không được copy vào image (`Dockerfile`
chỉ copy `app/`, `alembic/`, `alembic.ini`, `skills/`) và `pytest` không được cài (`pip install .`
không bao gồm extra `dev`). Chạy test trên host:

```bash
uv run --extra dev pytest tests/ -q
```

---

## Alembic (DB migrations)

```bash
# Áp dụng migration mới nhất
docker exec arkon_api alembic upgrade head

# Xem migration hiện tại
docker exec arkon_api alembic current

# Xem lịch sử migration
docker exec arkon_api alembic history

# Tạo migration mới
docker exec arkon_api alembic revision --autogenerate -m "ten_migration"
```

> **Bắt buộc đọc file sinh ra trước khi commit.** `alembic/env.py` chưa có `include_object` filter,
> nên autogenerate không thấy các index được tạo bằng raw SQL và sẽ đề xuất `drop_index` cho chúng.
> Đây chính là cách migration `018` đã xóa 4 index (gồm unique index trên `wiki_pages.slug` và 3 GIN
> index) mà không ai để ý.

---

## Danh sách container

| Container | Service | Vai trò |
|-----------|---------|---------|
| `arkon_api` | `api` | FastAPI backend :5055 (không publish ra host) |
| `arkon_frontend` | `frontend` | Next.js :3000 (không publish; truy cập qua nginx) |
| `arkon_worker` | `worker` | arq worker — document ingestion |
| `arkon_worker_skills` | `worker_skills` | arq worker — AI skills |
| `arkon_postgres` | `postgres` | PostgreSQL + pgvector |
| `arkon_redis` | `redis` | Redis (arq job queue) |
| `arkon_minio` | `minio` | MinIO object storage |
| `arkon_nginx` | `nginx` | Reverse proxy — port duy nhất được publish |

---

## URL truy cập

| Dịch vụ | URL |
|---------|-----|
| App (qua nginx) | `http://localhost:3119` |
| API | `http://arkon_api:5055` trong Compose network — **không publish ra host**; dùng `docker exec` hoặc đi qua nginx |
| MinIO console | port 9001 trong network, không publish mặc định |

nginx bind vào `127.0.0.1:${NGINX_PORT:-3119}:80`, nên chỉ truy cập được từ chính máy host.

---

## Workflow thường dùng

```bash
# Sau khi sửa backend Python
docker compose --env-file .env.docker up -d --build api

# Sau khi sửa frontend TypeScript/TSX
docker compose --env-file .env.docker up -d --build frontend

# Sau khi sửa worker (app/worker.py)
docker compose --env-file .env.docker up -d --build worker worker_skills

# Sau khi thêm migration DB — entrypoint tự chạy upgrade khi api start,
# lệnh dưới chỉ cần khi muốn chạy thủ công
docker compose --env-file .env.docker up -d --build api
docker exec arkon_api alembic upgrade head

# Full rebuild (sau khi thay đổi dependencies hoặc Dockerfile)
docker compose --env-file .env.docker up -d --build
```
