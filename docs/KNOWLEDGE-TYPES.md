# Knowledge types

A knowledge type is a **category with instructions**. It tells the compiler what kind of document this is, and it is one of the levers MCP uses to hide content from a token.

**Knowledge Types** are edited from the Documents area. Creating one needs `doc:create`; editing needs `doc:edit`; deleting needs `doc:delete`. Any signed-in user can list them.

---

## Fields

| Field | Who reads it | What to put there |
|---|---|---|
| `name` | People | “SOP”, “HR Policy”, “Pentest” |
| `slug` | URLs, MCP, filters | `sop`, `hr-policy`, `pentest` |
| `description` | UI + a short LLM label | One or two sentences |
| `extraction_hints` | **The MRP prompts** | Keep/drop rules, extra entity types, slug conventions |
| `color` | UI badge | `#6366f1` |

`description` is a label. `extraction_hints` is an instruction manual. When they conflict, **hints win**.

---

## Why it matters

1. **Extraction quality** — an SOP wants steps; a policy wants conditions; a pentest report wants commands and CVEs, not a paraphrase.
2. **Page shape** — procedures become lists, comparisons become tables.
3. **Slug convention** — `topic/…` vs `entity/…` vs `concept/…-linux`.
4. **MCP scope** — a token can be limited to certain slugs. `search_wiki` then simply does not return the rest.

---

## When you need `extraction_hints`

Write them if the default compiler would “helpfully” generalize away the thing you care about:

- Security / pentest / red team
- Medical or legal wording that must stay exact
- Platform-specific flags, versions, payloads

Ordinary SOPs and product briefs usually need only a clear `description`.

### A useful hints template

```markdown
Documents in this type contain [domain].

**Keep (verbatim):**
- KEEP [thing] — do not generalize
- KEEP platform, version, and flags with the command

**Drop:**
- DROP [noise that never belongs on the wiki page]

**Extra entity types:**
- `technique` — …
- `cve` — …

**Slugs:**
- concept/<technique>-<platform> for each variant
```

Security types also trigger deterministic artifact recovery (commands, payloads, CVE / ATT&CK IDs). See [WIKI.md](WIKI.md).

---

## Defaults

On first boot Arkon seeds a small set (General, SOP, Product, Project, Customer, plus security types with hints). You can rename, reorder, or add your own. Do not delete a type that pages still reference without a plan for retagging.

---

## API

| Method | Path |
|---|---|
| `GET` | `/api/knowledge-types` |
| `POST` | `/api/knowledge-types` |
| `PUT` | `/api/knowledge-types/{id}` |
| `DELETE` | `/api/knowledge-types/{id}` |
| `PATCH` | `/api/knowledge-types/reorder` |
