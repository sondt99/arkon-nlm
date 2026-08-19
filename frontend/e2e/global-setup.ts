/**
 * Logs in ONCE for the whole run and shares the token with every worker via
 * process.env. Without this, each worker (and dynamic-routes' beforeAll) does
 * its own login, and the API's per-IP login rate limit (10 per 5 min) kills
 * the run — all host traffic reaches the API as a single Docker gateway IP.
 */
const API_URL = process.env.PLAYWRIGHT_API_URL || "http://localhost:3119";
const ADMIN_EMAIL = process.env.PLAYWRIGHT_ADMIN_EMAIL || "admin@arkon.local";
const ADMIN_PASSWORD = process.env.PLAYWRIGHT_ADMIN_PASSWORD;

export default async function globalSetup() {
  // No default. This used to fall back to "admin123", which app/config.py lists in
  // `weak_admin` and refuses to boot with — so any stack that had actually started could
  // not possibly have the password this script was trying, and the suite failed at setup by
  // construction. Worse, the error below then blamed the stack being down, sending every
  // debugger to check docker instead of their environment.
  if (!ADMIN_PASSWORD) {
    throw new Error(
      "PLAYWRIGHT_ADMIN_PASSWORD is not set. Export the admin password of the running " +
        "stack (ARKON_ADMIN_PASSWORD in your .env.docker) first:\n" +
        "  export PLAYWRIGHT_ADMIN_PASSWORD=...\n" +
        "There is deliberately no default — app/config.py rejects every password weak " +
        "enough to be one."
    );
  }

  const res = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: ADMIN_EMAIL, password: ADMIN_PASSWORD }),
  });
  if (!res.ok) {
    throw new Error(
      `Login failed (${res.status}) against ${API_URL} — is the docker-compose stack up? ` +
        `(docker compose -f docker-compose.yml ps). A 429 means the login rate limit is ` +
        `saturated; wait 5 minutes and retry.`
    );
  }
  const data = (await res.json()) as { access_token: string };
  process.env.PLAYWRIGHT_SHARED_TOKEN = data.access_token;
}
