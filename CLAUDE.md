# Common Commands

**Read `COMMANDS.md` before running any shell command.** It contains verified commands for this
project (git, docker, tsc, alembic, pytest) plus the non-obvious traps. Do not guess — check there
first.

Key rules from COMMANDS.md:
- Run commands from the **repo root**, using relative paths. Never hardcode an absolute checkout
  path — that is exactly what rotted the previous version of these docs.
- Docker: always pass `--env-file .env.docker`. Omitting it silently substitutes the placeholder
  passwords from `docker-compose.yml` and crash-loops the workers.
- Backend tests live in `tests/`, **not** `app/tests/`.
- Quote Next.js route-group paths — `(portal)` is a glob pattern in bash and zsh:
  `git add "frontend/src/app/(portal)/chat/page.tsx"`
- A fresh clone has no `.venv`, no `frontend/node_modules`, and no `.env.docker`. Bootstrap before
  assuming any local tool is runnable.

---

# Documentation Maintenance

**Rule:** Whenever code changes affect system architecture, APIs, data models, or business logic, update `docs/DESIGN_DOCUMENT.md` accordingly in the same task.

**What to update:**
- New or modified API endpoint → update section 4 (Đặc tả API)
- New or modified DB model/column → update section 3 (Mô hình dữ liệu)
- New feature or use case change → update section 5 (Đặc tả Use Case)
- Pipeline/background task change → update sections 6 and Phụ lục
- Permission/RBAC change → update section 7
- MCP tool change → update section 8

**Scope:** Only update the parts that actually changed. Do not rewrite unrelated sections.

---

# Arkon MCP — Knowledge Base Access

Arkon exposes a FastMCP server for Claude Desktop and Claude Code to query the enterprise knowledge base.

## Setup (Claude Desktop)

```json
{
  "mcpServers": {
    "arkon": {
      "url": "http://localhost:3119/mcp",
      "headers": { "Authorization": "Bearer <your-mcp-token>" }
    }
  }
}
```

Use `https://arkon.ladybug.net/mcp` instead of `localhost:3119` when connecting from another machine. Both go through nginx, which routes `/mcp` to the API (`nginx/nginx.conf`) — the API port itself is not published.

Get a token from an Arkon admin. Tokens are scoped to specific knowledge types — what you can read depends on your token.

## Skills

| Skill | Trigger | Role needed |
|-------|---------|------------|
| `/arkon-query` | "what do we know about X", "find in KB", "query:" | Any (scoped by token) |
| `/arkon-edit` | "update wiki", "propose edit", "fix this page" | Contributor+ |
| `/arkon-review` | "review drafts", "approve draft", "check queue" | Editor/Admin |

Skills live in `skills/`. Claude Code picks them up automatically when working in this repo.

## Key Principles

- **Wiki first, sources second.** `search_wiki` → `read_wiki_page` → source drill-down only for precise citations.
- **Your token is your scope.** RBAC is enforced server-side; "access denied" means contact an admin.
- **Always confirm before writing.** `propose_wiki_edit` and `edit_wiki_page` modify the live KB — get user approval first.
