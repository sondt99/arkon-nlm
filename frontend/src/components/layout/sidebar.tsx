"use client";

import React, { useEffect, useState } from "react";
import Link from "next/link";
import Image from "next/image";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api";
import { ThemeToggle } from "@/components/layout/theme-toggle";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

/* ─── Types ─── */

type NavItem = {
  label: string;
  href: string;
  icon: string;
  requiredPermissions?: string[];
};

type NavSection = {
  id: string;
  label: string;
  requiredPermissions?: string[];
  items: NavItem[];
};

type WorkspaceItem = {
  id: string;
  name: string;
  workspace_type: string;
  status: string;
};

/* ─── Navigation Config ─── */

const navSections: NavSection[] = [
  {
    id: "org-knowledge",
    label: "Org Knowledge",
    items: [
      { label: "Documents", href: "/knowledge", icon: "description", requiredPermissions: ["doc:read:own_dept", "doc:read:all"] },
      { label: "Wiki", href: "/wiki", icon: "auto_stories", requiredPermissions: ["wiki:read:own_dept", "wiki:read:all"] },
      { label: "Chatbot", href: "/chat", icon: "smart_toy" },
      { label: "AI Skills", href: "/skills", icon: "bolt", requiredPermissions: ["skill:read:own_dept", "skill:read:all"] },
      { label: "NotebookLM", href: "/notebooklm", icon: "book_2" },
    ],
  },
  {
    id: "organization",
    label: "Organization",
    requiredPermissions: ["org:departments:read", "org:employees:read", "org:roles:read"],
    items: [
      { label: "Departments", href: "/departments", icon: "domain", requiredPermissions: ["org:departments:read"] },
      { label: "Employees", href: "/employees", icon: "group", requiredPermissions: ["org:employees:read"] },
      { label: "Roles", href: "/roles", icon: "manage_accounts", requiredPermissions: ["org:roles:read"] },
    ],
  },
  {
    id: "system",
    label: "System",
    requiredPermissions: ["org:audit:read", "org:settings:read"],
    items: [
      { label: "Audit Log", href: "/audit", icon: "policy", requiredPermissions: ["org:audit:read"] },
      { label: "Settings", href: "/settings", icon: "settings", requiredPermissions: ["org:settings:read"] },
    ],
  },
];

/* ─── Hooks ─── */

function useGroupToggle(groupId: string, defaultOpen: boolean) {
  const key = `sidebar-group-${groupId}`;
  const [open, setOpen] = React.useState(() => {
    if (typeof window === "undefined") return defaultOpen;
    const stored = localStorage.getItem(key);
    return stored === null ? defaultOpen : stored === "true";
  });

  const toggle = () =>
    setOpen((v) => {
      const next = !v;
      localStorage.setItem(key, String(next));
      return next;
    });

  return [open, toggle] as const;
}

function useSidebarCollapse() {
  const key = "sidebar-collapsed";
  const [collapsed, setCollapsed] = React.useState(() => {
    if (typeof window === "undefined") return false;
    return localStorage.getItem(key) === "true";
  });

  const toggle = () =>
    setCollapsed((v) => {
      const next = !v;
      localStorage.setItem(key, String(next));
      return next;
    });

  return [collapsed, toggle] as const;
}

/* ─── Helpers ─── */

