# Arkon

**Self-hosted knowledge hub for organizations.**

Upload documents. Arkon compiles them into a living wiki. Employees and AI clients (Claude Desktop, Claude Code) then query that wiki — already filtered to what each person is allowed to see.

Repository: [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

> Built on [nduckmink/arkon](https://github.com/nduckmink/arkon).  
> NotebookLM client: [teng-lin/notebooklm-py](https://github.com/teng-lin/notebooklm-py).

---

## How it works

```text
PDF / DOCX / URL / ZIP
          │
          ▼
   MRP pipeline
   Map → Reduce → Plan → Refine → Verify → Commit
          │
          ▼
   Interlinked wiki + search + graph
          │
     ┌────┼────────────┐
     ▼    ▼            ▼
   Portal  MCP      Chatbot
           (Claude)
```

You do not paste policies into a chat window. You upload the source once. Arkon writes structured wiki pages, keeps citations back to the original file, and serves those pages to people and agents through the same permission model.

---

## What you get

| Area | What it does |
|---|---|
| **Wiki** | Persistent, interlinked markdown pages with search, graph, history, and draft review |
| **Documents** | Ingest PDF, DOCX, DOC, images, URLs, and ZIP archives |
| **Workspaces** | Project-scoped wiki, files, and members (Viewer / Contributor / Editor / Admin) |
| **Access control** | Department RBAC plus workspace membership — two independent realms |
| **MCP** | 16 tools for Claude. Token-scoped. Wiki first, raw sources only for citations |
| **Chatbot** | RAG chat over the wiki; a conversation can be turned into a wiki page |
| **Skills** | Versioned agent packages with a contribute → review workflow |
| **NotebookLM** | Create notebooks, generate artifacts, import results back into the wiki |
| **Claude Code gateway** | Anthropic-compatible `/v1/messages` endpoint routed through your configured LLM |

**Stack:** FastAPI · PostgreSQL + pgvector · Redis (arq) · MinIO · Next.js · nginx

---

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) and Docker Compose v2.

```bash
git clone https://github.com/sondt99/arkon-nlm.git
cd arkon-nlm

# Compose joins this existing network. Create it once.
docker network create arkon_default

cp .env.docker.example .env.docker
```

Edit `.env.docker` and set at least:

```env
# python -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=replace-with-a-long-random-string

DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=a-strong-password

POSTGRES_PASSWORD=a-strong-db-password
REDIS_PASSWORD=a-strong-redis-password
MINIO_SECRET_KEY=a-strong-minio-password

# DATABASE_URL must use the same postgres user/password/db as above
DATABASE_URL=postgresql+asyncpg://arkon:a-strong-db-password@postgres:5432/arkon
```

Start everything:

```bash
docker compose --env-file .env.docker up -d --build
```

Open **http://localhost:3119** and sign in with the admin email and password.

Then go to **Settings** and configure at least:

1. An **embedding** provider (search)
2. An **LLM** provider (wiki compilation and chat)

Without those two, uploads queue but the wiki will not be written.

Full walkthrough: [docs/QUICKSTART.md](docs/QUICKSTART.md)

---

## After login

| I want to… | Where |
|---|---|
| Upload a document | **Documents** |
| Read compiled knowledge | **Wiki** |
| Ask questions | **Chatbot** |
| Connect Claude | **Profile → MCP token**, then see [docs/MCP.md](docs/MCP.md) |
| Add people and roles | **Employees** / **Roles** |
| Create a project space | **Workspaces** |

---

## Documentation

Start at the [docs index](docs/README.md).

| Guide | Use it when |
|---|---|
| [Quick start](docs/QUICKSTART.md) | First run on your machine |
| [Setup / deploy](docs/SETUP.md) | Putting Arkon on a server |
| [Local development](docs/HOW_TO_RUN.md) | Changing the code |
| [Architecture](docs/ARCHITECTURE.md) | How the pieces fit |
| [Admin guide](docs/ADMIN-GUIDE.md) | Day-to-day administration |
| [Access control](docs/ACCESS-CONTROL.md) | Roles and permissions |
| [Wiki & MRP](docs/WIKI.md) | How documents become pages |
| [MCP](docs/MCP.md) | Claude Desktop / Claude Code |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Something is broken |
| [Changelog](CHANGELOG.md) | What shipped in v0.2.0 |

---

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) and [docs/HOW_TO_RUN.md](docs/HOW_TO_RUN.md).

```bash
# Backend
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.local.example .env.local   # then edit
alembic upgrade head
uvicorn app.main:app --reload --port 5055
# `alembic` and `uvicorn` both import app.config, which refuses known-weak
# credentials at import time. .env.local.example ships with
# ARKON_ALLOW_DEFAULT_SECRET=1 and ARKON_ALLOW_CORS_WILDCARD=1 set for exactly
# that reason — keep them for local work, drop them anywhere else.

# Worker (second terminal)
python -m arq app.worker.WorkerSettings

# Frontend (third terminal)
cd frontend && npm install && npm run dev
```

---

## License

[PolyForm Noncommercial License 1.0.0](LICENSE).

Free for internal tooling, research, personal projects, and non-profit use. Commercial use needs a separate license from the original licensor.
