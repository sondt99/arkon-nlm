import { test as base, expect, type Page } from "@playwright/test";

const API_URL = process.env.PLAYWRIGHT_API_URL || "http://localhost:3119";
const ADMIN_EMAIL = process.env.PLAYWRIGHT_ADMIN_EMAIL || "admin@arkon.local";
const ADMIN_PASSWORD = process.env.PLAYWRIGHT_ADMIN_PASSWORD || "admin123";

let cachedToken: string | null = null;

export async function fetchToken(): Promise<string> {
  // Token from global-setup (one login per run) — avoids the per-IP login
  // rate limit that N parallel workers would otherwise trip.
  if (process.env.PLAYWRIGHT_SHARED_TOKEN) return process.env.PLAYWRIGHT_SHARED_TOKEN;
  if (cachedToken) return cachedToken;
  const res = await fetch(`${API_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: ADMIN_EMAIL, password: ADMIN_PASSWORD }),
  });
  if (!res.ok) {
    throw new Error(
      `Login failed (${res.status}) against ${API_URL} — is the docker-compose stack up? ` +
        `(docker compose -f docker-compose.yml ps)`
    );
  }
  const data = (await res.json()) as { access_token: string };
  cachedToken = data.access_token;
  return cachedToken;
}

export const test = base.extend<{ authedPage: Page }>({
  authedPage: async ({ page }, runTest) => {
    const token = await fetchToken();
    await page.addInitScript((t) => window.localStorage.setItem("arkon_token", t), token);
    await runTest(page);
  },
});

export { expect };
