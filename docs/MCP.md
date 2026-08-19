# MCP and Claude

Arkon speaks the [Model Context Protocol](https://modelcontextprotocol.io/). Claude Desktop, Claude Code, and any MCP client can search the wiki, read pages, open source excerpts, and (with the right role) propose or approve edits.

Every call is authenticated. The server applies the employee’s **document-read** scope and workspace membership. The client cannot “ask for more.”

---

## Connect

### 1. Mint a token

**Profile → MCP token**, or **Employees → [person] → Generate token**.

The value starts with `ark_` and is shown **once**.

### 2. Point the client at `/mcp`

Docker / nginx (recommended):

```json
{
  "mcpServers": {
    "arkon": {
      "url": "http://localhost:3119/mcp",
      "headers": {
        "Authorization": "Bearer ark_xxxxxxxx"
      }
    }
  }
}
```

API running on the host (dev):

```json
{
  "mcpServers": {
    "arkon": {
      "url": "http://localhost:5055/mcp",
      "headers": {
        "Authorization": "Bearer ark_xxxxxxxx"
      }
    }
  }
}
```

Claude Desktop file:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

On a server use `https://your-host/mcp`. nginx already routes `/mcp` to the API (streamable HTTP, buffering off).

Restart the client after saving.

### 3. Claude Code skills (optional)

This repo ships three skills that wrap the tools:

| Skill | When to use |
|---|---|
| `skills/arkon-query` | “What do we know about…”, `query:` |
| `skills/arkon-edit` | Propose or apply a wiki edit |
| `skills/arkon-review` | Review the draft queue |

Copy them into your Claude Code skills directory, or open this repo in Claude Code.

---

## How to use the tools

**Wiki first.** Pages are already synthesized across sources. Open a raw PDF only when you need the exact sentence.

```text
search_wiki("fire evacuation")
        │
        ▼
read_wiki_page("concept/fire-evacuation")
        │
        ▼  (only if you need the original wording)
get_source_outline(id) → get_source_pages(id, "12-14")
```

---

## Tool list (16)

### Read (any valid token)

| Tool | Does |
|---|---|
| `search_wiki(query, top_k=10)` | Semantic search. Default 10, max 50 |
| `read_wiki_index()` | Catalog page |
| `read_wiki_page(slug)` | Full markdown + backlinks |
| `list_wiki_pages(...)` | Filter by type / knowledge type |
| `list_sources(...)` | Source library |
| `get_source(source_id)` | Metadata + status |
| `get_source_outline(source_id)` | Heading tree |
| `get_source_pages(source_id, pages)` | Raw text, e.g. `"5-7"` |
| `list_knowledge_types()` | Categories |
| `get_knowledge_type_docs(slug)` | Sources in a category |

There is **no** `find_contacts` tool. People live as employees or as wiki entity pages.

### Contribute (`wiki:write:own_dept` or workspace Contributor)

| Tool | Does |
|---|---|
| `propose_wiki_edit(slug, content_md, note?)` | Opens a draft |

### Direct edit (`wiki:write:all` or workspace Editor+)

| Tool | Does |
|---|---|
| `edit_wiki_page(slug, content_md, change_note?)` | Writes immediately, new revision |

### Review (same as direct edit)

| Tool | Does |
|---|---|
| `list_pending_drafts(workspace_id?)` | Queue |
| `review_draft(draft_id)` | Proposed vs current |
| `approve_draft(draft_id, reviewer_note?, edited_content_md?)` | Publish |
| `reject_draft(draft_id, reviewer_note)` | Note is required |

---

## Resources

| URI | Content |
|---|---|
| `arkon://about` | How to use this server |
| `arkon://wiki-index` | Same catalog as `read_wiki_index` |

---

## Auth details

Header:

```http
Authorization: Bearer ark_xxxxxxxx
```

The token maps to a `ResolvedIdentity`:

- `is_admin`
- `allowed_source_ids` derived from `doc:read` (`all` / `own_dept` + global) and workspace memberships

- `wiki_readable` — whether the employee holds `wiki:read:own_dept` or `wiki:read:all`
- `allowed_knowledge_types` — the knowledge types this token may read wiki pages from

Knowledge-type filtering **is** active: the identity carries the KT slugs of the sources that
employee can see (unrestricted for `wiki:read:all` and for admins, empty — i.e. no wiki access —
when they hold no `wiki:read`), and wiki search, page reads, the index, chat RAG, and export chat
all apply it. The four wiki tools also refuse outright without `wiki:read`, returning
`Access denied: your token's role does not include wiki:read.`

Revoke from Profile or Employees. A revoked token fails on the next call.

The same `ark_` token authenticates the [Export API](API-REFERENCE.md) and the [Claude Code gateway](API-REFERENCE.md) (`Authorization` or `x-api-key`).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| No tools | Restart the client; confirm JSON is valid |
| Connection refused | From Docker, use **:3119/mcp**, not :5055 |
| 404 on `/mcp` | Recreate nginx (the `/mcp` location is required) |
| Invalid token | Mint a new one |
| Empty hits | The employee’s `doc:read` / workspace membership does not include those sources |
| HTTPS errors | Put a real certificate on the public proxy |

More: [TROUBLESHOOTING.md](TROUBLESHOOTING.md).
