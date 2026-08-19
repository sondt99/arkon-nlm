/**
 * #91 — the skill detail page double-fetched on every load and used `window.location.reload()`
 * as a refresh.
 *
 * The load effect depended on `viewingVersion` and also *set* it from the response, so it
 * re-triggered itself: two round-trips and two spinner flashes per page view. Six handlers then
 * hard-reloaded the document, throwing away the SPA — bundle re-downloaded, `AuthProvider`
 * re-running `/api/auth/me`, sidebar and file-tree state reset — to refresh two endpoints.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import SkillDetailPage from "./page";
import { api } from "@/lib/api";

vi.mock("@/lib/api", async () => {
  const actual = await import("@/lib/api");
  return { api: vi.fn(), apiUpload: vi.fn(), ApiError: actual.ApiError };
});

vi.mock("next/navigation", () => ({
  useParams: () => ({ slug: "pdf-extract" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/skills/pdf-extract",
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    canAccess: () => true,
    hasPermission: () => true,
    user: { id: "u1", email: "a@b.c", role: "admin" },
  }),
}));

const mockApi = vi.mocked(api);

const SKILL = {
  id: "s1",
  slug: "pdf-extract",
  name: "pdf-extract",
  description: "Pull tables out of PDFs",
  status: "active",
  current_version: 3,
  is_system: false,
  updated_at: "2026-01-01T00:00:00Z",
  created_at: "2026-01-01T00:00:00Z",
};

const VERSIONS = [{ version_number: 3 }, { version_number: 2 }, { version_number: 1 }];

/** Every request for the skill record itself, including versioned variants. */
const skillFetches = () =>
  mockApi.mock.calls
    .map(([p]) => String(p))
    .filter((p) => /^\/api\/skills\/pdf-extract(\?|$)/.test(p));

let reload: ReturnType<typeof vi.fn>;

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.mockImplementation(((path: string) => {
    const p = String(path);
    if (p.endsWith("/versions")) return Promise.resolve(VERSIONS);
    if (/^\/api\/skills\/pdf-extract(\?|$)/.test(p)) return Promise.resolve(SKILL);
    if (p.includes("/files")) return Promise.resolve([]);
    return Promise.resolve([]);
  }) as unknown as typeof api);

  reload = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname: "/skills/pdf-extract", search: "", href: "http://localhost/skills/pdf-extract", reload, assign: vi.fn() },
  });
  vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
  vi.stubGlobal("alert", vi.fn());
});

describe("skill detail load (#91)", () => {
  it("fetches the skill once per page view", async () => {
    render(<SkillDetailPage />);
    await waitFor(() => expect(screen.getByText("Version History")).toBeInTheDocument());

    // Settle anything the effects might still chain.
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(skillFetches()).toHaveLength(1);
  });

  it("requests the unparameterised endpoint until the user picks a version", async () => {
    render(<SkillDetailPage />);
    await waitFor(() => expect(skillFetches()).toHaveLength(1));
    expect(skillFetches()[0]).toBe("/api/skills/pdf-extract");
  });

  it("shows the current version as selected without a second round trip", async () => {
    render(<SkillDetailPage />);
    await waitFor(() => expect(screen.getByText("Version History")).toBeInTheDocument());

    // `viewingVersion` is derived from the response, so the newest version is already the
    // selection — the second fetch existed only to discover it. "PREVIEWING OLD" appears
    // whenever the selection and `current_version` disagree, so its absence is the assertion.
    expect(screen.queryByText("PREVIEWING OLD")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Set as Official Latest/i })).not.toBeInTheDocument();
  });
});

describe("refresh instead of reload (#91)", () => {
  it("re-fetches in place when promoting an older version", async () => {
    const user = userEvent.setup();
    render(<SkillDetailPage />);
    await waitFor(() => expect(screen.getByText("Version History")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { hidden: true }));
    await user.click(await screen.findByRole("option", { name: /Version 2/, hidden: true }));

    await waitFor(() => expect(screen.getByText("PREVIEWING OLD")).toBeInTheDocument());
    const beforePromote = skillFetches().length;

    await user.click(screen.getByRole("button", { name: /Set as Official Latest/i }));
    await waitFor(() =>
      expect(
        mockApi.mock.calls.some(([p]) => String(p).includes("/set-latest"))
      ).toBe(true)
    );

    // The whole point: a re-fetch, not a document reload.
    expect(reload).not.toHaveBeenCalled();
    await waitFor(() => expect(skillFetches().length).toBeGreaterThan(beforePromote));
  });

  it("returns to the current version after promoting", async () => {
    const user = userEvent.setup();
    render(<SkillDetailPage />);
    await waitFor(() => expect(screen.getByText("Version History")).toBeInTheDocument());

    await user.click(screen.getByRole("combobox", { hidden: true }));
    await user.click(await screen.findByRole("option", { name: /Version 2/, hidden: true }));
    await waitFor(() => expect(screen.getByText("PREVIEWING OLD")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Set as Official Latest/i }));

    // A hard reload used to reset the selection; `refresh()` has to do the same thing.
    await waitFor(() => expect(screen.queryByText("PREVIEWING OLD")).not.toBeInTheDocument());
  });
});

describe("load failure (#91)", () => {
  it("says what happened instead of rendering a blank page", async () => {
    mockApi.mockImplementation(((path: string) => {
      const p = String(path);
      if (p.endsWith("/versions")) return Promise.resolve(VERSIONS);
      if (/^\/api\/skills\/pdf-extract(\?|$)/.test(p)) {
        return Promise.reject(new Error("upstream exploded"));
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    render(<SkillDetailPage />);
    await waitFor(() =>
      expect(screen.getByText(/couldn't load this skill/i)).toBeInTheDocument()
    );
  });
});
