/** Required test widths (px). Heights are chosen to roughly match real device aspect ratios. */
export const VIEWPORTS = [
  { width: 320, height: 568 },
  { width: 375, height: 667 },
  { width: 390, height: 844 },
  { width: 414, height: 896 },
  { width: 768, height: 1024 },
  { width: 1024, height: 768 },
  { width: 1440, height: 900 },
  { width: 1920, height: 1080 },
] as const;

/** Authenticated, static (non-dynamic-segment) routes. Dynamic-segment routes
 * (skills/[slug], wiki/[...slug], workspaces/[id]) are covered separately once
 * a seed id/slug is resolved at runtime — see e2e/dynamic-routes.spec.ts. */
export const STATIC_ROUTES = [
  "/",
  "/admin/skill-contributions",
  "/audit",
  "/chat",
  "/departments",
  "/employees",
  "/knowledge",
  "/notebooklm",
  "/profile",
  "/projects",
  "/roles",
  "/settings",
  "/skills",
  "/wiki",
  "/wiki/graph",
  "/workspaces",
] as const;
