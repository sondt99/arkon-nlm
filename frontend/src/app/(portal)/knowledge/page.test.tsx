/**
 * #91 — the document poll rebuilt its interval on every response and could overwrite a
 * fresh search.
 *
 * The effect depended on `sources`, the very array its own callback replaces, so the timer
 * was torn down and recreated on every cycle (an `eslint-disable` hid the loop). And nothing
 * ordered the responses: a poll that left before the user typed could land after the search
 * request, so the search box showed the query while the table showed everything.
 *
 * `plan_ready` was also treated as pending. It is terminal until a human approves the plan —
 * `notebooklm/page.tsx` classifies it that way — so a document parked in review fired a
 * request every three seconds forever with nothing able to change the answer.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import KnowledgePage from "./page";
import { api } from "@/lib/api";
import { tick } from "@/test/tick";

vi.mock("@/lib/api", () => ({ api: vi.fn(), apiUpload: vi.fn() }));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    canAccess: () => true,
    hasPermission: () => true,
    user: { id: "u1", email: "a@b.c", role: "admin" },
  }),
}));

const mockApi = vi.mocked(api);

function source(title: string, status = "ready") {
  return {
    id: title,
    title,
    status,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function page(items: ReturnType<typeof source>[]) {
  return { items, total: items.length, page: 1, page_size: 20, total_pages: 1 };
}

function deferred<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    release = res;
  });
  return { promise, release };
}

/** Every `/api/sources` request the page has made. */
const sourceCalls = () =>
  mockApi.mock.calls.map(([p]) => String(p)).filter((p) => p.startsWith("/api/sources?"));

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
});

describe("stale response ordering (#91)", () => {
  it("does not let an in-flight unfiltered poll overwrite the results of a newer search", async () => {
    const stalePoll = deferred<ReturnType<typeof page>>();
    let sourceCall = 0;

    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.startsWith("/api/sources?")) {
        sourceCall += 1;
        if (sourceCall === 1) return Promise.resolve(page([source("Everything", "processing")]));
        // The poll tick — parked so the search below overtakes it.
        if (sourceCall === 2) return stalePoll.promise;
        return Promise.resolve(page([source("Filtered result")]));
      }
      if (p === "/api/knowledge-types" || p === "/api/departments") return Promise.resolve([]);
      return Promise.resolve([]);
    }) as unknown as typeof api);

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(<KnowledgePage />);
    await waitFor(() => expect(screen.getByText("Everything")).toBeInTheDocument());

    // Let one poll tick leave.
    await tick(3200);
    await waitFor(() => expect(sourceCalls().length).toBeGreaterThanOrEqual(2));

    // Search, and let it come back first.
    await user.type(screen.getByPlaceholderText(/search documents/i), "filtered{Enter}");
    await waitFor(() => expect(screen.getByText("Filtered result")).toBeInTheDocument());

    // Now the pre-search poll finally answers with the unfiltered list.
    stalePoll.release(page([source("Everything", "processing")]));
    // Flushed generously: without the guard the overwrite does land, just a couple of
    // scheduler turns later, and a short flush would let this test pass either way.
    await tick(250);
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(screen.getByText("Filtered result")).toBeInTheDocument();
    expect(screen.queryByText("Everything")).not.toBeInTheDocument();
  });
});

describe("poll lifecycle (#91)", () => {
  it("keeps one stable interval instead of rebuilding it on every response", async () => {
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.startsWith("/api/sources?")) {
        return Promise.resolve(page([source("Working", "processing")]));
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    // Filtered to the poll's own 3000ms cadence: `waitFor` drives its own interval, so an
    // unfiltered spy counts testing-library's timers too.
    const setSpy = vi.spyOn(globalThis, "setInterval");
    const pollTimers = () => setSpy.mock.calls.filter(([, delay]) => delay === 3000).length;

    render(<KnowledgePage />);
    await waitFor(() => expect(screen.getByText("Working")).toBeInTheDocument());

    await tick(9500); // three ticks
    await waitFor(() => expect(sourceCalls().length).toBeGreaterThanOrEqual(4));

    // The old effect depended on `sources`, so each response tore the timer down and built a
    // new one. One interval covers the whole run.
    expect(pollTimers()).toBe(1);
  });

  it("does not poll a document waiting on human plan review", async () => {
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.startsWith("/api/sources?")) {
        return Promise.resolve(page([source("Awaiting review", "plan_ready")]));
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(<KnowledgePage />);
    await waitFor(() => expect(screen.getByText("Awaiting review")).toBeInTheDocument());

    const before = sourceCalls().length;
    await tick(12_000);
    expect(sourceCalls().length).toBe(before);
  });

  it("still polls a document that is genuinely processing", async () => {
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.startsWith("/api/sources?")) {
        return Promise.resolve(page([source("Working", "processing")]));
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(<KnowledgePage />);
    await waitFor(() => expect(screen.getByText("Working")).toBeInTheDocument());

    const before = sourceCalls().length;
    await tick(3200);
    await waitFor(() => expect(sourceCalls().length).toBeGreaterThan(before));
  });
});

describe("load failure (#91)", () => {
  it("reports a failed user-initiated load rather than an empty library", async () => {
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.startsWith("/api/sources?")) return Promise.reject(new Error("gateway timeout"));
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(<KnowledgePage />);
    await waitFor(() =>
      expect(screen.getByText(/couldn't load documents/i)).toBeInTheDocument()
    );
  });

  it("keeps the rows it already has when a background poll tick fails", async () => {
    let sourceCall = 0;
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.startsWith("/api/sources?")) {
        sourceCall += 1;
        if (sourceCall === 1) return Promise.resolve(page([source("Working", "processing")]));
        return Promise.reject(new Error("blip"));
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(<KnowledgePage />);
    await waitFor(() => expect(screen.getByText("Working")).toBeInTheDocument());

    await tick(3200);
    await waitFor(() => expect(sourceCalls().length).toBeGreaterThanOrEqual(2));

    // One failed tick is not evidence the library is empty, and no error banner either.
    expect(screen.getByText("Working")).toBeInTheDocument();
    expect(screen.queryByText(/couldn't load documents/i)).not.toBeInTheDocument();
  });
});
