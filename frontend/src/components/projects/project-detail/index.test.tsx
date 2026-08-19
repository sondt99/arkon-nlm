/**
 * #91 — the workspace status poll never terminated, and flashed the Wiki tab into a spinner
 * every three seconds.
 *
 * `hasPending` included `plan_ready`, which is terminal until a human approves the plan
 * (`notebooklm/page.tsx` classifies it the same way), so a workspace sitting in plan review
 * fired three requests every three seconds forever with nothing on the server able to change
 * the answer. And each tick called `loadWiki()`, which set `wikiLoading(true)` — replacing the
 * page list with a spinner on every tick. The sibling knowledge page already had a `silent`
 * flag for exactly this; this copy did not.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

import { ProjectDetail } from "./index";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    canAccess: () => true,
    hasPermission: () => true,
    user: { id: "u1", email: "a@b.c", role: "admin" },
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/workspaces/p1",
}));

const mockApi = vi.mocked(api);

const PROJECT = {
  id: "p1",
  name: "Acme onboarding",
  workspace_type: "customer",
  status: "active",
  member_count: 0,
  source_count: 1,
};

const WIKI_PAGES = [
  { slug: "acme", title: "Acme", page_type: "entity", summary: "", updated_at: "" },
];

/** Requests grouped by endpoint family. */
const callsTo = (fragment: string) =>
  mockApi.mock.calls.map(([p]) => String(p)).filter((p) => p.includes(fragment));

function install(sourceStatus: string) {
  mockApi.mockImplementation(((path: string) => {
    const p = String(path);
    if (p.endsWith("/wiki/index")) return Promise.resolve({ content_md: "# Index" });
    if (p.includes("/wiki?")) return Promise.resolve(WIKI_PAGES);
    if (p.includes("/members")) return Promise.resolve([]);
    if (p.includes(`/projects/${PROJECT.id}/sources`)) {
      return Promise.resolve([{ source_id: "s1", title: "Contract.pdf", status: sourceStatus }]);
    }
    if (p.startsWith("/api/employees")) return Promise.resolve({ items: [] });
    if (p.startsWith("/api/sources")) return Promise.resolve({ items: [] });
    return Promise.resolve([]);
  }) as unknown as typeof api);
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("workspace status poll (#91)", () => {
  it("does not poll a workspace waiting on human plan review", async () => {
    install("plan_ready");
    render(<ProjectDetail project={PROJECT} isAdmin onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    const before = callsTo("/sources").length;
    await vi.advanceTimersByTimeAsync(12_000);
    expect(callsTo("/sources").length).toBe(before);
  });

  it("still polls a workspace whose document is processing", async () => {
    install("processing");
    render(<ProjectDetail project={PROJECT} isAdmin onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    const before = callsTo(`/projects/${PROJECT.id}/sources`).length;
    await vi.advanceTimersByTimeAsync(3200);
    await waitFor(() =>
      expect(callsTo(`/projects/${PROJECT.id}/sources`).length).toBeGreaterThan(before)
    );
  });

  it("keeps the wiki page list on screen while a poll tick is in flight", async () => {
    // The flash only exists while the tick's request is outstanding, so the poll's wiki
    // request is parked. A non-silent `loadWiki()` sets `wikiLoading(true)` on entry, and
    // WikiPageTree renders a skeleton instead of its list whenever `loading` is true.
    let wikiCall = 0;
    let releasePoll!: (pages: typeof WIKI_PAGES) => void;
    const parked = new Promise<typeof WIKI_PAGES>((resolve) => {
      releasePoll = resolve;
    });

    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.endsWith("/wiki/index")) return Promise.resolve({ content_md: "# Index" });
      if (p.includes("/wiki?")) {
        wikiCall += 1;
        return wikiCall === 1 ? Promise.resolve(WIKI_PAGES) : parked;
      }
      if (p.includes("/members")) return Promise.resolve([]);
      if (p.includes(`/projects/${PROJECT.id}/sources`)) {
        return Promise.resolve([{ source_id: "s1", title: "Contract.pdf", status: "processing" }]);
      }
      if (p.startsWith("/api/employees")) return Promise.resolve({ items: [] });
      if (p.startsWith("/api/sources")) return Promise.resolve({ items: [] });
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(<ProjectDetail project={PROJECT} isAdmin onBack={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("Acme")).toBeInTheDocument());

    await vi.advanceTimersByTimeAsync(3100);
    await waitFor(() => expect(wikiCall).toBeGreaterThan(1));

    // Still in flight — the list must not have been replaced by a spinner.
    expect(screen.getByText("Acme")).toBeInTheDocument();

    releasePoll(WIKI_PAGES);
    await vi.advanceTimersByTimeAsync(50);
    expect(screen.getByText("Acme")).toBeInTheDocument();
  });
});
