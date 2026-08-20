/**
 * #91 — "add page" derived its slug from `pages.length + 1`, so the same slug could be handed
 * out twice.
 *
 * add / add / delete-the-first / add produces two pages called `new-page-2`, and the approve
 * request carries both. The list is keyed by array index, so React never warned about the
 * duplicate. Priorities collided the same way.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PlanReviewDialog } from "./plan-review-dialog";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const SOURCE = {
  id: "src-1",
  title: "Quarterly report",
  status: "plan_ready",
  created_at: "2026-01-01T00:00:00Z",
} as unknown as React.ComponentProps<typeof PlanReviewDialog>["source"];

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
    if (opts?.method === "POST") return Promise.resolve({});
    return Promise.resolve({ plan: { pages: [] } });
  }) as unknown as typeof api);
});

/** Every slug currently rendered in the plan list. */
function slugs(): string[] {
  return screen
    .getAllByText(/^new-page-\d+$/)
    .map((node) => node.textContent ?? "");
}

const addPage = () => screen.getByRole("button", { name: /Add page/ });

/** Adding a page opens its editor; close it so the next slug is visible in the list. */
async function addAndClose(user: ReturnType<typeof userEvent.setup>) {
  await user.click(addPage());
  await user.click(screen.getByRole("button", { name: "Cancel" }));
}

/** Two-stage delete: the row's trash icon arms it, then the row is replaced by a "Delete" confirm. */
async function deleteRow(user: ReturnType<typeof userEvent.setup>, slug: string) {
  const row = screen.getByText(slug).closest(".group") as HTMLElement;
  await user.click(within(row).getByTitle("Delete"));
  await user.click(await screen.findByRole("button", { name: "Delete" }));
}

describe("plan page slugs (#91)", () => {
  it("never reuses a slug after the first page is deleted", async () => {
    const user = userEvent.setup();
    render(<PlanReviewDialog source={SOURCE} onClose={vi.fn()} onDone={vi.fn()} />);
    await waitFor(() => expect(addPage()).toBeInTheDocument());

    await addAndClose(user);
    await addAndClose(user);
    expect(slugs()).toEqual(["new-page-1", "new-page-2"]);

    await deleteRow(user, "new-page-1");
    await waitFor(() => expect(slugs()).toEqual(["new-page-2"]));

    // `pages.length + 1` is 2 again here — a slug already in use.
    await addAndClose(user);
    expect(slugs()).toEqual(["new-page-2", "new-page-3"]);
  });

  it("submits a plan whose slugs and priorities are all distinct", async () => {
    const user = userEvent.setup();
    render(<PlanReviewDialog source={SOURCE} onClose={vi.fn()} onDone={vi.fn()} />);
    await waitFor(() => expect(addPage()).toBeInTheDocument());

    await addAndClose(user);
    await addAndClose(user);
    await deleteRow(user, "new-page-1");
    await waitFor(() => expect(slugs()).toEqual(["new-page-2"]));
    await addAndClose(user);

    await user.click(screen.getByRole("button", { name: /Approve/i }));
    await waitFor(() =>
      expect(
        mockApi.mock.calls.some(([path]) => String(path).endsWith("/plan/approve"))
      ).toBe(true)
    );

    const approve = mockApi.mock.calls.find(([path]) => String(path).endsWith("/plan/approve"));
    const body = (approve?.[1] as { body: { modified_plan: { pages: { slug: string; priority?: number }[] } } }).body;

    const submitted = body.modified_plan.pages.map((p) => p.slug);
    expect(submitted).toHaveLength(2);
    expect(new Set(submitted).size).toBe(submitted.length);

    const priorities = body.modified_plan.pages.map((p) => p.priority);
    expect(new Set(priorities).size).toBe(priorities.length);
  });
});
