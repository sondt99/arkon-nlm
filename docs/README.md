# Arkon documentation

Version **0.1.0** · Source: [github.com/sondt99/arkon-nlm](https://github.com/sondt99/arkon-nlm)

These pages are the source of truth. If something here disagrees with a comment in code, trust the running code and file an issue.

---

## Start here

| I want to… | Read |
|---|---|
| Understand the product in 5 minutes | [../README.md](../README.md) |
| Run it on my laptop | [QUICKSTART.md](QUICKSTART.md) |
| Deploy it on a server | [SETUP.md](SETUP.md) |
| Hack on the code | [HOW_TO_RUN.md](HOW_TO_RUN.md) + [../CONTRIBUTING.md](../CONTRIBUTING.md) |
| Fix a broken install | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) |

---

## How the product works

| Topic | Read |
|---|---|
| Containers, ports, data flow | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Formal spec (models, APIs, use cases) | [DESIGN_DOCUMENT.md](DESIGN_DOCUMENT.md) |
| HTTP endpoints | [API-REFERENCE.md](API-REFERENCE.md) |
| Wiki compiler (MRP) | [WIKI.md](WIKI.md) |
| Roles and permissions | [ACCESS-CONTROL.md](ACCESS-CONTROL.md) |
| What shipped | [../CHANGELOG.md](../CHANGELOG.md) |

---

## Day-to-day use

| Topic | Read |
|---|---|
| Admin tasks | [ADMIN-GUIDE.md](ADMIN-GUIDE.md) |
| Workspaces | [WORKSPACES.md](WORKSPACES.md) |
| Knowledge types | [KNOWLEDGE-TYPES.md](KNOWLEDGE-TYPES.md) |
| AI Skills | [SKILLS.md](SKILLS.md) |
| Claude / MCP | [MCP.md](MCP.md) |
| NotebookLM | [NOTEBOOKLM-INTEGRATION.md](NOTEBOOKLM-INTEGRATION.md) |
| NotebookLM login | [notebooklm-auth.md](notebooklm-auth.md) |

---

## How documents become answers

```text
Upload a source
      │
      ▼
MRP writes wiki pages (with citations)
      │
      ├── Portal  → people read and edit
      ├── Chatbot → RAG over those pages
      └── MCP     → Claude searches the same pages, scoped to the token
```

---

## Release facts (v0.1.0)

| Item | Value |
|---|---|
| App version | `0.1.0` |
| Latest migration | `033_nlm_passthrough_ownership` (`alembic heads` → `033`) |
| Browser entry | `http://localhost:3119` |
| MCP (Docker) | `http://localhost:3119/mcp` |
| Health (Docker) | `http://localhost:3119/api/health` |
| Health (inside API container) | `GET /health` |

The API port `5055` is **not** published in the default Compose file. nginx is the only host port.

`Arkon-Documentation.docx` is a leftover export from `export_docs.py`. Treat the Markdown files as the source of truth.
