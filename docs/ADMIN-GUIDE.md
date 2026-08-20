# Admin guide

For accounts with `role=admin`. That flag is a system override — it is not the same thing as “workspace Admin.”

Portal: **http://localhost:3119** after [QUICKSTART.md](QUICKSTART.md).

---

## What only a system admin can do

- Skip every permission check
- Create / delete workspaces
- Manage departments, employees, roles
- Change AI provider keys
- See the full audit log
- Connect or disconnect the NotebookLM Google session

Everyone else is limited by their custom Role plus workspace membership. See [ACCESS-CONTROL.md](ACCESS-CONTROL.md).

---

## First hour

1. Change the admin password (**Profile**) if you used a temporary one.
2. **Settings** — embedding + LLM, click Test on each.
3. **Departments** — IT, HR, …
4. **Knowledge Types** — at least one category you will upload into.
5. **Roles** — keep the presets or clone them.
6. **Employees** — create people, assign department + role, mint MCP tokens as needed.
7. Upload one document and confirm the wiki page appears.

---

## Departments

**Departments → New**. Name is enough.

A document or skill with no department is **global**. With departments, `*:own_dept` only matches employees in one of those departments.

Deleting a department unassigns it; it does not delete the people.

---

## Employees

**Employees → New**

| Field | Notes |
|---|---|
| Name, email, password | Email is the login |
| Department | Used by `own_dept` |
| Custom role | Empty = the default contributor-ish set |

Toggle **active** to lock someone out without deleting them. JWT and MCP tokens then fail.

**Generate token** creates an `ark_…` MCP / gateway / export credential. Shown once. Revoke from the same row or from the employee’s **Profile**.

---

## Roles

**Roles** lists every permission as a checkbox grouped by resource. Presets: Viewer, Contributor, Department Admin, Knowledge Admin.

Do not try to encode workspace access here. Workspaces have their own member roles.

---

## Settings (AI)

Five slots, each with provider + model + key (or base URL):

| Slot | Required | Used by |
|---|---|---|
| Embedding | Yes | Search, MRP dedup, chat RAG |
| LLM | Yes | MRP, plan, verify |
| Vision | No | PDF image captions |
| Chatbot | No | Falls back to LLM |
| Claude Code gateway | No | `/api/claude-gateway` |

Supported providers: Google, OpenAI, Anthropic (no embeddings), Ollama, 9Router, Omniroute.

Ollama / 9Router / Omniroute: set the base URL, **Fetch Models**, pick one, **Test**.

Omniroute can also be seeded from env (`OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL`, `OMNIROUTE_MODEL`) so the LLM slot works before the first Settings save. Reasoning models such as `glm/glm-5.3` need more than a handful of tokens — the built-in Test uses 256.

Keys are stored encrypted (`SECRET_KEY` → Fernet). Changing `SECRET_KEY` later makes old keys unreadable.

### Embedding dimension

Models live at 768 / 1024 / 1536 / 3072 dimensions. Switching to a different size starts a re-embed job. Wait for it before you trust search.

---

## Documents

**Documents** is the source library.

- Upload file, ZIP, or URL
- Pick knowledge type + scope (global / workspace)
- Watch status: `pending` → `processing` → `plan_review` / `ready` / `error`
- **Review Plan** when MRP wants approval
- **Retry** on error
- **Send to NotebookLM** if that integration is connected

Large PDFs take minutes. Worker logs: `docker compose --env-file .env.docker logs -f worker`.

---

## Wiki

**Wiki** — search, tree, graph, history, rollback.

Drafts submitted by contributors show a banner. **Approve** / **Reject** there or via MCP (`list_pending_drafts`).

Do not hand-edit `_index` / `_log` unless you know why — COMMIT regenerates them.

---

## Workspaces

Only you can create them. Then add members with a workspace role. Guide: [WORKSPACES.md](WORKSPACES.md).

---

## Skills

Upload a ZIP, wait for `worker_skills`, assign scope. Review contributions under **Skill contributions**. Guide: [SKILLS.md](SKILLS.md).

---

## Chatbot

**Chatbot** is RAG over the wiki the current user can see. Optional dedicated provider. **Add to Wiki** turns a thread into a page (still permission-checked).

---

## NotebookLM

Connect the Google session once ([notebooklm-auth.md](notebooklm-auth.md)). After that, anyone with access to the page can manage notebooks; you remain the person who can tear the session down.

---

## Audit log

**Audit Log** — who did what, with filters. Needs `org:audit:read`.

---

## Care and feeding

```bash
docker compose --env-file .env.docker ps
curl -s http://localhost:3119/api/health
docker compose --env-file .env.docker logs --tail=100 worker

# after git pull
docker compose --env-file .env.docker up -d --build
docker exec arkon_api alembic current
```

Backups = Postgres dump + MinIO volume (or `mc mirror`) + `notebooklm_data` if you use NotebookLM.

Never commit `.env.docker`. Rotate `SECRET_KEY` only if you are ready to re-enter every provider key.
