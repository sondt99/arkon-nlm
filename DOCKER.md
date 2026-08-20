# Arkon — Docker Commands

All Docker commands for this project. Run them from the **repo root** and use relative
paths — do not hardcode an absolute path (an earlier version of this file pinned
`E:\AI-CLAUDE\arkon`, and every command in it broke when the repo moved).

> **Always pass `--env-file .env.docker`.** The `postgres` / `redis` / `minio` services resolve
> `${POSTGRES_PASSWORD}`, `${REDIS_PASSWORD}`, `${MINIO_SECRET_KEY}`, `${NGINX_PORT}` … via Compose
> variable substitution, and that mechanism **only** reads the shell environment or a file given
> with `--env-file`. Declaring `env_file: [.env.docker]` on the backend services does **not**
> feed substitution — it only injects variables into that container's own process.
>
> Without `--env-file`, Compose silently uses the placeholder passwords in `docker-compose.yml`
> (`change-me-postgres-password`, `change-me-redis-password`, …) for postgres/redis/minio, while
> the api/worker still use the real passwords from `.env.docker`. Result: auth fails and the
> workers crash-loop under `restart: always`. Verified with `docker compose config`.
>
> Quick check: `docker compose --env-file .env.docker config | grep -i password` must show the
> real values, not `change-me-*`.

---

## Start / Build

> **Deploy is still one command.** `api` has `depends_on: migrate` with
> `condition: service_completed_successfully`, so `up -d` runs the migration first and only
> then starts the API — it cannot be skipped. The difference from before is that the migration
> runs in its own one-shot service (`restart: "no"`), not in every backend container's
> entrypoint.
>
> **And it fail-closes.** If a migration is rejected because it would destroy data, `migrate`
> exits 1, `api` **never starts**, and the schema is unchanged. Verified: database at revision
> `013`, `up -d api` → `migrate exit=1`, `api state=created`, schema still `013`. The API never
> runs on the old schema.

```bash
# Build and start the whole stack (migration runs automatically, before api)
docker compose --env-file .env.docker up -d --build

# Run only the migration, start nothing else — use to override or inspect first
docker compose --env-file .env.docker run --rm migrate

# Build and restart one service (most common)
docker compose --env-file .env.docker up -d --build api
docker compose --env-file .env.docker up -d --build frontend
docker compose --env-file .env.docker up -d --build worker
docker compose --env-file .env.docker up -d --build worker_skills

# Build several services at once
docker compose --env-file .env.docker up -d --build api frontend
docker compose --env-file .env.docker up -d --build api worker worker_skills

# Restart without building (reuse the existing image)
docker compose --env-file .env.docker up -d
```

First time on a new machine, create the network and config file first:

```bash
docker network create arkon_default    # once per machine
cp .env.docker.example .env.docker     # then edit — see "Quick start" in README.md
```

---

## Stop / Remove

```bash
# Stop every container (keep volumes)
docker compose --env-file .env.docker down

# Stop and remove volumes (reset DB, Redis, MinIO — CANNOT BE UNDONE)
docker compose --env-file .env.docker down -v

# Stop one service
docker compose --env-file .env.docker stop api
docker compose --env-file .env.docker stop frontend

# Restart one service (no rebuild)
docker compose --env-file .env.docker restart api
docker compose --env-file .env.docker restart worker
```

> **`restart` is now safe.** Previously `entrypoint.sh` ran `alembic upgrade head` on every
> backend container start — including a plain `restart` — so `restart worker` also migrated
> the production database, and `api` / `worker` / `worker_skills` raced to migrate the same
> database at boot with no lock. A failed migration plus `set -e` plus `restart: always` is a
> crash-loop with no circuit breaker.
>
> Now only the `migrate` service runs migrations, and it has `restart: "no"` — a failure stops
> once and stays down, no loop. `entrypoint.sh` only drops privileges (runs as `appuser`, not
> root).

---

## Status

```bash
# All containers and their state
docker compose --env-file .env.docker ps

# Services defined in the compose file
docker compose --env-file .env.docker config --services

# Config after substitution (use this to verify .env.docker was read)
docker compose --env-file .env.docker config
```

---

## Logs

```bash
# Tail logs in real time
docker compose --env-file .env.docker logs -f api
docker compose --env-file .env.docker logs -f frontend
docker compose --env-file .env.docker logs -f worker
docker compose --env-file .env.docker logs -f worker_skills
docker compose --env-file .env.docker logs -f nginx
docker compose --env-file .env.docker logs -f postgres
docker compose --env-file .env.docker logs -f redis

# Last N lines (no tail)
docker compose --env-file .env.docker logs --tail=100 api

# Logs from every service at once
docker compose --env-file .env.docker logs -f
```