function isActive(href: string, pathname: string) {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

/** Pick a color for workspace icon based on workspace type */
function workspaceColor(type: string): string {
  const colors: Record<string, string> = {
    internal: "#c2652a",
    customer: "#2a7ec2",
    partner: "#2ac265",
  };
  return colors[type] || "#78706a";
}

/* ─── Sub-components ─── */

function SidebarNavItem({
  item,
  pathname,
  indented = false,
}: {
  item: NavItem;
  pathname: string;
  indented?: boolean;
}) {
  const active = isActive(item.href, pathname);

  return (
    <Link
      href={item.href}
      className={cn(
        "group relative flex items-center gap-2 rounded-md px-2 py-[5px] text-[13px] transition-colors duration-100",
        indented && "ml-3",
        active
          ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
          : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
      )}
    >
      <span
        className={cn(
          "material-symbols-outlined text-[18px] shrink-0",
          active ? "filled text-foreground" : "text-muted-foreground/70 group-hover:text-muted-foreground"
        )}
        style={{ fontVariationSettings: active ? "'FILL' 1, 'wght' 300, 'GRAD' 0, 'opsz' 20" : "'FILL' 0, 'wght' 300, 'GRAD' 0, 'opsz' 20" }}
      >
        {item.icon}
      </span>
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

/** Static section — always expanded, no toggle */
function SidebarStaticSection({
  section,
  hasPermission,
  pathname,
}: {
  section: NavSection;
  hasPermission: (perm: string) => boolean;
  pathname: string;
}) {
  const visibleItems = section.items.filter((i) => {
    if (!i.requiredPermissions) return true;
    return i.requiredPermissions.some((p) => hasPermission(p));
  });
  if (visibleItems.length === 0) return null;

  return (
    <div className="mt-4 first:mt-0">
      {/* Section label */}
      <div className="px-2 py-[3px] text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">
        {section.label}
      </div>

      {/* Items — always visible */}
      <div className="mt-[2px] space-y-[1px]">
        {visibleItems.map((item) => (
          <SidebarNavItem key={item.href} item={item} pathname={pathname} indented />
        ))}
      </div>
    </div>
  );
}

/** Workspaces section — collapsible, fetches workspace list */
function SidebarWorkspacesSection({
  pathname,
  canCreate,
}: {
  pathname: string;
  canCreate: boolean;
}) {
  const [workspaces, setWorkspaces] = useState<WorkspaceItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [open, toggle] = useGroupToggle("workspaces", true);

  useEffect(() => {
    const fetchWS = () => {
      api<WorkspaceItem[]>("/api/projects")
        .then((data) => setWorkspaces(data))
        .catch(() => setWorkspaces([]))
        .finally(() => setLoaded(true));
    };

    fetchWS();

    window.addEventListener("workspaces-changed", fetchWS);
    return () => window.removeEventListener("workspaces-changed", fetchWS);
    // Mount-only: `pathname` isn't read above, and the "workspaces-changed"
    // event (dispatched whenever membership actually changes) already
    // covers the case a per-navigation refetch was papering over.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const SIDEBAR_LIMIT = 10;
  const hasActiveChild = workspaces.some((w) =>
    pathname.startsWith(`/workspaces`) && pathname.includes(w.id)
  );
  const isOpen = open || hasActiveChild;
  const displayedWorkspaces = workspaces.slice(0, SIDEBAR_LIMIT);
  const hasMore = workspaces.length > SIDEBAR_LIMIT;
  const itemCount = displayedWorkspaces.length + (hasMore ? 1 : 0);

  return (
    <div className="mt-4">
      {/* Section header — collapsible + create button */}
      <div className="group/ws flex items-center">
        <button
          onClick={toggle}
          className="flex flex-1 items-center gap-1 px-2 py-[3px] text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60 group-hover/ws:text-muted-foreground transition-colors duration-100"
        >
          <span>Workspaces</span>
          <span
            className="material-symbols-outlined text-[14px] transition-all duration-150 opacity-0 group-hover/ws:opacity-100"
            style={{
              transform: isOpen ? "rotate(0deg)" : "rotate(-90deg)",
              fontVariationSettings: "'FILL' 0, 'wght' 500, 'GRAD' 0, 'opsz' 14",
            }}
          >
            expand_more
          </span>
        </button>
        {canCreate && (
          <Link
            href="/?new=1"
            className="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground/40 hover:bg-sidebar-accent hover:text-primary transition-all duration-100 opacity-0 group-hover/ws:opacity-100 mr-1"
            title="New Workspace"
          >
            <span
              className="material-symbols-outlined text-[16px]"
              style={{ fontVariationSettings: "'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 16" }}
            >
              add
            </span>
          </Link>
        )}
      </div>

      {/* Workspace items */}
      <div
        className="overflow-hidden transition-all duration-200 ease-out"
        style={{
          maxHeight: isOpen ? `${Math.max(itemCount, 1) * 32 + 8}px` : "0px",
          opacity: isOpen ? 1 : 0,
        }}
      >
        <div className="mt-[2px] space-y-[1px]">
          {!loaded ? (
            <div className="ml-3 space-y-[3px] py-[2px]">
              {[52, 72, 44].map((w, i) => (
                <div key={i} className="flex items-center gap-2 px-2 py-[5px]">
                  <Skeleton className="w-2 h-2 rounded-sm shrink-0" />
                  <Skeleton style={{ height: 10, width: w }} />
                </div>
              ))}
            </div>
          ) : workspaces.length === 0 ? (
            <div className="ml-3 px-2 py-[5px] text-[12px] text-muted-foreground/40">
              No workspaces
            </div>
          ) : (
            <>
              {displayedWorkspaces.map((ws) => {
                const href = `/workspaces/${ws.id}`;
                const active = pathname === href;

                return (
                  <Link
                    key={ws.id}
                    href={href}
                    className={cn(
                      "group relative flex items-center gap-2 rounded-md ml-3 px-2 py-[5px] text-[13px] transition-colors duration-100",
                      active
                        ? "bg-sidebar-accent font-semibold text-sidebar-accent-foreground"
                        : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"
                    )}
                  >
                    <span
                      className="w-[8px] h-[8px] rounded-[2px] shrink-0"
                      style={{ backgroundColor: workspaceColor(ws.workspace_type) }}
                    />
                    <span className="truncate">{ws.name}</span>
                  </Link>
                );
              })}
              {hasMore && (
                <Link
                  href="/"
                  className="flex items-center gap-2 ml-3 px-2 py-[5px] text-[12px] text-muted-foreground/50 hover:text-muted-foreground transition-colors duration-100"
                >
                  <span className="material-symbols-outlined text-[14px]">more_horiz</span>
                  <span>{workspaces.length - SIDEBAR_LIMIT} more…</span>
                </Link>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function OrgHeader({
  user,
}: {
  user: { name: string; role: string } | null;
}) {
  const router = useRouter();
  const { logout } = useAuth();

  const handleLogout = () => {
    logout();
    router.push("/login");
  };

  return (
    <div className="px-2 py-1 mb-1">
      <DropdownMenu>
        <DropdownMenuTrigger className="flex items-center gap-2.5 rounded-md px-1.5 py-1.5 hover:bg-sidebar-accent/60 transition-colors cursor-pointer min-w-0 w-full">
          <Image
            src="/arkon-icon-v2.png"
            alt="Arkon"
            width={24}
            height={24}
            className="shrink-0 rounded-[4px]"
          />
          <div className="flex flex-col items-start min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-[15px] font-semibold text-primary truncate leading-tight font-heading">Arkon</span>
              <span className="rounded border border-primary/20 bg-primary/10 px-1 py-0.5 font-mono text-[8px] font-semibold uppercase leading-none text-primary">v2</span>
            </div>
            {user && (
              <span className="text-[10px] text-muted-foreground/70 truncate leading-tight">
                {user.name} · {user.role}
              </span>
            )}
          </div>
          <span className="material-symbols-outlined text-[14px] text-muted-foreground/50 ml-auto shrink-0">
            arrow_drop_down
          </span>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="w-56">
          {user && (
            <>
              <div className="px-3 py-2">
                <p className="text-sm font-medium">{user.name}</p>
                <p className="text-xs text-muted-foreground capitalize">{user.role}</p>
              </div>
              <DropdownMenuSeparator />
            </>
          )}
          <DropdownMenuItem onClick={() => router.push("/profile")}>
            <span className="material-symbols-outlined mr-2 text-base">person</span>
            Profile
          </DropdownMenuItem>
          <DropdownMenuItem onClick={handleLogout} className="text-destructive">
            <span className="material-symbols-outlined mr-2 text-base">logout</span>
            Sign out
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

/* ─── Main Sidebar ─── */

export function Sidebar({ className }: { className?: string } = {}) {
  const pathname = usePathname();
  const { user, hasPermission } = useAuth();
  const [collapsed, toggleCollapsed] = useSidebarCollapse();

  const visibleSections = navSections.filter((s) => {
    if (!s.requiredPermissions) return true;
    return s.requiredPermissions.some((p) => hasPermission(p));
  });

  if (collapsed) {
    const allItems = [
      { label: "Dashboard", href: "/", icon: "dashboard" },
      ...visibleSections.flatMap((s) =>
        s.items.filter((i) => !i.requiredPermissions || i.requiredPermissions.some((p) => hasPermission(p)))
      ),
    ];

    return (
      <nav className={cn("flex flex-col h-full w-14 shrink-0 items-center bg-sidebar/90 border-r border-sidebar-border backdrop-blur-xl py-2 gap-1", className ?? "hidden md:flex")}>
        <button
          onClick={toggleCollapsed}
          className="flex items-center justify-center w-9 h-9 rounded-md text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground transition-colors"
          title="Expand sidebar"
        >
          <Image src="/arkon-icon-v2.png" alt="Arkon" width={20} height={20} className="rounded-[4px]" />
        </button>
        <div className="w-6 border-t border-sidebar-border my-1" />
        <div className="flex-1 overflow-y-auto overflow-x-hidden flex flex-col items-center gap-[2px] sidebar-scrollbar">
          {allItems.map((item) => {
            const active = isActive(item.href, pathname);
            return (
              <Link
                key={item.href}
                href={item.href}
                title={item.label}
                className={cn(
                  "flex items-center justify-center w-9 h-9 rounded-md transition-colors duration-100",
                  active
                    ? "bg-sidebar-accent text-sidebar-accent-foreground"
                    : "text-muted-foreground/70 hover:bg-sidebar-accent/60 hover:text-foreground"
                )}
              >
                <span
                  className="material-symbols-outlined text-[18px]"
                  style={{ fontVariationSettings: active ? "'FILL' 1, 'wght' 300, 'GRAD' 0, 'opsz' 20" : "'FILL' 0, 'wght' 300, 'GRAD' 0, 'opsz' 20" }}
                >
                  {item.icon}
                </span>
              </Link>
            );
          })}
        </div>
      </nav>
    );
  }

  return (
    <nav className={cn("flex flex-col h-full w-[252px] shrink-0 bg-sidebar/90 border-r border-sidebar-border backdrop-blur-xl", className ?? "hidden md:flex")}>
      {/* Org Header + User */}
      <div className="pt-2 flex items-center gap-1 pr-1">
        <div className="flex-1 min-w-0">
          <OrgHeader user={user} />
        </div>
        <button
          onClick={toggleCollapsed}
          className="shrink-0 flex items-center justify-center w-7 h-7 rounded-md text-muted-foreground/50 hover:bg-sidebar-accent hover:text-foreground transition-colors"
          title="Collapse sidebar"
        >
          <span className="material-symbols-outlined text-[16px]">chevron_left</span>
        </button>
      </div>

      {/* Divider */}
      <div className="mx-3 border-t border-sidebar-border my-1" />

      {/* Navigation */}
      <div className="flex-1 overflow-y-auto overflow-x-hidden px-2 py-1 sidebar-scrollbar">
        {/* Dashboard */}
        <SidebarNavItem
          item={{ label: "Dashboard", href: "/", icon: "dashboard" }}
          pathname={pathname}
        />

        {/* Workspaces — collapsible, inline list */}
        <SidebarWorkspacesSection pathname={pathname} canCreate={hasPermission("workspace:view:all")} />

        {/* Static sections — no collapse */}
        {visibleSections.map((section) => (
          <SidebarStaticSection
            key={section.id}
            section={section}
            hasPermission={hasPermission}
            pathname={pathname}
          />
        ))}
      </div>

      {/* Bottom meta */}
      <div className="space-y-2 border-t border-sidebar-border px-3 py-3">
        <ThemeToggle className="w-full justify-center" />
        <span className="text-[10px] text-muted-foreground/40 font-medium">
          On-Premise · Internal
        </span>
      </div>
    </nav>
  );
}
