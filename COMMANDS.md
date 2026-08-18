# Arkon — Common Commands

Commands verified on Linux (bash/zsh). Docker 29.x, Compose v5, Python 3.12, Node 22.

**Key rule:** run everything from the **repo root**. Commands below use paths relative to it, so
they work on any machine and in any checkout. Do not hardcode an absolute path into this file —
an earlier version pinned `E:\AI-CLAUDE\arkon` and every command in it broke when the repo moved.

If a tool needs an explicit root (or you are unsure of the current directory):

```bash
cd "$(git rev-parse --show-toplevel)"
# or, without changing directory:
git -C "$(git rev-parse --show-toplevel)" status
```

---

## First run on a fresh checkout

A clean clone has no `.venv`, no `frontend/node_modules`, and no `.env.docker`. Nothing below
that touches those will work until you bootstrap. Docker-only is the shortest path:

```bash
docker network create arkon_default    # once per machine; Compose joins this existing network
cp .env.docker.example .env.docker     # then edit — see README "Quick start" for required keys
docker compose --env-file .env.docker up -d --build
```

Open <http://localhost:3119>.

For local (non-Docker) development, see the `README.md` "Development" section.

---

## Git

Parentheses in Next.js route-group paths are glob characters in bash and zsh — **quote the path**:

```bash
git add "frontend/src/app/(portal)/chat/page.tsx"
```

Unquoted, zsh fails with `no matches found` and bash silently passes the literal through.

Multi-line commit messages — use repeated `-m`, or a quoted heredoc so the shell does not
expand backticks or `$` in the body:

```bash
git commit -m "feat: short summary" -m "Longer description here."
```

---

## Docker

> **Always pass `--env-file .env.docker`.** The `redis` / `minio` / `postgres` services resolve
> `${REDIS_PASSWORD}` etc. via Compose variable substitution, which only reads a file literally
> named `.env` unless `--env-file` is given explicitly. `env_file: [.env.docker]` on the backend
> services does **not** feed this substitution — it only injects env vars into those containers'
> own processes. Without `--env-file`, recreating redis/minio silently falls back to the
> placeholder passwords in `docker-compose.yml`, breaking auth against the real credentials the
> API uses (seen 2026-07-08: caused `worker` / `worker_skills` to crash-loop).

```bash
# Rebuild and restart one service (most common)
docker compose --env-file .env.docker up -d --build frontend
docker compose --env-file .env.docker up -d --build api
docker compose --env-file .env.docker up -d --build worker

# Rebuild everything
docker compose --env-file .env.docker up -d --build

# Status / logs / stop
docker compose ps
docker compose logs -f api
docker compose down
```

### Container names (for `docker exec`)

| Container | Role |
|-----------|------|
| `arkon_api` | FastAPI backend :5055 (not published) |
| `arkon_frontend` | Next.js :3000 (not published; reached via nginx) |
| `arkon_worker` | arq document ingestion worker |
| `arkon_worker_skills` | arq skills worker |
| `arkon_postgres` | PostgreSQL + pgvector |
| `arkon_redis` | Redis (arq queue) |
| `arkon_minio` | MinIO object storage |
| `arkon_nginx` | Reverse proxy — the **only** published port, `127.0.0.1:${NGINX_PORT:-3119}:80` |

```bash
# Alembic migrations
docker exec arkon_api alembic upgrade head
docker exec arkon_api alembic current
docker exec arkon_api alembic history

# Shells
docker exec -it arkon_api bash
docker exec -it arkon_postgres psql -U arkon -d arkon
```

> `arkon_api` runs with `read_only: true`. A command that needs to write inside the container
> will fail unless it writes to one of the configured `tmpfs` mounts.

---

## TypeScript type-check

Requires `frontend/node_modules` (run `npm install` in `frontend/` first — the project uses
**npm**; `package-lock.json` is the committed lockfile).

```bash
cd frontend && npm install          # once
cd frontend && ./node_modules/.bin/tsc --noEmit
```

No output = no errors.

---

## Next.js build

```bash
cd frontend && npm run build
```

Or verify through Docker, which runs the same build and refuses to start on failure:

```bash
docker compose --env-file .env.docker up -d --build frontend
```

---

## Backend: tests and lint

> Tests live in **`tests/`** at the repo root, **not** `app/tests/`. `pytest app/tests/` fails
> with "file or directory not found".

With a local virtualenv (see `README.md` "Development"):

```bash
.venv/bin/python -m pytest tests/ -q
.venv/bin/python -m ruff check app/ tests/
```

Without one, `uv` runs both without a persistent venv:

```bash
uv run pytest tests/ -q
uv run ruff check app/ tests/
```

Import smoke-test inside the running container:

```bash
docker exec arkon_api python -c "from app.routers.chat import router; print('OK')"
```

---

## Frontend: lint and e2e

```bash
cd frontend && npm run lint
cd frontend && npm run test:e2e     # Playwright; needs the stack running
```

---

## Environment

- App URL: `http://localhost:3119` (via nginx)
- API: `http://arkon_api:5055` inside the Compose network — **not published to the host**; use
  `docker exec` or go through nginx
- MinIO console: port 9001 inside the network, not published by default
- Config: `.env.docker` (gitignored — never commit)
- Templates: `.env.docker.example` (Docker), `.env.local.example` (local dev) — placeholders only
