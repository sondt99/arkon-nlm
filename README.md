![Arkon Banner](docs/assets/banner.png)

# Arkon - Enterprise AI Knowledge Hub

**Connect your organization's knowledge to any Ai Client. Self-hosted, on-premise.**

Arkon is the central layer between your documents and your employees' AI clients. Upload your SOPs, policies, product specs, and internal docs - Arkon compiles them into a structured knowledge wiki and serves it directly to Claude via MCP. Every employee gets the right context, automatically, without copy-pasting.

[Setup Guide](docs/SETUP.md) · [Architecture](docs/ARCHITECTURE.md) · [Wiki System](docs/WIKI.md) · [MCP & Claude](docs/MCP.md) · [AI Skills](docs/SKILLS.md) · [Access Control](docs/ACCESS-CONTROL.md)

---

## The problem

Most organizations adopt AI team-by-team, with no shared knowledge, inconsistent context, and no visibility into what information Ai Client is actually working with. Every employee manually pastes documents, repeats the same background, and gets different answers depending on what they remembered to include.

![Problem](docs/assets/Problem.png)

Arkon treats your AI client as a managed organizational resource - not a personal chatbot.

---

## How it works

![How it works](docs/assets/HowItWorks.png)

Knowledge compounds. Every document you add enriches the existing wiki rather than creating isolated fragments. By the time an employee asks Claude a question, the answer has already been synthesized from dozens of sources.

---

## Features

### Knowledge Wiki
Documents are compiled into a persistent, interlinked wiki — not just indexed. The MRP pipeline (MAP → REDUCE → PLAN → REFINE → VERIFY) reads every section of every document, traces every claim back to its source, and lets an editor review the compilation plan before any pages are written. Each page covers a specific entity, concept, or topic. Pages cross-reference each other. The wiki grows smarter as more documents are added.

- Three-panel wiki browser: page tree, content, backlinks & outlinks
- Full-text and semantic search
- Knowledge graph visualization
- Organize by knowledge type (SOP, Product, HR Policy, etc.)
- Version history and rollback for every page
- Draft proposal → editor review → approval workflow

### Workspaces
Cross-functional knowledge contexts for projects, clients, or initiatives.

Create a workspace → add members from any department → attach documents. Each workspace has its own scoped wiki, document list, and member roster. Members see their workspace knowledge automatically through Claude.

- Role-based membership: Viewer, Contributor, Editor, Admin
- Scoped wiki and document management
- Contributors propose wiki edits; editors review and approve

### NotebookLM Integration
Connect Arkon to Google NotebookLM to generate rich study guides, quizzes, flashcards, reports, audio overviews, and more from any knowledge source.

- Create and manage NotebookLM notebooks directly from Arkon
- Generate artifacts: audio podcast, video, quiz, flashcards, report, slide deck, infographic, data table
- Preview all artifact types inline — quiz interactive, flashcard flip, report rendered, audio/video playback
- Chat with any notebook using NotebookLM's AI
- Import artifact content back into the Arkon wiki with one click
- Server-side session management with automatic 30-minute keepalive (no browser required)

### AI Skills
Upload custom agent packages and make them available to employees through Claude. Skills are versioned, department-scoped, and distributed via MCP.

### MCP Server
Employees connect Claude Desktop (or any MCP client) to Arkon using a personal token. Claude gets access to the compiled wiki, raw source documents, and AI skills - all filtered to the employee's permission scope.

→ See [MCP & Claude](docs/MCP.md) for the full tool reference.

### Access Control
Fine-grained RBAC at department level plus workspace membership roles. Admins define roles with granular permissions; employees inherit access based on department or explicit assignment.

→ See [Access Control](docs/ACCESS-CONTROL.md) for the full permission model.

---

## Quick Start (Docker)

**Prerequisites:** Docker, Docker Compose, an AI provider API key (Google, OpenAI, or Anthropic).

```bash
git clone https://github.com/nduckmink/arkon.git
cd arkon
cp .env.docker.example .env.docker
```

Edit `.env.docker` - set at minimum:

```env
SECRET_KEY=<run: python -c "import secrets; print(secrets.token_urlsafe(32))">
DEFAULT_ADMIN_EMAIL=admin@yourcompany.com
DEFAULT_ADMIN_PASSWORD=your-secure-password
```

```bash
docker compose --env-file .env.docker up -d --build
```

Open **http://localhost:3119** and log in with your admin credentials.

Go to **Settings** → configure your embedding model, LLM, and (optionally) vision model. Then upload your first document from **Knowledge Base**.

→ See [Setup Guide](docs/SETUP.md) for the full walkthrough including development mode.

---

## Connecting Claude

Once an employee account is created and an MCP token is generated:

```json
{
  "mcpServers": {
    "arkon": {
      "url": "https://your-arkon-server/mcp",
      "headers": {
        "Authorization": "Bearer ark_xxxxxxxxxxxx"
      }
    }
  }
}
```

Add this to `claude_desktop_config.json` and restart Claude Desktop. The employee's compiled knowledge is immediately available.

→ See [MCP & Claude](docs/MCP.md) for the complete setup and tool reference.

---

## Architecture

![Arkon System Design](docs/assets/Architecture.png)

**Stack:** FastAPI · PostgreSQL + pgvector · Redis (arq) · MinIO · Next.js · Tailwind CSS

**AI providers (your choice):** Google · OpenAI · Anthropic · Ollama · Voyage · Cohere

**Outbound network:** configured AI provider only. No telemetry, no external calls.

→ See [Architecture](docs/ARCHITECTURE.md) for the full technical breakdown.

---

## Roadmap

- [x] MRP Pipeline - deterministic MAP→REDUCE→PLAN→REFINE→VERIFY compilation with full document coverage, traceable citations, and human plan review
- [x] Wiki browser - three-panel layout with graph visualization
- [x] MCP Server with scoped knowledge access
- [x] Ingestion pipeline - PDF, DOCX, DOC, URLs, images with vision captions
- [x] Workspaces - scoped wiki, documents, and members
- [x] Wiki draft & revision system - propose, review, approve, rollback
- [x] AI Skills - versioned, department-scoped agent packages
- [x] Full RBAC - department permissions + workspace membership roles
- [x] Audit log
- [x] NotebookLM integration - notebook management, artifact generation, wiki import
- [ ] Arkon CLI - one-command employee setup
- [ ] Notification system for draft review requests
- [ ] Usage analytics dashboard

---

## License

Arkon is licensed under the [PolyForm Noncommercial License 1.0.0](https://polyformproject.org/licenses/noncommercial/1.0.0).

Free to use for internal tooling, research, personal projects, and non-profit use.

**Need a commercial license or custom integration?** We help organizations integrate Claude, custom AI agents, and MCP servers into their existing infrastructure - from connecting to internal databases and legacy systems to building purpose-built agents for specific business processes.

[Get in touch →](https://bitsness.vn)

---

[![Star History Chart](https://api.star-history.com/svg?repos=nduckmink/arkon&type=Date)](https://star-history.com/#nduckmink/arkon&Date)
