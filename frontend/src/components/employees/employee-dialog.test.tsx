/**
 * #91 — the inline "create department" prompt could be submitted twice.
 *
 * The submit button carried `disabled={inlinePrompt.saving || !value.trim()}`, but the input's
 * Enter handler called `submitInlinePrompt()` straight through. Pressing Enter twice before the
 * POST returned created two departments with the same name. The guard belongs in the function,
 * not on one of its two call sites.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EmployeeDialog } from "./employee-dialog";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    canAccess: () => true,
    hasPermission: () => true,
    user: { id: "u1", email: "a@b.c", role: "admin" },
  }),
}));

const mockApi = vi.mocked(api);

/** Every POST /api/departments the dialog issued. */
const departmentPosts = () =>
  mockApi.mock.calls.filter(
    ([path, opts]) => path === "/api/departments" && (opts as { method?: string })?.method === "POST"
  );

function deferred<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    release = res;
  });
  return { promise, release };
}

beforeEach(() => {
  vi.clearAllMocks();
});

async function openCreateDepartmentPrompt(
  user: ReturnType<typeof userEvent.setup>
): Promise<HTMLInputElement> {
  render(
    <EmployeeDialog
      open
      onOpenChange={vi.fn()}
      employee={null}
      departments={[{ id: "d1", name: "Engineering" }]}
      onSaved={vi.fn()}
    />
  );

  // `hidden: true` throughout: the dialog is portalled and jsdom has none of the CSS that
  // makes its contents visible to the accessibility tree. ARIA does not compute a combobox
  // name from its content and these triggers carry no aria-label, so the department Select is
  // located positionally — asserted, so a reordered form fails loudly instead of silently
  // driving the wrong control.
  const combos = screen.getAllByRole("combobox", { hidden: true });
  const departmentSelect = combos[1];
  expect(departmentSelect).toHaveTextContent("Engineering");

  await user.click(departmentSelect);
  await user.click(await screen.findByRole("option", { name: /Create new department/i, hidden: true }));

  // The prompt's Input has no `id`/`for` pairing, so it is reached through its label's row.
  const label = await screen.findByText(/Department Name/i);
  return label.parentElement!.querySelector("input")!;
}

describe("inline create-department prompt (#91)", () => {
  it("creates one department when Enter is pressed twice before the request returns", async () => {
    const gate = deferred<{ id: string; name: string }>();
    mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
      if (path === "/api/departments" && opts?.method === "POST") return gate.promise;
      return Promise.resolve({});
    }) as unknown as typeof api);

    const user = userEvent.setup();
    const input = await openCreateDepartmentPrompt(user);

    await user.type(input, "Platform");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(departmentPosts()).toHaveLength(1));

    // Impatient second Enter, while the first POST is still in flight.
    await user.keyboard("{Enter}");
    await user.keyboard("{Enter}");

    expect(departmentPosts()).toHaveLength(1);

    gate.release({ id: "d2", name: "Platform" });
    await waitFor(() => expect(screen.queryByText(/Department Name/i)).not.toBeInTheDocument());
  });

  it("does not submit an empty name", async () => {
    mockApi.mockImplementation((() => Promise.resolve({})) as unknown as typeof api);

    const user = userEvent.setup();
    const input = await openCreateDepartmentPrompt(user);

    await user.click(input);
    await user.keyboard("{Enter}");
    expect(departmentPosts()).toHaveLength(0);
  });
});
