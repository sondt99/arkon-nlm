/**
 * #91 — the fork dialog normally submitted `base_version: null`.
 *
 * `selectedVersion` was seeded from the `versions` prop in a `useState` initializer. The parent
 * fetches that list in an effect, so on the render that mounted this dialog the array was still
 * empty — and nothing ever re-synced it. The user saw the version dropdown fill in and submitted
 * anyway, and the contribution was recorded with no base version.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { SkillContributeDialog } from "./skill-contribute-dialog";
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

/** The body of the POST that creates the contribution. */
function createdBody() {
  const call = mockApi.mock.calls.find(
    ([path, opts]) => path === "/api/skill-contributions" && (opts as { method?: string })?.method === "POST"
  );
  return (call?.[1] as { body: { base_version: number | null; skill_id: string | null } })?.body;
}

beforeEach(() => {
  vi.clearAllMocks();
  mockApi.mockImplementation((() => Promise.resolve({ id: "contrib-1", items: [] })) as unknown as typeof api);
});

/** Mounts with no versions, then supplies them — how the real parent behaves. */
function renderWithLateVersions() {
  const props = {
    skillId: "skill-1",
    skillName: "pdf-extract",
    versions: [] as { version_number: number }[],
    onContributionCreated: vi.fn(),
  };
  const view = render(<SkillContributeDialog {...props} />);
  const supply = (versions: { version_number: number }[]) =>
    view.rerender(<SkillContributeDialog {...props} versions={versions} />);
  return { ...view, props, supply };
}

describe("fork base version (#91)", () => {
  it("submits the newest version when the list arrives after mount", async () => {
    const user = userEvent.setup();
    const { supply } = renderWithLateVersions();

    supply([{ version_number: 7 }, { version_number: 6 }, { version_number: 5 }]);

    await user.click(screen.getByRole("button", { name: /Contribute/i }));
    await user.click(await screen.findByRole("button", { name: /Start Improving/i }));

    await waitFor(() => expect(createdBody()).toBeTruthy());
    expect(createdBody().base_version).toBe(7);
    expect(createdBody().skill_id).toBe("skill-1");
  });

  it("submits the version the user picked instead of the newest", async () => {
    const user = userEvent.setup();
    const { supply } = renderWithLateVersions();
    supply([{ version_number: 7 }, { version_number: 6 }]);

    await user.click(screen.getByRole("button", { name: /Contribute/i }));
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByRole("option", { name: "Version 6" }));
    await user.click(screen.getByRole("button", { name: /Start Improving/i }));

    await waitFor(() => expect(createdBody()).toBeTruthy());
    expect(createdBody().base_version).toBe(6);
  });

  it("submits no base version when creating a brand-new skill", async () => {
    const user = userEvent.setup();
    render(<SkillContributeDialog onContributionCreated={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: /Contribute/i }));
    await user.type(await screen.findByRole("textbox"), "brand new skill");
    await user.click(screen.getByRole("button", { name: /Start Creating/i }));

    await waitFor(() => expect(createdBody()).toBeTruthy());
    expect(createdBody().base_version).toBeNull();
    expect(createdBody().skill_id).toBeNull();
  });
});
