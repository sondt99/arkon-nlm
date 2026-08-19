# Access control

Arkon has **two independent realms**. A global permission does not open a workspace. A workspace role does not open global documents.

---

## Picture

```text
Employee
   │
   ├── system role = admin?  ──yes──►  bypass everything
   │
   ├── Global realm
   │     custom Role  →  permissions like wiki:read:own_dept
   │     applies to unscoped (org-wide) resources
   │
   └── Workspace realm
         membership row  →  viewer | contributor | editor | admin
         applies only inside that workspace
```

---

## Global realm

### Permission strings

```text
{resource}:{action}:{scope}
```

`scope` is `own_dept` (your department + unscoped/global items) or `all` (every department).

Org permissions look like `org:departments:read` (the third part is the action, not a scope).

### Documents

| Permission | Meaning |
|---|---|
| `doc:read:own_dept` | See your department’s files + global files |
| `doc:read:all` | See every file |
| `doc:create:own_dept` / `:all` | Upload |
| `doc:edit:own_dept` / `:all` | Edit metadata |
| `doc:delete:own_dept` / `:all` | Delete |

A file with **no** department **and** `scope_type != project` is global: anyone with the matching action can see it. Workspace uploads (`scope_type=project`) have no department rows on purpose — they are **not** global. Only workspace members (or a system admin) can read them.

### Wiki

| Permission | Meaning |
|---|---|
| `wiki:read:own_dept` / `:all` | Read pages |
| `wiki:write:own_dept` | Propose drafts on global pages |
| `wiki:write:all` | Direct edit + approve/reject drafts |
| `wiki:delete:own_dept` / `:all` | **Not currently effective — admin only.** `app/routers/wiki.py` rejects any non-admin after the permission check, so granting this to a custom role does nothing. (`super_admin` appears in that check but exists nowhere else in the codebase.) Tracked in issue #86. |

### Skills

Same shape as documents: `skill:read|create|edit|delete` × `own_dept|all`.

### Organization

| Permission | Meaning |
|---|---|
| `org:departments:read` / `manage` | Departments |
| `org:employees:read` / `manage` | People (manage cannot assign `role=admin`, reset passwords, or toggle admin accounts — those require a system admin) |
| `org:roles:read` / `manage` | Roles (manage cannot grant a permission the caller does not hold) |
| `org:settings:read` / `manage` | AI keys and models |
| `org:audit:read` | Audit log |
| `skill:contribution:review` | Approve or reject skill contributions |
| `workspace:view:all` | Declared in the catalog. **v0.1.0 does not use it** — only system `admin` lists every workspace |

### Built-in role presets

These are **templates** the Roles UI can apply. They are not four extra rows seeded by the migration (the database seeds system roles named Admin and Employee). You can still create them by hand.

| Preset | Includes |
|---|---|
| **Viewer** | read own-dept docs / wiki / skills, `org:departments:read` |
| **Contributor** | Viewer + create docs / wiki drafts / skills in own dept |
| **Department Admin** | Contributor + edit/delete in own dept |
| **Knowledge Admin** | `:all` on docs, wiki, and skills |

If an employee has **no** custom role, they get:

```text
doc:read:own_dept
doc:create:own_dept
wiki:read:own_dept
wiki:write:own_dept
skill:read:own_dept
org:departments:read
```

> `org:departments:read` gates `GET /api/departments`, which returns every department with
> its employee count — so an employee with no custom role can enumerate the org chart. That
> is the current intended behaviour; it is listed here because it was previously omitted from
> this document and from the design document, making the default reach look narrower than it
> is.

### System admin

`Employee.role = admin` is not a custom Role. It skips every permission check, is an implicit admin of every workspace, and is the only actor that can create or delete workspaces.

---

## Workspace realm

Membership lives on `project_members`. Roles are a ladder — a higher role includes the lower ones.

| Role | Level | Can |
|---|---|---|
| Viewer | 0 | Read wiki, sources, members |
| Contributor | 1 | + propose drafts |
| Editor | 2 | + direct edit, review drafts, add/remove sources, upload |
| Admin | 3 | + manage members and archive the workspace |

The last workspace admin cannot be removed or demoted. Assign another admin first.

System admins do not need a membership row.

---

## MCP tokens

An `ark_…` token is bound to one employee. Every MCP tool re-resolves that employee and applies the same **document-read** scope they have in the portal:

- `doc:read:all` — every source
- `doc:read:own_dept` — their department’s sources plus unscoped/global sources
- workspace membership — project-scoped sources they belong to

Wiki reads are gated twice more. The token's employee must hold `wiki:read:own_dept` or
`wiki:read:all`, or the four wiki tools return `Access denied: your token's role does not include
wiki:read.` And the resolved identity carries a knowledge-type restriction derived from the
sources that employee can see: unrestricted for `wiki:read:all` and for admins, the KT slugs of
their visible sources otherwise, and an empty list — meaning **no** wiki access — when they hold no
`wiki:read` at all. `read_wiki_index` returns a catalog filtered the same way.

So an empty result can mean any of: department scope, workspace scope, or a knowledge type this
token cannot reach.

Employees can mint or revoke their own token under **Profile**. Admins can also mint/revoke from **Employees**.

---

## How to think about a decision

1. Is the resource in a **workspace**? Use membership. Stop.
2. Otherwise use the employee’s **permission list**.
3. If the resource has departments, `own_dept` must match one of them.
4. If the resource has no departments, treat it as global.

Code: `app/services/permission_engine.py`.