---

## Exec into a container

```bash
# Interactive shell
docker exec -it arkon_api bash
docker exec -it arkon_frontend sh
docker exec -it arkon_postgres psql -U arkon -d arkon

# One-shot command
docker exec arkon_api python -c "from app.routers.chat import router; print('OK')"
```

> `arkon_api` runs with `read_only: true`. A command that needs to write a file inside the
> container will fail unless it writes to one of the configured `tmpfs` mounts.

**Do not run pytest inside the container.** The `tests/` directory is not copied into the image
(`Dockerfile` only copies `app/`, `alembic/`, `alembic.ini`, `skills/`) and `pytest` is not
installed (`pip install .` does not include the `dev` extra). Run tests on the host:

```bash
uv run --extra dev pytest tests/ -q
```

---

## Alembic (DB migrations)

```bash
# `up -d` already ran the migration. The commands below are only for running it separately —
# e.g. to see what pre-flight says, or to use ALLOW_DESTRUCTIVE_MIGRATIONS. Do NOT exec into
# arkon_api.
docker compose --env-file .env.docker run --rm migrate

# Check only, write nothing
docker compose --env-file .env.docker run --rm migrate ./migrate.sh --check

# Current migration
docker exec arkon_api alembic current

# Migration history
docker exec arkon_api alembic history

# Create a new migration
docker exec arkon_api alembic revision --autogenerate -m "migration_name"
```

> **Read the generated file before you commit.** `alembic/env.py` now has an `include_object`
> filter, so autogenerate no longer proposes `drop_index` for indexes created with raw SQL —
> that is how migration `018` deleted 4 indexes (the unique index on `wiki_pages.slug` and 3
> GIN indexes) unnoticed. The filter only blocks what it knows about: an index or table you
> create without declaring it in `models.py` will still be proposed for deletion on the next
> autogenerate.
>
> **A migration that drops a column or table is refused.** `migrate.sh` scans the `upgrade()`
> of unapplied revisions, and if it sees `drop_table` / `drop_column` / `DROP` / `TRUNCATE` /
> `DELETE FROM` it stops, prints the exact file and line, and writes nothing. A fresh database
> (no `alembic_version` yet) is exempt — there is no data to lose. To actually run it, back
> up first, then:
>
> ```bash
> docker exec arkon_postgres pg_dump -U arkon -d arkon -Fc > arkon-$(date +%F).dump
> ALLOW_DESTRUCTIVE_MIGRATIONS=1 docker compose --env-file .env.docker run --rm migrate
> ```
>
> See `alembic/README.md` for the rules when you need to drop a column that already has data.

---

## Container list

| Container | Service | Role |
|-----------|---------|------|
| `arkon_api` | `api` | FastAPI backend :5055 (not published to the host) |
| `arkon_frontend` | `frontend` | Next.js :3000 (not published; reached via nginx) |
| `arkon_worker` | `worker` | arq worker — document ingestion |
| `arkon_worker_skills` | `worker_skills` | arq worker — AI skills |
| `arkon_postgres` | `postgres` | PostgreSQL + pgvector |
| `arkon_redis` | `redis` | Redis (arq job queue) |
| `arkon_minio` | `minio` | MinIO object storage |
| `arkon_nginx` | `nginx` | Reverse proxy — the only published port |

---

## Access URLs

| Service | URL |
|---------|-----|
| App (via nginx) | `http://localhost:3119` |
| API | `http://arkon_api:5055` inside the Compose network — **not published to the host**; use `docker exec` or go through nginx |
| MinIO console | port 9001 inside the network, not published by default |

nginx binds to `127.0.0.1:${NGINX_PORT:-3119}:80`, so it is only reachable from the host machine.

---

## Common workflows

```bash
# After changing backend Python
docker compose --env-file .env.docker up -d --build api

# After changing frontend TypeScript/TSX
docker compose --env-file .env.docker up -d --build frontend

# After changing the worker (app/worker.py)
docker compose --env-file .env.docker up -d --build worker worker_skills

# After adding a DB migration — entrypoint runs upgrade when api starts;
# the command below is only needed for a manual run
docker compose --env-file .env.docker up -d --build api
docker exec arkon_api alembic upgrade head

# Full rebuild (after changing dependencies or a Dockerfile)
docker compose --env-file .env.docker up -d --build
```
