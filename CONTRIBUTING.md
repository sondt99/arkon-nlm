# Contributing to Arkon

Thank you for helping improve Arkon. Bug reports, docs, and code are all useful.

Repository: [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

---

## Before you start

Read:

1. [README](README.md) — what the product is
2. [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md) — how to run it locally
3. [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the pieces fit
4. [docs/DESIGN_DOCUMENT.md](docs/DESIGN_DOCUMENT.md) — the formal spec

If your change touches APIs, data models, pipeline behavior, permissions, or MCP tools, update `docs/DESIGN_DOCUMENT.md` in the same pull request.

---

## Prerequisites

| Tool | Version | Why |
|---|---|---|
| Python | 3.11 – 3.14 | Backend |
| Node.js | 20+ | Frontend |
| Docker + Compose v2 | current | Postgres, Redis, MinIO — or the full stack |
| Git | current | Source control |

You do **not** need Neo4j.

---

## Setup

```bash
git clone https://github.com/sondt99/arkon-nlm.git
cd arkon-nlm
```

### Infrastructure

Easiest path — start only the data services, then run the app on the host:

```bash
docker network create arkon_default   # required once
# or start postgres / redis / minio individually; see docs/HOW_TO_RUN.md
```

For a full stack that matches production, use Docker:

```bash
cp .env.docker.example .env.docker
# edit secrets
docker compose --env-file .env.docker up -d --build
```

### Backend (host)

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.local.example .env.local   # edit DATABASE_URL, SECRET_KEY, MinIO, Redis
alembic upgrade head
```

Set `ARKON_ALLOW_DEFAULT_SECRET=1` in `.env.local` only if you keep the example secrets on a private machine.

### Frontend

```bash
cd frontend
npm install
```

For host-run backend, create `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:5055
```

---

## Running in development

You typically need **four** processes:

```bash
# 1 — API
uvicorn app.main:app --host 0.0.0.0 --port 5055 --reload

# 2 — document / wiki / NotebookLM worker
python -m arq app.worker.WorkerSettings

# 3 — skills worker
python -m arq app.worker.SkillWorkerSettings

# 4 — frontend
cd frontend && npm run dev
```

| URL | What |
|---|---|
| http://localhost:3000 | Portal |
| http://localhost:5055 | API |
| http://localhost:5055/docs | Swagger |
| http://localhost:5055/mcp | MCP (host-run) |
| http://localhost:3119 | Portal + API + MCP when using the Docker nginx stack |

Documents stay `pending` if worker 2 is not running. Skills stay `pending` if worker 3 is not running.

---

## Project layout

```text
arkon-nlm/
├── app/                    # FastAPI backend
│   ├── ai/                 # Providers + MRP pipeline
│   ├── database/           # SQLAlchemy models
│   ├── mcp/                # MCP tools and resources
│   ├── routers/            # HTTP endpoints
│   ├── services/           # Business logic
│   ├── main.py             # App entry
│   └── worker.py           # arq jobs
├── alembic/versions/       # Migrations (never rewrite a merged file)
├── frontend/src/           # Next.js App Router
├── nginx/                  # Reverse proxy
├── skills/                 # Claude Code skills that call Arkon MCP
├── tests/                  # pytest
└── docs/                   # Human docs (this is the source of truth)
```

Business logic belongs in `app/services/`, not in routers.

---

## Coding standards

### Python

- Ruff (`E`, `F`, `I`), line length 88
- Type hints on public functions
- All database and I/O is `async`
- Absolute imports: `from app.services.wiki_service import ...`
- One router file per domain

```bash
ruff check app/ tests/
ruff format app/ tests/
pytest
```

### TypeScript / React

- Next.js App Router, TypeScript, Tailwind
- Shared UI in `frontend/src/components/ui/`
- Feature UI in `frontend/src/components/<feature>/`
- HTTP goes through `frontend/src/lib/api.ts`
- Prefer local state; use context only when several trees share it

```bash
cd frontend
npm run lint
./node_modules/.bin/tsc --noEmit
```

### Migrations

```bash
alembic revision --autogenerate -m "short description"
```

Review the generated file. Never edit a migration that already landed on `main`.

---

## Commit messages

[Conventional Commits](https://www.conventionalcommits.org/):

```text
<type>(<scope>): short summary
```

| Type | Use for |
|---|---|
| `feat` | New behavior |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `refactor` | No behavior change |
| `test` | Tests |
| `chore` | Tooling, deps |

Common scopes: `api`, `db`, `worker`, `ai`, `mcp`, `ui`, `auth`, `wiki`, `notebooklm`, `docs`.

Examples:

```text
feat(wiki): paginate the landing page on the server
fix(notebooklm): live-verify imported cookies
docs: rewrite the quick start for nginx on :3119
```

---

## Pull requests

1. Branch from `main`: `feat/…` or `fix/…`.
2. Keep the change to one concern.
3. Update docs if the user-visible behavior, API, model, or permissions changed.
4. Include a screenshot for UI work.
5. Never commit `.env`, cookies, master tokens, or API keys.

Checklist:

- [ ] `ruff check` and `pytest` pass
- [ ] Frontend lint / `tsc` pass for UI changes
- [ ] New migration reviewed (if models changed)
- [ ] `docs/DESIGN_DOCUMENT.md` updated when APIs, models, pipeline, RBAC, or MCP change

---

## Reporting issues

Open an issue at [github.com/sondt99/arkon-nlm/issues](https://github.com/sondt99/arkon-nlm/issues) and include:

- OS, Docker / Python / Node versions
- Steps to reproduce
- Expected vs actual
- Relevant logs (`docker compose --env-file .env.docker logs --tail=80 api worker`)

---

## License

Contributions are licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).
