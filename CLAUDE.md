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
- New or modified API endpoint → update section 4 (API specification)
- New or modified DB model/column → update section 3 (Data model)
- New feature or use case change → update section 5 (Use cases)
- Pipeline/background task change → update section 6 and the Appendix
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

Get a token from an Arkon admin.

> **Token scoping is implemented. Two earlier warnings here were out of date; a narrower one
> replaces them.**
>
> What this block used to say — that `ResolvedIdentity.allowed_knowledge_types` "is read in eight
> places and never assigned", that a token "grants read access to the whole wiki", and that the wiki
> tools "skip the `wiki:read` permission entirely" — is no longer true of the code:
>
> - **#11 closed.** `allowed_knowledge_types` is assigned on all four `_resolve_scope` return paths,
>   and `[]` is honoured fail-closed (an empty list matches nothing, and is distinct from `None`).
> - **#35 closed for the tools.** All four wiki tools call `_require_wiki_read`. The `arkon://wiki-index`
>   *resource* still does not, and is safe today only because `_resolve_scope` happens never to
>   produce that combination — worth closing deliberately rather than relying on.
> - **#43 closed, with a caveat.** All four KB-mutating tools write an audit entry. MCP records the
>   page *slug* where REST records the page *UUID*, so an audit query keyed on a page id still misses
>   MCP writes.
>
> ⚠️ **What is worth knowing instead:** MCP and REST used to enforce different things, so neither
> surface was a superset of the other. An MCP token's document scope could be *wider* than the
> portal's — a knowledge-type grant derived from one department-visible document matched every
> document of that type — while the REST wiki API applied no document-visibility filter at all.
> Both now resolve visibility through the same helpers (`apply_scope_filter` for documents,
> `wiki_service.wiki_visibility_for` for the wiki), and the call sites are pinned by AST tests in
> `tests/test_mcp_guards_are_called.py` — the guards were correct before too; nothing asserted they
> were called. If you add a tool or a page-reading endpoint, those tests are what will tell you it
> is unscoped.

## Skills

| Skill | Trigger | Role needed |
|-------|---------|------------|
| `/arkon-query` | "what do we know about X", "find in KB", "query:" | Any (scoped by token) |
| `/arkon-edit` | "update wiki", "propose edit", "fix this page" | Contributor+ |
| `/arkon-review` | "review drafts", "approve draft", "check queue" | Editor/Admin |

Skills live in `skills/`. Claude Code picks them up automatically when working in this repo.

## Key Principles

- **Wiki first, sources second.** `search_wiki` → `read_wiki_page` → source drill-down only for precise citations.
- **RBAC is enforced server-side, but the two surfaces differ.** "Access denied" means contact an
  admin. The converse does not hold: access being *granted* through MCP does not mean the portal
  would have granted it, and vice versa — see the warning above.
- **Always confirm before writing.** `propose_wiki_edit` and `edit_wiki_page` modify the live KB — get user approval first.
- **MCP writes are audited, but keyed differently.** The four KB-mutating tools do write audit-log
  entries (#43 is closed). They record the page slug where the REST path records the page UUID, so
  an audit query keyed on a page id will not find them.
