import { defineConfig, devices } from "@playwright/test";

const PORT = process.env.PLAYWRIGHT_PORT || "3210";
const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || `http://localhost:${PORT}`;
const API_URL = process.env.PLAYWRIGHT_API_URL || "http://localhost:3119";

/**
 * Runs against a local `next dev` pointed at the already-running docker-compose
 * backend (nginx on :3119) — the compose `frontend`/`api` images are baked builds
 * with no source bind-mount, so they can't reflect local edits. The backend stack
 * must already be up (`docker compose ps`); this config only manages the frontend.
 */
export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "off",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: `npm run dev -- -p ${PORT}`,
    url: BASE_URL,
    reuseExistingServer: true,
    timeout: 60_000,
    env: { NEXT_PUBLIC_API_URL: API_URL },
  },
});
