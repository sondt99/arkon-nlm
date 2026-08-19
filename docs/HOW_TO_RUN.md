# Local development

Run the API, workers, and Next.js on your machine. Use Docker only for Postgres, Redis, and MinIO — or run those yourself.

For the full production-like stack, use [SETUP.md](SETUP.md).

---

## Prerequisites

| Tool | Version |
|---|---|
| Python | 3.11 – 3.14 |
| Node.js | 20+ |
| Docker (optional) | for Postgres + Redis + MinIO |
| PostgreSQL | 16 with [pgvector](https://github.com/pgvector/pgvector) |
| Redis | 7 |
| MinIO | current |

---

## 1. Data services

```bash
docker run -d --name arkon-pg \
  -e POSTGRES_USER=arkon \
  -e POSTGRES_PASSWORD=arkon_secret \
  -e POSTGRES_DB=arkon \
  -p 5432:5432 \
  pgvector/pgvector:pg16

docker run -d --name arkon-redis \
  -p 6379:6379 \
  redis:7-alpine

docker run -d --name arkon-minio \
  -e MINIO_ROOT_USER=minioadmin \
  -e MINIO_ROOT_PASSWORD=minioadmin123 \
  -p 9000:9000 -p 9001:9001 \
  minio/minio server /data --console-address ":9001"
```

These factory MinIO credentials match `.env.local.example`. The API allows them only if `ARKON_ALLOW_DEFAULT_SECRET=1`.

---

## 2. Environment

```bash
cp .env.local.example .env.local
```

Minimum:

```env
ARKON_ALLOW_DEFAULT_SECRET=1
ARKON_ALLOW_CORS_WILDCARD=1
SECRET_KEY=dev-only-not-for-production
DEFAULT_ADMIN_EMAIL=admin@arkon.local
DEFAULT_ADMIN_PASSWORD=admin123
DATABASE_URL=postgresql+asyncpg://arkon:arkon_secret@localhost:5432/arkon
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin123
CORS_ORIGINS=http://localhost:3000
NEXT_PUBLIC_API_URL=http://localhost:5055
```

Do not use this file in production. The Docker template is `.env.docker.example`.

---

## 3. Backend

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
alembic upgrade head
```

The first API start also:

- creates the MinIO bucket
- seeds the default admin if none exists
- seeds built-in skills
- seeds security knowledge-type extraction hints

---

## 4. Frontend

```bash
cd frontend
npm install
```

`frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:5055
```

---

## 5. Start four processes

```bash
# Terminal 1 — API
uvicorn app.main:app --host 0.0.0.0 --port 5055 --reload

# Terminal 2 — wiki / documents / NotebookLM / re-embed
python -m arq app.worker.WorkerSettings

# Terminal 3 — skills
python -m arq app.worker.SkillWorkerSettings

# Terminal 4 — portal
cd frontend && npm run dev
```

| URL | What |
|---|---|
| http://localhost:3000 | Portal |
| http://localhost:5055 | API |
| http://localhost:5055/docs | Swagger |
| http://localhost:5055/health | Dependency health |
| http://localhost:5055/mcp | MCP |

---

## 6. Configure AI, then upload

Sign in at http://localhost:3000 with `DEFAULT_ADMIN_EMAIL` / `DEFAULT_ADMIN_PASSWORD`.

**Settings →** set embedding + LLM, click Test.

Without those, the worker will pick up jobs and fail during MAP/REFINE.

---

## 7. Tests and lint

```bash
# from repo root, venv active
ruff check .
pytest

cd frontend
npm run lint
./node_modules/.bin/tsc --noEmit
```

Playwright (optional):

```bash
cd frontend
npx playwright install
npm run test:e2e
```

---

## Worker jobs (reference)

**`WorkerSettings`** (Redis default queue):

| Job | Purpose |
|---|---|
| `ingest_file_task` | Extract text from an uploaded file |
| `ingest_url_task` | Fetch and extract a URL |
| `caption_images_task` | Vision captions for page images |
| `ingest_map_reduce_task` | MRP MAP + REDUCE + plan |
| `ingest_refine_task` | MRP REFINE + VERIFY + COMMIT |
| `reembed_all_pages_task` | Rebuild embeddings after a dimension change |
| `notebooklm_generate_task` | Generate a NotebookLM artifact |
| `notebooklm_ingest_artifact_task` | Import an artifact into the wiki |
| cron `notebooklm_refresh_session_cron` | Every 30 minutes |

**`SkillWorkerSettings`** (queue `skills_queue`):

| Job | Purpose |
|---|---|
| `ingest_skill_task` | Unpack and store a skill ZIP |
| `delete_skill_task` | Remove skill objects |
| cron `cleanup_temp_uploads_cron` | Hourly |

---

## Common local failures

| Symptom | Fix |
|---|---|
| `SECRET_KEY is still the default` | Set `ARKON_ALLOW_DEFAULT_SECRET=1` or change the secret |
| `CORS_ORIGINS is '*'` | Set `ARKON_ALLOW_CORS_WILDCARD=1` or list `http://localhost:3000` |
| `pgvector extension not found` | Use the `pgvector/pgvector` image |
| Documents stuck at `pending` | Start worker terminal 2 |
| Skills stuck at `pending` | Start worker terminal 3 |
| Frontend “API Error” | API down, or `NEXT_PUBLIC_API_URL` wrong |
| Login works but images 404 | MinIO not running, or `MINIO_ENDPOINT` not `localhost:9000` |

More cases: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
