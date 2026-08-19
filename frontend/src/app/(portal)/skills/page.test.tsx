/**
 * #91 — the skills status poll keyed its effect on a derived string, and mutated a second
 * piece of state from inside a `setSkills` updater.
 *
 * The dep array was `[skills.map(s => s.status).join(",")]`. That string is positional, so a
 * list whose *statuses* line up the same way but whose *ids* have moved leaves it unchanged:
 * one skill finishes and a different one starts processing in the same window, the effect
 * never re-runs, and the interval keeps its stale id closure. The newly-processing skill is
 * polled **never** and sits on "Processing…" until someone reloads the page.
 *
 * `setTotal(prev => ...)` also ran inside the `setSkills` updater. Updaters must be pure —
 * StrictMode invokes them twice — so every deletion was subtracted from the header count twice.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StrictMode } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import SkillsPage from "./page";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn(), apiUpload: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    canAccess: () => true,
    hasPermission: () => true,
    user: { id: "u1", email: "a@b.c", role: "admin" },
  }),
}));

const mockApi = vi.mocked(api);

type Row = { id: string; status: string };

function skill({ id, status }: Row) {
  return {
    id,
    slug: id,
    name: id,
    description: "",
    status,
    current_version: 1,
    is_system: false,
    updated_at: "2026-01-01T00:00:00Z",
    created_at: "2026-01-01T00:00:00Z",
  };
}

/** Every `ids=` value the poll has asked about so far. */
function polledIds(): string[][] {
  return mockApi.mock.calls
    .map(([path]) => String(path))
    .filter((path) => path.includes("ids="))
    .map((path) => new URL(path, "http://x").searchParams.getAll("ids"));
}

/**
 * Routes every request the page and its children make. `lists` are successive generations of
 * the library; each full `/api/skills` fetch advances to the next one, and the poll answers
 * from the generation currently on screen — so a poll never reveals a state the list fetch
 * has not shown yet, which is what the real backend does too.
 */
function install(lists: Row[][]) {
  let shown = -1;
  mockApi.mockImplementation(((path: string) => {
    const p = String(path);
    if (p.startsWith("/api/skills?") && p.includes("ids=")) {
      const ids = new URL(p, "http://x").searchParams.getAll("ids");
      const current = lists[Math.max(0, shown)];
      const items = ids
        .map((id) => current.find((r) => r.id === id))
        .filter((r): r is Row => !!r)
        .map(skill);
      return Promise.resolve({ items, total: items.length });
    }
    if (p.startsWith("/api/skills?")) {
      shown = Math.min(shown + 1, lists.length - 1);
      const rows = lists[shown];
      return Promise.resolve({ items: rows.map(skill), total: rows.length });
    }
    if (p === "/api/departments") return Promise.resolve([]);
    if (p === "/api/skill-contributions") return Promise.resolve([]);
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

describe("processing-status poll (#91)", () => {
  it("polls a skill that started processing while another one finished in the same window", async () => {
    // Both lists join to the same status string ("processing,active"), which is exactly what
    // made the old dep array blind: the ids in the processing slot swapped underneath it.
    install([
      [{ id: "alpha", status: "processing" }, { id: "bravo", status: "active" }],
      [{ id: "charlie", status: "processing" }, { id: "alpha", status: "active" }],
    ]);

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<SkillsPage />);

    await waitFor(() => expect(screen.getByText("alpha")).toBeInTheDocument());
    await vi.advanceTimersByTimeAsync(3200);
    await waitFor(() => expect(polledIds().length).toBeGreaterThan(0));
    expect(polledIds()[0]).toEqual(["alpha"]);

    // Re-fetch the list (a search does it) so the second, id-swapped list lands.
    await user.type(screen.getByPlaceholderText(/search skills/i), "c");
    await vi.advanceTimersByTimeAsync(400);
    await waitFor(() => expect(screen.getByText("charlie")).toBeInTheDocument());

    const before = polledIds().length;
    await vi.advanceTimersByTimeAsync(3200);
    await waitFor(() => expect(polledIds().length).toBeGreaterThan(before));

    expect(polledIds().at(-1)).toEqual(["charlie"]);
  });

  it("stops polling once nothing is processing", async () => {
    install([[{ id: "alpha", status: "active" }]]);

    render(<SkillsPage />);
    await waitFor(() => expect(screen.getByText("alpha")).toBeInTheDocument());

    await vi.advanceTimersByTimeAsync(9000);
    expect(polledIds()).toEqual([]);
  });

  it("subtracts a vanished skill from the total exactly once", async () => {
    // The poll response omitting an id means the skill was deleted server-side. With the
    // decrement inside the `setSkills` updater, StrictMode's double invocation ran it twice
    // and the header count drifted below the real number of skills.
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.includes("ids=")) return Promise.resolve({ items: [], total: 0 });
      if (p.startsWith("/api/skills?")) {
        return Promise.resolve({
          items: [skill({ id: "alpha", status: "deleting" }), skill({ id: "bravo", status: "active" })],
          total: 2,
        });
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(
      <StrictMode>
        <SkillsPage />
      </StrictMode>
    );
    await waitFor(() => expect(screen.getByText("bravo")).toBeInTheDocument());

    await vi.advanceTimersByTimeAsync(3200);
    await waitFor(() => expect(screen.queryByText("alpha")).not.toBeInTheDocument());

    // One skill left, so the header must read "1 skill" — the doubled decrement made it 0.
    await waitFor(() => expect(screen.getByText("1 skill")).toBeInTheDocument());
  });
});
