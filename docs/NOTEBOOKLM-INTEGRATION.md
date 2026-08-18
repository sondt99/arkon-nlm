# NotebookLM

Arkon can drive [Google NotebookLM](https://notebooklm.google.com) for you: create notebooks, push sources, generate study artifacts, chat, and import the result back into the wiki.

It does **not** replace the MRP compiler. MRP builds the structured wiki. NotebookLM is an optional enrichment layer (podcasts, quizzes, long-form briefs).

Client library: [teng-lin/notebooklm-py](https://github.com/teng-lin/notebooklm-py) (`[markdown,headless]`).

---

## What works today

- Connect a Google session (cookie import **or** master token)
- Automatic refresh every 30 minutes (`notebooklm_refresh_session_cron`)
- CRUD notebooks (Arkon rows + live NLM notebooks)
- Add sources: URL, pasted text, file upload, or “send this Arkon document”
- Generate: audio, video, quiz, flashcards, report, slide deck, infographic, data table
- Preview artifacts in the portal
- Chat with a notebook (the backend attaches the notebook’s sources)
- **Add to Wiki** — ingest an artifact or a chat answer as wiki pages
- Session files persist in the `notebooklm_data` volume

---

## Connect

Open **NotebookLM**. The banner is the session.

**Prefer the Master token tab.** It survives cookie expiry and works on a headless server. How to mint one, and why cookie import sometimes fails: [notebooklm-auth.md](notebooklm-auth.md).

Cookie import is the fallback. Export from [Cookie-Editor](https://cookie-editor.com/) on notebooklm.google.com and paste **immediately** (`__Secure-1PSIDTS` rotates about every 30 minutes).

Use a **dedicated Google account** for the server. A master token is a long-lived, full-account credential.

---

## Daily use

1. **+** to create a notebook
2. Add sources (or send a document from **Documents → Send to NotebookLM**)
3. Generate an artifact
4. Preview it
5. Chat if you want a Q&A
6. **Add to Wiki** on a finished text artifact

Long jobs run on `arkon_worker` (`notebooklm_generate_task`, `notebooklm_ingest_artifact_task`).

---

## Storage

| Where | What |
|---|---|
| Volume `notebooklm_data` → `/data/notebooklm-session` | `storage_state.json`, `master_token.json` (`0600`) |
| Postgres `notebooklm_notebooks` / `notebooklm_artifacts` | Portal state |

`NOTEBOOKLM_STORAGE_PATH` defaults to that path in Docker. Wiping the volume forces a reconnect.

---

## Permissions

NotebookLM routes require a logged-in user. Connecting / disconnecting the Google session is an **admin** action. Generating artifacts and importing to the wiki follow normal wiki/document permissions.

---

## API groups

| Prefix | Purpose |
|---|---|
| `/api/notebooklm/auth/*` | Status, refresh, import cookies, master token, logout |
| `/api/notebooklm/notebooks/*` | Arkon-side notebook + artifact rows |
| `/api/notebooklm/nlm/*` | Live NotebookLM proxy (notebooks, sources, generate, chat, ingest) |

Full list: [API-REFERENCE.md](API-REFERENCE.md).
