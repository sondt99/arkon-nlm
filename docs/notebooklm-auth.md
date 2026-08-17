# NotebookLM authentication — how "Connect" works and why cookie import can fail

## How the connect flow works
Arkon has no Google login of its own. It drives Google NotebookLM through the
`notebooklm-py` library using a Google **session** that you import as cookies:

1. `POST /api/notebooklm/auth/import-cookies` — paste a Cookie-Editor JSON export.
   Arkon converts it to a Playwright `storage_state.json` in the `notebooklm` volume,
   then **live-verifies** it (one real `refresh_auth` call) and returns `verified: true|false`.
2. `GET /api/notebooklm/auth/status` — fast, file-only check (does the cookie file exist
   with the required cookie names). It does **not** prove the session works — the live
   endpoints or the import verification do.
3. `POST /api/notebooklm/auth/refresh` — live check / keepalive.

`notebooklm-py` proves a session by `GET https://notebooklm.google.com/`. A valid session
returns 200 (and a CSRF token); an invalid one **302s to `accounts.google.com`**, which the
library reports as "Authentication expired or invalid". Before the GET it also POSTs
`accounts.google.com/RotateCookies` to refresh the rotating `__Secure-1PSIDTS` token.

## Known blocker (investigated 2026-08): Google rejects server-side replay
For at least one account, importing **fresh** cookies still fails, and a deep trace showed
this is **not** an arkon or `notebooklm-py` bug:

- Raw `GET notebooklm.google.com/` with a hand-built `Cookie:` header (bypassing arkon
  and the library) → **302 → login**.
- `POST accounts.google.com/RotateCookies` → **401 Unauthorized** — Google rejects the
  *stable* session cookies (SID / SAPISID / `__Secure-1PSID`) outright, not just a stale
  rotating token.
- Reproduces with cookies from **both Chrome and Firefox**.
- Container egress IP **equals** the host IP, so it is not an IP/VPN mismatch.
- The cookie set satisfies `notebooklm-py`'s own requirements
  (`MINIMUM_REQUIRED_COOKIES = {SID, __Secure-1PSIDTS}` plus a secondary binding via
  `OSID` or `APISID`+`SAPISID`).

**Conclusion:** Google is binding the session to the originating browser/device (DBSC —
Device Bound Session Credentials — now extended beyond Chrome) or enforcing an account
security policy (Workspace context-aware access / Advanced Protection). Replaying such a
session from a headless server is refused by design. No cookie-import variant can work for
that account; only a real browser holding the device-bound key can mint a usable session.

## The real fix: master-token headless auth (notebooklm-py ≥ 0.8.0, `[headless]` extra)
`notebooklm-py` 0.8.0 added exactly the server-side auth path we need (ADR-0023). Instead of
replaying short-lived browser cookies (which DBSC blocks), it uses a durable Google
**master token** (`aas_et/…`, the Android/`gpsoauth` credential) to **mint fresh NotebookLM
web cookies on demand, off-device, with no per-session browser** (`OAuthLogin → uberauth →
MergeSession`). The ADR verifies this works server-side (`batchexecute LIST_NOTEBOOKS →
200`). This is the foundation for unattended / remote deployments — precisely arkon's case.

How it works:
- Mint the master token **once**: `notebooklm login --master-token` does a single browser
  sign-in only to capture a single-use `oauth_token` from `accounts.google.com/EmbeddedSetup`,
  then stores a durable `master_token.json` (`0600`) beside `storage_state.json`.
- After that, when the session is expired, the client **auto-re-mints in-process** (layer 4
  of the refresh ladder) — no browser, self-healing, single-flight.
- Dependency: the master token needs `gpsoauth`, which ships only with the `[headless]` extra.

**Security (important):** a master token is a **full-account, long-lived** credential that
**survives password changes** until explicitly revoked — a much larger blast radius than an
expiring cookie file. The library's own guidance: **use a dedicated / throwaway Google
account for servers**, never your primary account. It is stored `0600` and redacted from
logs. The flow uses Google's unofficial Android auth path (`gpsoauth`) and is ToS-grey.

### Adoption in arkon (IMPLEMENTED)
1. Backend dependency is `notebooklm-py[markdown,headless]>=0.8.1` (adds `gpsoauth` + the
   master-token modules).
2. `POST /api/notebooklm/auth/master-token` (admin only) accepts the contents of a
   `master_token.json` (`{master_token, email, android_id}`), writes it `0600` beside
   `storage_state.json` in the `notebooklm` volume, removes the stale cookie session so the
   client mints cleanly, and **live-verifies** by minting a session (`verified: true|false`).
   `DELETE /api/notebooklm/auth/session` now also removes the master token.
3. The NotebookLM page's Connect dialog has a **Master token** tab (default) with the mint
   instructions and the dedicated-account warning; the Cookies tab remains for the fallback.

**How to mint (one time, on any machine with a browser):**
```
pip install "notebooklm-py[headless]"
notebooklm login --master-token      # sign in with a DEDICATED / throwaway account
```
Then open the generated `master_token.json` and paste its contents into the Master token tab.
The value is never logged and lives only in the runtime `notebooklm` volume.

### Fallback without master token
- A **personal Google account without Advanced Protection** and not under a Workspace policy
  may still allow plain cookie replay — try that first if you don't want a master token.
- The Playwright browser-login paths (L3/L5) also exist but need a browser in the image and
  are explicitly "not for a remote / hosted server".

## What arkon does about it
- Import live-verifies and returns `verified: false` with an actionable message instead of
  silently saving dead cookies.
- Auth-expiry is mapped to HTTP 401 across the live endpoints so the UI flips to the
  "Connect" state instead of getting stuck on a false "connected".
- Cookie values are never logged, committed, or stored outside the runtime `notebooklm`
  volume.
