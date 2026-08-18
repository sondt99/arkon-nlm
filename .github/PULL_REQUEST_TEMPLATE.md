<!--
Keep the title in conventional-commit form, matching this repo's history:
  feat(notebooklm): master-token headless auth
  fix(worker): commit progress before yielding
  docs: rewrite guides and ship the v0.1.0 changelog
-->

## What and why

<!-- The change, and the problem it solves. Link the issue: "Closes #123". -->

## How it was verified

<!--
Not "it should work" — what you actually ran and observed. Paste the commands.
A PR whose only verification is "typechecks" has not been verified.
-->

- [ ] `uv run ruff check app/ tests/` (or `.venv/bin/python -m ruff check app/ tests/`)
- [ ] `uv run pytest tests/ -q`
- [ ] `cd frontend && ./node_modules/.bin/tsc --noEmit`
- [ ] `cd frontend && npm run lint`
- [ ] Exercised the affected flow in a running stack, not just tests

<details>
<summary>Verification output</summary>

```
paste here
```

</details>

## Risk

<!-- What could this break? What is the rollback? Delete rows that do not apply. -->

- [ ] **Database migration** — reversible `downgrade()` written and tested; existing rows
      backfilled
- [ ] **Breaking change** — API contract, config, or data model; noted in `CHANGELOG.md`
- [ ] **Access control** — changes what a role can do; covered by a test that asserts the
      *denial* path, not only the allow path
- [ ] **MCP tool surface** — tool added, removed, or its scope changed
- [ ] **New dependency** — justified, and pinned in the lockfile
- [ ] **Config** — new env var documented in `.env.docker.example` *and* `.env.local.example`

## Docs

Per `CLAUDE.md`, architecture / API / data-model / RBAC / MCP changes update
`docs/DESIGN_DOCUMENT.md` in the same PR.

- [ ] Design document updated, or not applicable
- [ ] User-facing docs under `docs/` updated, or not applicable
- [ ] No secrets, tokens, or absolute local paths committed
