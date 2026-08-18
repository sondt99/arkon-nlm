# AI Skills

A **skill** is a versioned ZIP package (prompt, scripts, assets) that your organization stores in Arkon. Think of it as an internal catalog of agent capabilities — the same idea as a Claude skill folder, managed centrally.

Skills are **not** MCP tools. MCP searches the wiki. Skills are files people (and Claude Code) can download and load. This repo’s own `skills/arkon-query` (and friends) are examples of the format.

---

## Life of a skill

```text
Upload ZIP  →  worker unpacks to MinIO  →  status = active
                                              │
                    new version ──────────────┤
                    contribute branch ────────┤
                    approve → becomes latest ─┘
```

**AI Skills → Upload**. Required: name, slug, ZIP. Optional: departments, workspace scope, changelog.

Statuses: `pending` → `processing` → `active` (or `error`). If it stays `pending`, start `worker_skills`.

System skills (`is_system=true`) are seeded on boot and are read-only.

---

## Versioning

Each upload creates a `SkillVersion` (number + content hash + object prefix). **Set latest** points the live slug at a previous version. Old objects stay in MinIO until you delete the skill.

---

## Who can see a skill

Same dual-realm rules as documents. See [ACCESS-CONTROL.md](ACCESS-CONTROL.md).

- No department + global scope → anyone with `skill:read:*`
- Departments set → those departments
- Workspace scope → workspace members with `skill:read`

`skill:create` / `edit` / `delete` follow the same `own_dept` / `all` split.

---

## Contributions

People who cannot edit a skill directly open a **contribution** (a sandbox copy):

1. **Contribute** from the skill card
2. Edit files in the contribution browser
3. **Submit**
4. A reviewer **Approves** (publishes a new version) or **Rejects**

API lives under `/api/skill-contributions`. Admins also have `/api/admin/skill-contributions`.

---

## Built-in skills in this repo

| Folder | Purpose |
|---|---|
| `skills/arkon-query` | Query the wiki through MCP |
| `skills/arkon-edit` | Propose or apply wiki edits |
| `skills/arkon-review` | Review drafts |

These talk to the MCP server. They are not stored as rows unless you upload them through the portal.

---

## API (short)

| Method | Path |
|---|---|
| `GET` | `/api/skills` |
| `POST` | `/api/skills/upload` |
| `POST` | `/api/skills/inspect-zip` |
| `GET` | `/api/skills/{slug}` |
| `GET` | `/api/skills/{slug}/versions` |
| `POST` | `/api/skills/{slug}/set-latest` |
| `POST` | `/api/skills/{slug}/reupload` |
| `PATCH` / `DELETE` | `/api/skills/{slug}` |
| `GET` | `/api/skills/{id}/files` and `.../files/content` |
