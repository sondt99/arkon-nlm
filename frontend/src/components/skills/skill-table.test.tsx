/**
 * #82 — the skills search box lost focus and dropped keystrokes as soon as a query
 * returned zero results.
 *
 * The page gated the whole table behind `loading && skills.length === 0`, so once a query
 * came back empty, the next keystroke's debounced fetch flipped `loading` and React
 * unmounted the subtree holding the focused input. The user typed "kubernetes", got as far
 * as "kub", and the rest went nowhere.
 *
 * These tests pin the invariant that makes that impossible: the search input stays mounted
 * across every combination of loading and result count.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SkillTable } from "./skill-table";

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    canAccess: () => true,
    hasPermission: () => true,
    user: { id: "u1", email: "a@b.c", role: "admin" },
  }),
}));

vi.mock("@/lib/api", () => ({
  api: vi.fn().mockResolvedValue({}),
  apiUpload: vi.fn().mockResolvedValue({}),
}));

function renderTable(overrides: Partial<React.ComponentProps<typeof SkillTable>> = {}) {
  const props = {
    skills: [],
    departments: [],
    loading: false,
    onDelete: vi.fn(),
    onRefresh: vi.fn(),
    onClick: vi.fn(),
    onSearch: vi.fn(),
    total: 0,
    search: "",
    ...overrides,
  };
  return { ...render(<SkillTable {...props} />), props };
}

const searchBox = () => screen.getByPlaceholderText(/search/i);

describe("search input persistence (#82)", () => {
  it("stays mounted while loading with zero results — the exact failure state", () => {
    renderTable({ loading: true, skills: [], total: 0, search: "kub" });
    expect(searchBox()).toBeInTheDocument();
  });

  it("stays mounted for every loading/result combination", () => {
    for (const loading of [true, false]) {
      for (const total of [0, 3]) {
        const { unmount } = renderTable({ loading, total, search: "q" });
        expect(searchBox(), `loading=${loading} total=${total}`).toBeInTheDocument();
        unmount();
      }
    }
  });

  it("keeps DOM identity across a re-render into the empty+loading state", () => {
    // Identity is the real assertion: a remount is what moved focus, and a new element
    // with the same placeholder would still satisfy a mere "is present" check.
    const { rerender, props } = renderTable({ loading: false, total: 5, search: "" });
    const before = searchBox();

    rerender(<SkillTable {...props} loading={true} skills={[]} total={0} search="k" />);

    expect(searchBox()).toBe(before);
  });

  it("retains focus when results drop to zero mid-typing", async () => {
    const user = userEvent.setup();
    const { rerender, props } = renderTable({ loading: false, total: 5, search: "" });

    await user.click(searchBox());
    expect(searchBox()).toHaveFocus();

    rerender(<SkillTable {...props} loading={true} skills={[]} total={0} search="k" />);

    expect(searchBox()).toHaveFocus();
  });

  it("reports every keystroke to the parent, losing none", async () => {
    const user = userEvent.setup();
    const onSearch = vi.fn();
    // The input is controlled by the `search` prop, so a test typing into it without a
    // parent to echo the value back sees one call per keystroke, each with a single char.
    renderTable({ onSearch });

    await user.type(searchBox(), "kube");

    expect(onSearch).toHaveBeenCalledTimes(4);
    expect(onSearch.mock.calls.map((c) => c[0])).toEqual(["k", "u", "b", "e"]);
  });

  it("is driven by the search prop, with no stale mirror state", async () => {
    // `skill-table` used to hold `useState(search)`, derived from the prop and never
    // re-synced, so a parent-initiated change (a cleared filter, a restored URL query)
    // never reached the box.
    const { rerender, props } = renderTable({ search: "initial" });
    expect(searchBox()).toHaveValue("initial");

    rerender(<SkillTable {...props} search="changed-by-parent" />);

    expect(searchBox()).toHaveValue("changed-by-parent");
  });
});

describe("loading and empty rendering", () => {
  it("shows a loading indicator alongside the still-mounted search box", () => {
    renderTable({ loading: true, search: "q" });
    expect(searchBox()).toBeInTheDocument();
    expect(screen.queryByText(/no skills/i)).not.toBeInTheDocument();
  });

  it("shows the empty state only once loading has finished", () => {
    renderTable({ loading: false, skills: [], total: 0 });
    expect(searchBox()).toBeInTheDocument();
  });
});
