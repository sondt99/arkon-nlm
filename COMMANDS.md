# Arkon — Common Commands

Commands verified to work on this project (Windows 11, PowerShell + Bash tool).

**Key rule:** Git and Docker commands use the `-C` / `-f` flag with the full path because
`cd` does not persist between PowerShell tool calls.

---

## Git (PowerShell tool)

```powershell
# Status / diff
git -C "E:\AI-CLAUDE\arkon" status
git -C "E:\AI-CLAUDE\arkon" diff --stat
git -C "E:\AI-CLAUDE\arkon" log --oneline -10

# Stage
git -C "E:\AI-CLAUDE\arkon" add docs/DESIGN_DOCUMENT.md
git -C "E:\AI-CLAUDE\arkon" add -A

# Commit (use here-string to handle multi-line messages safely)
git -C "E:\AI-CLAUDE\arkon" commit -m @'
feat: short summary

Longer description here.

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
'@

# Push / pull
git -C "E:\AI-CLAUDE\arkon" push
git -C "E:\AI-CLAUDE\arkon" pull
```

> **Note:** Files under `(portal)` in Next.js have parentheses in their path.
> Escape them in PowerShell with backticks:
> `git -C "E:\AI-CLAUDE\arkon" add frontend/src/app/`(portal`)/chat/page.tsx`

---

## Docker (PowerShell tool)

> **Always pass `--env-file .env.docker`.** The `redis`/`minio`/`postgres` services resolve
> `${REDIS_PASSWORD}` etc. via Compose variable substitution, which only reads a file named
> `.env` (not `.env.docker`) unless `--env-file` is given explicitly. `env_file: [.env.docker]`
> on the backend services does NOT feed this substitution — it only injects env vars into
> those containers' own processes. Without `--env-file`, recreating redis/minio silently
> falls back to the placeholder passwords in docker-compose.yml, breaking auth against the
> real credentials the API uses (seen 2026-07-08: caused worker/worker_skills to crash-loop).

```powershell
# Rebuild and restart a specific service (most common)
docker compose --env-file "E:\AI-CLAUDE\arkon\.env.docker" -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build frontend
docker compose --env-file "E:\AI-CLAUDE\arkon\.env.docker" -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build api
docker compose --env-file "E:\AI-CLAUDE\arkon\.env.docker" -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build worker

# Rebuild all services
docker compose --env-file "E:\AI-CLAUDE\arkon\.env.docker" -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build

# Check container status
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" ps

# Tail logs
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f api
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f frontend
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" logs -f worker

# Stop everything
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" down
```

### Container names (for `docker exec`)

| Container | Role |
|-----------|------|
| `arkon_api` | FastAPI backend :5055 |
| `arkon_frontend` | Next.js :3119 (via nginx) |
| `arkon_worker` | arq document ingestion worker |
| `arkon_worker_skills` | arq skills worker |
| `arkon_postgres` | PostgreSQL + pgvector |
| `arkon_redis` | Redis (arq queue) |
| `arkon_minio` | MinIO object storage |
| `arkon_nginx` | Reverse proxy, public port ${NGINX_PORT:-3119} |

```powershell
# Run a one-off Python snippet inside the API container
docker exec arkon_api python -c "import asyncio; ..."

# Alembic migrations
docker exec arkon_api alembic upgrade head
docker exec arkon_api alembic current
docker exec arkon_api alembic history

# Open a shell inside a container
docker exec -it arkon_api bash
docker exec -it arkon_postgres psql -U arkon -d arkon
```

---

## TypeScript type-check (Bash tool)

> Use the **Bash tool**, not PowerShell — the local `tsc` binary only works from bash.

```bash
cd E:/AI-CLAUDE/arkon/frontend && ./node_modules/.bin/tsc --noEmit 2>&1 | grep error
```

No output = no errors.

---

## Next.js build (inside Docker — preferred)

The easiest way to verify a frontend build is to rebuild the Docker image:

```powershell
docker compose -f "E:\AI-CLAUDE\arkon\docker-compose.yml" up -d --build frontend
```

The build output shows TypeScript check + route list. If it fails, the container won't start.

---

## Backend: run tests / check imports (Bash tool)

```bash
docker exec arkon_api python -c "from app.routers.chat import router; print('OK')"
docker exec arkon_api python -m pytest app/tests/ -x -q
```

---

## MinIO / storage

```powershell
# MinIO console is at http://localhost:9001 (admin UI) — not exposed by default
# To temporarily expose, edit docker-compose.yml ports for minio service
```

---

## Environment

- App URL: `http://localhost:3119` (via nginx)
- API direct: `http://localhost:5055` (not exposed by default — use docker exec)
- Config file: `.env.docker` (gitignored — never commit)
- Example config: `.env.docker.example` (placeholder values only)
