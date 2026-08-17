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

## Cookie import: works only while the session is FRESH (investigated 2026-08)
Cookie import **does** work — but only if the cookies are imported while
`__Secure-1PSIDTS` is still valid. That token rotates ~every 30 min, so a delay between
exporting in the browser and importing here makes Google reject the whole session.

Confirmed working: a fresh Chrome export, imported immediately, verified live and listed
**63 real notebooks** (`/nlm/notebooks` → 200). The same account had previously failed
across several attempts — but those attempts had long multi-step delays between export and
import, during which the trace showed the tell-tale stale-session signature:
`GET notebooklm.google.com/` → 302 login and `POST accounts.google.com/RotateCookies` → 401.
So the earlier "DBSC blocks this account" conclusion was **wrong**: the real cause was a
**stale `__Secure-1PSIDTS`**, not device binding. (Egress IP equals the host IP, and the
cookie set always satisfied `MINIMUM_REQUIRED_COOKIES = {SID, __Secure-1PSIDTS}` + a
secondary binding.)

**Practical rule:** export cookies and import them **immediately** (within a few minutes).
If verification fails, re-export and retry right away rather than assuming the account is
blocked. Once imported, `notebooklm-py`'s keepalive rotates `__Secure-1PSIDTS` to keep the
session alive; it will still eventually decay when the stable cookies are culled (days/weeks)
— which is what the master token below solves permanently.

DBSC / Advanced-Protection accounts genuinely *can* refuse server-side replay outright, so
if a fast fresh import still 401s, that is the likely cause — but freshness is the first
thing to rule out.

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
