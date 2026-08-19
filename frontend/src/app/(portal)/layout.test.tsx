/**
 * #91 listed a hydration mismatch from reading `localStorage` in `useState` initializers
 * (`layout/sidebar.tsx`, `wiki/wiki-page-tree.tsx`, and the same hook in `chat/page.tsx`).
 *
 * It is not reachable, and this file is why. `PortalLayout` returns `<AppShellSkeleton />`
 * while `useAuth().loading` is true. `loading` starts `true` and only flips inside an effect,
 * after `/api/auth/me` answers — so the server render and the first client render both produce
 * the skeleton, and `<Sidebar />` (plus `children`, so the chat page and the wiki tree too)
 * never exists during hydration. Those initializers run on a fresh client-side mount, where
 * there is no server HTML to disagree with.
 *
 * That makes the invariant load-bearing rather than incidental: rendering the real shell while
 * auth is still resolving would make every one of those `localStorage` reads a genuine
 * hydration mismatch. These tests fail if anyone removes the gate.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import PortalLayout from "./layout";

const authState = { user: null as unknown, loading: true };

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({
    ...authState,
    hasPermission: () => true,
    canAccess: () => true,
    getWorkspaceRole: () => "admin",
  }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/skills",
}));

vi.mock("@/lib/api", () => ({ api: vi.fn().mockResolvedValue([]) }));

const CHILD = "child-page-content";

describe("portal shell gating (#91)", () => {
  it("renders the skeleton, not the sidebar, while auth is still resolving", () => {
    authState.loading = true;
    authState.user = null;

    render(<PortalLayout>{CHILD}</PortalLayout>);

    // No <nav> means no Sidebar, so `useSidebarCollapse` never runs during hydration.
    expect(document.querySelector("nav")).toBeNull();
    expect(screen.queryByText(CHILD)).not.toBeInTheDocument();
    expect(document.querySelectorAll(".arkon-skeleton").length).toBeGreaterThan(0);
  });

  it("renders nothing at all once auth resolves to no user", () => {
    authState.loading = false;
    authState.user = null;

    const { container } = render(<PortalLayout>{CHILD}</PortalLayout>);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders the sidebar and the page only once a user is present", () => {
    authState.loading = false;
    authState.user = { id: "u1", name: "Ada", email: "a@b.c", role: "admin", permissions: [] };

    render(<PortalLayout>{CHILD}</PortalLayout>);

    expect(document.querySelector("nav")).not.toBeNull();
    expect(screen.getByText(CHILD)).toBeInTheDocument();
  });
});
