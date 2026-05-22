# Arkon — Enterprise AI Knowledge Hub

**Self-hosted, on-premise AI knowledge base for organizations.**

> This project is developed and extended from [nduckmink/arkon](https://github.com/nduckmink/arkon).  
> NotebookLM integration uses [teng-lin/notebooklm-py](https://github.com/teng-lin/notebooklm-py).

---

## What is Arkon?

Arkon is the central layer between your organization's documents and your employees' AI clients. Upload SOPs, policies, product specs, and internal documents — Arkon compiles them into a structured knowledge wiki and serves it to Claude via MCP. Every employee gets the right context automatically, without copy-pasting.

**Stack:** FastAPI · PostgreSQL + pgvector · Redis (arq) · MinIO · Next.js · Tailwind CSS

---

## Features

### Knowledge Wiki
Documents are compiled into a persistent, interlinked wiki using the MRP pipeline (Map → Reduce → Plan → Refine → Verify). Each page covers a specific entity, concept, or topic. Pages cross-reference each other.

- Three-panel wiki browser: page tree, content, backlinks & outlinks
- Full-text and semantic search
- Knowledge graph visualization
- Organize by knowledge type (SOP, Product, HR Policy, etc.)
- Version history and rollback for every page
- Draft proposal → editor review → approval workflow

### NotebookLM Integration
Connect Arkon to Google NotebookLM to generate rich study materials from any knowledge source.

- Create and manage NotebookLM notebooks directly from Arkon
- Generate artifacts: audio podcast, video, quiz, flashcards, report, slide deck, infographic, data table
- Preview all artifact types inline — interactive quiz, flashcard flip, rendered report, media playback
- Chat with any notebook using NotebookLM's AI
- Import artifact content back into Arkon wiki with one click
- Server-side session with automatic 30-minute keepalive cron (no browser required after initial setup)
- Manual session refresh + last-refreshed timestamp in UI

### Workspaces
Cross-functional knowledge contexts for projects, clients, or initiatives.

- Create a workspace → add members → attach documents
- Scoped wiki and document management per workspace
- Role-based membership: Viewer, Contributor, Editor, Admin
- Contributors propose wiki edits; editors review and approve

### AI Skills
Upload custom agent packages and make them available to employees through Claude. Skills are versioned, department-scoped, and distributed via MCP.

### MCP Server
Employees connect Claude Desktop (or any MCP client) to Arkon using a personal token. Claude gets access to the compiled wiki, raw source documents, and AI skills — all filtered to the employee's permission scope.

### AI Provider Settings
Configurable embedding, LLM, and vision providers — all from the admin UI.

- Providers: Google, OpenAI, Anthropic, Ollama, 9Router
- Per-provider API key storage
- Custom embedding model support with dimension selector
- Fetch available models directly from Ollama/9Router endpoints
- Test connection buttons per provider

### Access Control
Fine-grained RBAC at department level plus workspace membership roles. Admins define roles with granular permissions.

---

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and Docker Compose
- An AI provider API key: Google, OpenAI, Anthropic, or Ollama (local)
- (Optional) Google account for NotebookLM integration

---

## Quick Start

```bash
git clone <your-repo-url>
cd arkon
cp .env.docker.example .env.docker
```

Edit `.env.docker` — set at minimum:

```env
# Generate with: python -c "import secrets; print(secrets.token_urlsafe(32))"
SECRET_KEY=your-random-secret-key

DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=your-secure-password
```

Build and start:

```bash
docker compose --env-file .env.docker up -d --build
```

Open **http://localhost:3119** and log in with your admin credentials.

---

## Configuration

### AI Providers (Settings → Embedding / LLM / Vision)

After first login, go to **Settings** and configure at least one provider for each capability:

| Capability | Required for |
|---|---|
| **Embedding** | Document indexing and semantic search |
| **LLM** | Wiki compilation (MRP pipeline), webhook gateway |
| **Vision** | Image analysis during document ingestion (optional) |

**Supported providers:**

| Provider | Embedding | LLM | Vision |
|---|---|---|---|
| Google Gemini | ✓ | ✓ | ✓ |
| OpenAI | ✓ | ✓ | ✓ |
| Anthropic | — | ✓ | ✓ |
| Ollama (local) | ✓ | ✓ | ✓ |
| 9Router (proxy) | ✓ | ✓ | ✓ |

For **Ollama**: enter your Ollama base URL (e.g. `http://host.docker.internal:11434/v1`), then click **Fetch Models** to load available models.

For **9Router**: enter your 9Router endpoint URL, click **Fetch Models**, then select from the returned list.

### Embedding Dimension Warning

Changing embedding providers after documents have been indexed requires re-embedding all wiki pages. Arkon will prompt you through this process in the admin UI. The embedding dimension must match the selected model (768d / 1536d / 3072d).

---

## NotebookLM Setup

NotebookLM integration uses [notebooklm-py](https://github.com/teng-lin/notebooklm-py) to communicate with Google NotebookLM on your behalf. Authentication requires importing your Google session cookies (one-time setup).

### Connect your Google account

1. Open **NotebookLM** in Arkon sidebar
2. Click **Connect** in the session banner
3. Follow the dialog:
   - Install the [Cookie-Editor](https://cookie-editor.com/) browser extension
   - Visit [notebooklm.google.com](https://notebooklm.google.com) (make sure you are logged in)
   - Open Cookie-Editor → **Export** → **Export as JSON**
   - Paste the JSON into the Arkon dialog and click **Import**

### Session management

After connecting, the session is maintained server-side:
- The arq worker refreshes session cookies automatically every **30 minutes**
- You can manually refresh at any time using the **Refresh** button in the connected banner
- Session files are stored at `NOTEBOOKLM_STORAGE_PATH` (default: `/data/notebooklm-session` in Docker)
- Mount this path as a Docker volume (already configured in `docker-compose.yml`) so sessions survive container restarts

### Using NotebookLM

1. **Create a notebook** — click the `+` button in the notebook list panel
2. **Add sources** — URL, plain text, or file upload
3. **Generate artifacts** — select an artifact type and click Generate:
   - **Audio** — podcast-style audio overview
   - **Video** — video summary
   **Quiz** — interactive multiple-choice quiz
   - **Flashcards** — flip-card study set
   - **Report** — briefing doc, study guide, or blog post
   - **Slide Deck** — PDF presentation
   - **Infographic** — visual summary image
   - **Data Table** — CSV structured data
4. **Preview** — click any completed artifact to open the preview modal
5. **Chat** — ask questions about the notebook in the Chat tab
6. **Add to Wiki** — on completed text artifacts, click **Add to Wiki** to import into Arkon

---

## Connecting Claude (MCP)

Once an employee account is created and an MCP token is generated (Profile → MCP Tokens):

```json
{
  "mcpServers": {
    "arkon": {
      "url": "http://your-arkon-server/mcp",
      "headers": {
        "Authorization": "Bearer ark_xxxxxxxxxxxx"
      }
    }
  }
}
```

Add this to `claude_desktop_config.json` and restart Claude Desktop.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                     Browser / Claude                     │
└──────────────┬──────────────────────────┬───────────────┘
               │ HTTP                     │ MCP
               ▼                          ▼
┌──────────────────────┐    ┌─────────────────────────────┐
│   Next.js Frontend   │    │      FastAPI (API + MCP)     │
│   (port 3119)        │    │      (port 5055)             │
└──────────────────────┘    └──────┬──────────────┬────────┘
                                   │              │
                    ┌──────────────┘              │
                    ▼                             ▼
          ┌─────────────────┐         ┌────────────────────┐
          │   arq Worker    │         │     PostgreSQL      │
          │ (ingestion,     │         │   + pgvector        │
          │  NLM refresh,   │         └────────────────────┘
          │  cron tasks)    │
          └────────┬────────┘         ┌────────────────────┐
                   │                  │       MinIO         │
                   │ Redis            │  (file storage)     │
                   ▼                  └────────────────────┘
          ┌─────────────────┐
          │      Redis      │
          └─────────────────┘
```

**Outbound network:** configured AI provider only. No telemetry, no external calls except NotebookLM (if configured).

---

## Development

### Run locally (without Docker)

**Backend:**

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install dependencies
pip install -e .

# Set environment variables
cp .env.docker.example .env.local
# Edit .env.local with local DB/Redis URLs

# Run database migrations
alembic upgrade head

# Start API server
uvicorn app.main:app --reload --port 5055

# Start worker (separate terminal)
arq app.worker.WorkerSettings
```

**Frontend:**

```bash
cd frontend
npm install
npm run dev   # starts on port 3000
```

### Environment variables

| Variable | Description | Default |
|---|---|---|
| `DATABASE_URL` | PostgreSQL connection string | required |
| `SECRET_KEY` | JWT signing secret (generate randomly) | required |
| `REDIS_HOST` | Redis hostname | `redis` |
| `REDIS_PASSWORD` | Redis password | `""` |
| `MINIO_ENDPOINT` | MinIO endpoint | `minio:9000` |
| `MINIO_ACCESS_KEY` | MinIO access key | `minioadmin` |
| `MINIO_SECRET_KEY` | MinIO secret key | required |
| `NOTEBOOKLM_STORAGE_PATH` | Path for NLM session cookies | `/data/notebooklm-session` |
| `CORS_ORIGINS` | Allowed CORS origins | `*` |
| `WORKER_MAX_JOBS` | Max concurrent worker jobs | `3` |
| `WORKER_JOB_TIMEOUT` | Worker job timeout (seconds) | `600` |

---

## Docker volumes

The `docker-compose.yml` defines persistent volumes:

| Volume | Purpose |
|---|---|
| `pg_data` | PostgreSQL data |
| `minio_data` | Uploaded files |
| `./data` | NotebookLM session cookies |

**Important:** Do not delete `./data` — it contains your NotebookLM session. If deleted, you must re-import cookies.

---

## Roadmap

- [x] MRP Pipeline — Map → Reduce → Plan → Refine → Verify wiki compilation
- [x] Wiki browser — three-panel layout with graph visualization
- [x] MCP Server with scoped knowledge access
- [x] Ingestion pipeline — PDF, DOCX, DOC, URLs, images with vision captions
- [x] Workspaces — scoped wiki, documents, and members
- [x] Wiki draft & revision system — propose, review, approve, rollback
- [x] AI Skills — versioned, department-scoped agent packages
- [x] Full RBAC — department permissions + workspace membership roles
- [x] Audit log
- [x] NotebookLM integration — notebook management, artifact generation, wiki import, session keepalive
- [x] Multi-provider settings UI — Embedding, LLM, Vision with per-provider keys
- [ ] Arkon CLI — one-command employee setup
- [ ] Notification system for draft review requests
- [ ] Usage analytics dashboard
- [ ] Multi-account NotebookLM support (per-user sessions)

---

## Attribution

- Core platform: [nduckmink/arkon](https://github.com/nduckmink/arkon) — PolyForm Noncommercial License 1.0.0
- NotebookLM client: [teng-lin/notebooklm-py](https://github.com/teng-lin/notebooklm-py)

---

## License

This project is built on top of Arkon which is licensed under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).

Free for internal tooling, research, personal projects, and non-profit use.
