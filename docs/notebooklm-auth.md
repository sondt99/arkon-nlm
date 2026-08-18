# NotebookLM authentication

Arkon has no Google OAuth app of its own. It reuses a Google **session** that you import, then `notebooklm-py` talks to NotebookLM as that account.

There are two ways to create that session. Use the master token unless you have a reason not to.

---

## Option A — Master token (recommended)

`notebooklm-py` ≥ 0.8 can mint fresh NotebookLM cookies from a durable Google **master token** (`aas_et/…`, the Android / `gpsoauth` credential). No browser on the server, no cookie replay.

### Mint once (any machine with a browser)

```bash
pip install "notebooklm-py[headless]"
notebooklm login --master-token
```

That writes `master_token.json` (`{master_token, email, android_id}`).

### Import into Arkon

**NotebookLM → Connect → Master token** and paste the file, or:

```http
POST /api/notebooklm/auth/master-token
```

Arkon writes it next to `storage_state.json`, drops the old cookie session, and live-verifies by minting cookies. `verified: true` means LIST_NOTEBOOKS succeeded.

`DELETE /api/notebooklm/auth/session` removes **both** the cookies and the master token.

### Security

A master token is a **long-lived, full-account** credential. It can survive a password change until you revoke it. Use a throwaway / dedicated Google account. The file is `0600` and redacted from logs. The flow uses Google’s unofficial Android auth path and is ToS-grey.

---

## Option B — Cookie import (fallback)

1. Install [Cookie-Editor](https://cookie-editor.com/)
2. Open [notebooklm.google.com](https://notebooklm.google.com) while signed in
3. Export as JSON
4. Paste into **Connect → Cookies** immediately

`POST /api/notebooklm/auth/import-cookies` converts the export to a Playwright `storage_state.json` and **live-verifies** it (`refresh_auth`). The response includes `verified: true|false`.

`GET /api/notebooklm/auth/status` only checks that the cookie file exists and has the required names. It does **not** prove Google still accepts the session.

### Why a “good” export sometimes fails

`__Secure-1PSIDTS` rotates about every 30 minutes. If you wait between export and import, Google returns 302 to accounts.google.com and `RotateCookies` 401. That is almost always **staleness**, not “this account is blocked.”

Re-export and paste within a few minutes. Once imported, the worker’s 30-minute cron rotates the token. Stable cookies still die after days or weeks — that is what the master token fixes.

If a **fresh** import still 401s, the account may be on DBSC / Advanced Protection, which can refuse server-side cookie replay. Switch to the master token.

---

## Keepalive

`notebooklm_refresh_session_cron` runs at minute 0 and 30. You can also click **Refresh** (`POST /api/notebooklm/auth/refresh`).

Session files live in `NOTEBOOKLM_STORAGE_PATH` (Docker: `/data/notebooklm-session` on volume `notebooklm_data`).
