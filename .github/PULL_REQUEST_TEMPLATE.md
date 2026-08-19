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

CI runs all of these on every PR and they are **required to pass before merge**
(branch protection on `main`). Run them locally first so review is not spent on
a red build:

- [ ] `uv run --extra dev ruff check .`
- [ ] `uv run --extra dev python -m pytest -q`  <!-- --extra dev is required: pytest is an optional dep -->
- [ ] `ARKON_ALLOW_DEFAULT_SECRET=1 uv run --extra dev python -c "import app.main as m; m.app.openapi()"`
      <!-- FastAPI 0.141 includes routers LAZILY, so a plain import proves nothing.
           openapi() forces materialization — this is what catches a bad signature. -->

- [ ] `cd frontend && npm run typecheck`
- [ ] `cd frontend && npm run test`
- [ ] `cd frontend && npm run lint`  <!-- ratchet: the error count may not rise; see .eslint-baseline.json -->
- [ ] `cd frontend && npm run build`
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
