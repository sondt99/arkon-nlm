# Workspaces

A workspace (also called a **project** in the API) is a private room: its files and wiki pages are visible only to members.

System admins create and delete workspaces. Everyone else joins as a member.

---

## Global vs workspace

| | Global | Workspace |
|---|---|---|
| Who sees files / wiki | People with the matching `doc:` / `wiki:` permission | Members only |
| Access model | Department RBAC | Membership role |
| Typical content | Company SOPs, HR policy | One client, one engagement, one product squad |

The two realms do not leak. Being Knowledge Admin globally does **not** open a workspace you are not in. Being workspace Admin does **not** grant `org:settings:manage`.

System `admin` accounts can see every workspace without a membership row.

---

## Create

**Workspaces → New**

Give it a name and, optionally, a type / description. Only a system admin can do this (`POST /api/projects`).

---

## Members

**Workspaces → [name] → Members**

| Role | Can |
|---|---|
| **Viewer** | Read wiki, files, member list |
| **Contributor** | + open wiki drafts |
| **Editor** | + edit wiki, review drafts, upload / attach / detach sources |
| **Admin** | + add/remove members, change roles, rename, archive |

The last Admin cannot be removed or demoted.

---

## Files

Two ways in:

1. **Documents → Upload**, scope = **Project**, pick the workspace
2. Inside the workspace, **Sources → Upload** or **Attach** an existing global source

Attached sources stay in the workspace’s list; membership still gates who sees them.

---

## Wiki

Workspace pages are compiled the same way as global pages (see [WIKI.md](WIKI.md)). They live under the workspace scope (`scope_type=project`, `scope_id=<workspace>`).

The workspace screen embeds the wiki tree, a page reader, and a mini graph so you do not have to bounce to the global Wiki.

Draft rules follow the **workspace** role, not the global `wiki:write:*` permission.

---

## MCP

Tools automatically hide workspace pages from tokens whose employee is not a member. Admins see all.

---

## API (short)

| Method | Path |
|---|---|
| `GET/POST` | `/api/projects` |
| `PUT/DELETE` | `/api/projects/{id}` |
| `GET/POST` | `/api/projects/{id}/members` |
| `PATCH/DELETE` | `/api/projects/{id}/members/{employee_id}` |
| `GET/POST/DELETE` | `/api/projects/{id}/sources` |
| `POST` | `/api/projects/{id}/sources/upload` |
| `POST` | `/api/projects/{id}/sources/url` |
| `GET` | `/api/projects/{id}/wiki` |
| `GET` | `/api/projects/{id}/wiki/index` |
| `GET` | `/api/projects/{id}/wiki/graph` |
