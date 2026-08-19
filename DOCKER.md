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

> **Deploy vẫn là một lệnh.** `api` có `depends_on: migrate` với
> `condition: service_completed_successfully`, nên `up -d` tự chạy migration trước rồi mới
> khởi động API — không thể bị bỏ sót. Điểm khác so với trước là migration chạy trong một
> service one-shot riêng (`restart: "no"`), không còn nằm trong entrypoint của mọi container.
>
> **Và nó fail-closed.** Nếu migration bị từ chối vì có bước phá dữ liệu, `migrate` exit 1,
> `api` **không bao giờ start**, và schema giữ nguyên. Đã kiểm chứng: database ở revision
> `013`, chạy `up -d api` → `migrate exit=1`, `api state=created`, schema vẫn `013`. API
> không bao giờ chạy trên schema cũ.

```bash
# Build và khởi động toàn bộ stack (migration chạy tự động, trước api)
docker compose --env-file .env.docker up -d --build

# Chỉ chạy migration, không khởi động gì khác — dùng khi cần override hoặc kiểm tra trước
docker compose --env-file .env.docker run --rm migrate

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

> **`restart` giờ đã an toàn.** Trước đây `entrypoint.sh` chạy `alembic upgrade head` mỗi lần
> container backend start — kể cả khi chỉ `restart` — nên `restart worker` cũng chạy migration lên
> database production, và `api` / `worker` / `worker_skills` đua nhau migrate cùng một database lúc
> boot mà không có lock. Một migration lỗi cộng `set -e` cộng `restart: always` là crash-loop không
> có circuit breaker.
>
> Bây giờ chỉ service `migrate` chạy migration, và nó có `restart: "no"` — lỗi thì dừng một lần rồi
> nằm im, không loop. `entrypoint.sh` chỉ còn làm việc hạ quyền (chạy như `appuser`, không phải
> root).

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
# `up -d` đã tự chạy migration. Lệnh dưới đây chỉ cần khi muốn chạy riêng — ví dụ để xem
# pre-flight nói gì, hoặc để dùng ALLOW_DESTRUCTIVE_MIGRATIONS. KHÔNG exec vào arkon_api.
docker compose --env-file .env.docker run --rm migrate

# Chỉ kiểm tra, không ghi gì
docker compose --env-file .env.docker run --rm migrate ./migrate.sh --check

# Xem migration hiện tại
docker exec arkon_api alembic current

# Xem lịch sử migration
docker exec arkon_api alembic history

# Tạo migration mới
docker exec arkon_api alembic revision --autogenerate -m "ten_migration"
```

> **Bắt buộc đọc file sinh ra trước khi commit.** `alembic/env.py` giờ đã có `include_object`
> filter, nên autogenerate không còn đề xuất `drop_index` cho các index tạo bằng raw SQL — đó chính
> là cách migration `018` đã xóa 4 index (unique index trên `wiki_pages.slug` và 3 GIN index) mà
> không ai để ý. Nhưng filter chỉ chặn được thứ nó biết: index/bảng nào bạn tạo mà không khai báo
> trong `models.py` vẫn sẽ bị đề xuất xóa ở lần autogenerate sau.
>
> **Migration xóa cột hoặc bảng sẽ bị từ chối.** `migrate.sh` quét trước phần `upgrade()` của các
> revision chưa áp dụng, và nếu thấy `drop_table` / `drop_column` / `DROP` / `TRUNCATE` /
> `DELETE FROM` thì dừng lại, in ra đúng file và số dòng, và không ghi gì cả. Database mới (chưa có
> `alembic_version`) được miễn — không có dữ liệu nào để mất. Muốn chạy thật thì backup trước rồi:
>
> ```bash
> docker exec arkon_postgres pg_dump -U arkon -d arkon -Fc > arkon-$(date +%F).dump
> ALLOW_DESTRUCTIVE_MIGRATIONS=1 docker compose --env-file .env.docker run --rm migrate
> ```
>
> Xem `alembic/README.md` để biết quy tắc khi cần xóa một cột đang có dữ liệu.

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
