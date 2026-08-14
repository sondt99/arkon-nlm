import { fetchToken, test } from "./fixtures";
import { VIEWPORTS } from "./routes";
import { checkRoute } from "./check-route";

const API_URL = process.env.PLAYWRIGHT_API_URL || "http://localhost:3119";

async function authedFetch(path: string, token: string, init: RequestInit = {}) {
  return fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}`, ...init.headers },
  });
}

const getToken = fetchToken;

test.describe("skill detail + edit (uses an existing system skill, read-only navigation)", () => {
  let slug: string | undefined;

  test.beforeAll(async () => {
    const token = await getToken();
    const skills = (await (await authedFetch("/api/skills?limit=1", token)).json()) as {
      items: Array<{ slug: string }>;
    };
    slug = skills.items[0]?.slug;
  });

  for (const viewport of VIEWPORTS) {
    test(`/skills/[slug] @ ${viewport.width}x${viewport.height}`, async ({ authedPage: page }) => {
      test.skip(!slug, "No skills exist in this environment");
      await checkRoute(page, `/skills/${slug}`, viewport, "skills_[slug]");
    });
  }

  for (const viewport of VIEWPORTS) {
    test(`/skills/[slug]/edit @ ${viewport.width}x${viewport.height}`, async ({ authedPage: page }) => {
      test.skip(!slug, "No skills exist in this environment");
      await checkRoute(page, `/skills/${slug}/edit`, viewport, "skills_[slug]_edit");
    });
  }
});

test.describe("wiki page detail (uses an existing wiki page, read-only navigation)", () => {
  let slug: string | undefined;

  test.beforeAll(async () => {
    const token = await getToken();
    const pages = (await (await authedFetch("/api/wiki/pages?limit=1", token)).json()) as Array<{ slug: string }>;
    slug = pages[0]?.slug;
  });

  for (const viewport of VIEWPORTS) {
    test(`/wiki/[...slug] @ ${viewport.width}x${viewport.height}`, async ({ authedPage: page }) => {
      test.skip(!slug, "No wiki pages exist in this environment");
      await checkRoute(page, `/wiki/${slug}`, viewport, "wiki_[slug]");
    });
  }
});

test.describe("workspace detail (creates + cleans up a throwaway workspace)", () => {
  let workspaceId = "";
  let token = "";

  test.beforeAll(async () => {
    token = await getToken();
    const res = await authedFetch("/api/projects", token, {
      method: "POST",
      body: JSON.stringify({ name: "__e2e_scratch_workspace__", description: "temporary, created by Playwright" }),
    });
    const data = (await res.json()) as { id: string };
    workspaceId = data.id;
  });

  test.afterAll(async () => {
    if (workspaceId) await authedFetch(`/api/projects/${workspaceId}`, token, { method: "DELETE" });
  });

  for (const viewport of VIEWPORTS) {
    test(`/workspaces/[id] @ ${viewport.width}x${viewport.height}`, async ({ authedPage: page }) => {
      await checkRoute(page, `/workspaces/${workspaceId}`, viewport, "workspaces_[id]");
    });
  }
});

test.describe("login (unauthenticated)", () => {
  for (const viewport of VIEWPORTS) {
    test(`/login @ ${viewport.width}x${viewport.height}`, async ({ page }) => {
      await checkRoute(page, "/login", viewport, "login");
    });
  }
});
