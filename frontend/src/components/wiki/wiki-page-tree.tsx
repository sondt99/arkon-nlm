"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";
import { api } from "@/lib/api";
import { useDebounce } from "@/lib/hooks/use-debounce";
import { WikiTreeItem } from "@/types/wiki";
import { wikiTypeIcon, wikiTypeColor, wikiTypeGroupLabel } from "./wiki-type-badge";

const GROUP_ORDER = ["entity", "concept", "topic", "source", "synthesis"];
const HEADER_ROW_HEIGHT = 34;
const ITEM_ROW_HEIGHT = 30;

type FlatRow =
  | { kind: "header"; type: string; count: number; isExpanded: boolean }
  | { kind: "item"; page: WikiTreeItem };

export function WikiPageTree({
  activeSlug,
  onDeleted,
  pages: pagesProp,
  loading: loadingProp,
  pagesUrl,
  linkQueryParams,
  onPageSelect,
}: {
  activeSlug?: string;
  onDeleted?: () => void;
  /** Pre-fetched pages — when provided, the tree skips its own fetch entirely. */
  pages?: WikiTreeItem[];
  /** Loading flag for the pre-fetched `pages` mode. Ignored in self-fetch mode. */
  loading?: boolean;
  /** Override the API URL to load pages from (default: /api/wiki/tree — the slim
   * nav list). Ignored when `pages` is provided. */
  pagesUrl?: string;
  /** Query params to append to page links (e.g. "?scopeType=project&scopeId=xxx") */
  linkQueryParams?: string;
  /** If provided, clicks call this instead of navigating via Link */
  onPageSelect?: (slug: string) => void;
}) {
  const pathname = usePathname();
  const usingExternalPages = pagesProp !== undefined;
  const [internalPages, setInternalPages] = React.useState<WikiTreeItem[]>([]);
  const [internalLoading, setInternalLoading] = React.useState(!usingExternalPages);
  const pages = usingExternalPages ? pagesProp! : internalPages;
  const loading = usingExternalPages ? (loadingProp ?? false) : internalLoading;

  const [search, setSearch] = React.useState("");
  const [collapsed, setCollapsed] = React.useState(false);
  const treeRef = React.useRef<HTMLDivElement>(null);
  const [expandedGroups, setExpandedGroups] = React.useState<Set<string>>(() => {
    // Explicit rather than relying on the catch below to absorb a ReferenceError. This does not
    // cause a hydration mismatch: the tree only ever mounts under `(portal)/layout.tsx`, which
    // renders its skeleton until `useAuth().loading` clears in an effect, so the tree does not
    // exist in the server HTML. The guard keeps that from being load-bearing, and keeps a
    // corrupt stored value from being indistinguishable from an unavailable store.
    if (typeof window === "undefined") return new Set(GROUP_ORDER);
    try {
      const saved = localStorage.getItem("wiki-tree-expanded-groups");
      if (saved) {
        const arr = JSON.parse(saved);
        if (Array.isArray(arr)) return new Set(arr);
      }
    } catch {}
    return new Set(GROUP_ORDER);
  });
  // Two-stage delete
  const [armedSlug, setArmedSlug] = React.useState<string | null>(null);
  const [deletingSlug, setDeletingSlug] = React.useState<string | null>(null);

  const expandedGroupsRef = React.useRef(expandedGroups);
  expandedGroupsRef.current = expandedGroups;

  const currentSlug = activeSlug ?? pathname.replace(/^\/wiki\//, "");

  const debouncedSearch = useDebounce(search, 150);

  const loadPages = React.useCallback(() => {
    if (usingExternalPages) return;
    setInternalLoading(true);
    const url = pagesUrl || "/api/wiki/tree";
    api<WikiTreeItem[]>(url)
      .then((data) => setInternalPages(Array.isArray(data) ? data : []))
      .catch(() => setInternalPages([]))
      .finally(() => setInternalLoading(false));
  }, [pagesUrl, usingExternalPages]);

  React.useEffect(() => {
    loadPages();
  }, [loadPages]);

  const filtered = React.useMemo(() => {
    if (!debouncedSearch) return pages;
    const q = debouncedSearch.toLowerCase();
    return pages.filter(
      (p) =>
        p.title.toLowerCase().includes(q) ||
        p.slug.toLowerCase().includes(q) ||
        (p.summary?.toLowerCase().includes(q) ?? false)
    );
  }, [pages, debouncedSearch]);

  const grouped = React.useMemo(() => {
    const map = new Map<string, WikiTreeItem[]>();
    for (const p of filtered) {
      const t = p.page_type;
      if (t === "index" || t === "log") continue;
      if (!map.has(t)) map.set(t, []);
      map.get(t)!.push(p);
    }
    return map;
  }, [filtered]);

  const totalCount = filtered.filter(
    (p) => p.page_type !== "index" && p.page_type !== "log"
  ).length;

  const flatRows: FlatRow[] = React.useMemo(() => {
    const rows: FlatRow[] = [];
    for (const type of GROUP_ORDER) {
      const items = grouped.get(type);
      if (!items) continue;
      const isExpanded = expandedGroups.has(type);
      rows.push({ kind: "header", type, count: items.length, isExpanded });
      if (isExpanded) for (const page of items) rows.push({ kind: "item", page });
    }
    return rows;
  }, [grouped, expandedGroups]);

  const virtualizer = useVirtualizer({
    count: flatRows.length,
    getScrollElement: () => treeRef.current,
    estimateSize: (i) => (flatRows[i].kind === "header" ? HEADER_ROW_HEIGHT : ITEM_ROW_HEIGHT),
    overscan: 12,
  });

  // Auto-expand group and scroll active item into view when slug or pages change
  React.useEffect(() => {
    if (!currentSlug || !pages.length) return;
    const activePage = pages.find((p) => p.slug === currentSlug);
    if (!activePage || activePage.page_type === "index" || activePage.page_type === "log") return;

    if (!expandedGroupsRef.current.has(activePage.page_type)) {
      setExpandedGroups((prev) => {
        const next = new Set(prev);
        next.add(activePage.page_type);
        try {
          localStorage.setItem("wiki-tree-expanded-groups", JSON.stringify([...next]));
        } catch {}
        return next;
      });
      // Group just expanded — let flatRows recompute before scrolling.
      return;
    }

    const index = flatRows.findIndex(
      (r) => r.kind === "item" && r.page.slug === currentSlug
    );
    if (index >= 0) {
      requestAnimationFrame(() => {
        virtualizer.scrollToIndex(index, { align: "center" });
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentSlug, pages, flatRows]);

  const handleDelete = async (slug: string) => {
    // First click: arm; second click: execute
    if (armedSlug !== slug) {
      setArmedSlug(slug);
      return;
    }
    setArmedSlug(null);
    setDeletingSlug(slug);
    try {
      await api(`/api/wiki/pages/${encodeURIComponent(slug)}`, { method: "DELETE" });
      if (!usingExternalPages) loadPages();
      onDeleted?.();
    } catch (err) {
      console.error("Delete failed:", err);
    } finally {
      setDeletingSlug(null);
    }
  };

  // Click outside armed row → disarm
  React.useEffect(() => {
    if (!armedSlug) return;
    const handler = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(`[data-slug="${armedSlug}"]`)) {
        setArmedSlug(null);
      }
    };
    document.addEventListener("click", handler, true);
    return () => document.removeEventListener("click", handler, true);
  }, [armedSlug]);

  const toggleGroup = (type: string) =>
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      next.has(type) ? next.delete(type) : next.add(type);
      try {
        localStorage.setItem("wiki-tree-expanded-groups", JSON.stringify([...next]));
      } catch {}
      return next;
    });

  if (collapsed) {
    return (
      <div className="w-10 border-r border-border bg-card/30 flex flex-col items-center pt-4 gap-3 shrink-0">
        <button
          onClick={() => setCollapsed(false)}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Expand page tree"
        >
          <span className="material-symbols-outlined text-base">chevron_right</span>
        </button>
      </div>
    );
  }

  return (
    <div className="w-64 shrink-0 border-r border-border bg-card/30 flex flex-col overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-border">
        <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex-1">
          Pages
        </span>
        <span className="text-xs text-muted-foreground tabular-nums bg-muted rounded-md px-1.5 py-0.5">
          {totalCount}
        </span>
        <button
          onClick={() => setCollapsed(true)}
          className="text-muted-foreground hover:text-foreground transition-colors"
          title="Collapse"
        >
          <span className="material-symbols-outlined text-base">chevron_left</span>
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2 border-b border-border">
        <div className="flex items-center gap-2 bg-background border border-border rounded-lg px-2.5 py-1.5">
          <span className="material-symbols-outlined text-sm text-muted-foreground">
            search
          </span>
          <input
            type="text"
            placeholder="Filter pages..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="flex-1 text-xs bg-transparent outline-none text-foreground placeholder:text-muted-foreground"
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="text-muted-foreground hover:text-foreground"
            >
              <span className="material-symbols-outlined text-sm">close</span>
            </button>
          )}
        </div>
      </div>

      {/* Tree */}
      <div ref={treeRef} className="flex-1 overflow-y-auto py-2">
        {loading ? (
          <div className="px-3 space-y-2 mt-1">
            {Array.from({ length: 6 }).map((_, i) => (
              <div
                key={i}
                className="h-7 rounded-md bg-muted animate-pulse"
                style={{ opacity: 1 - i * 0.12 }}
              />
            ))}
          </div>
        ) : flatRows.length === 0 ? (
          <p className="text-xs text-muted-foreground px-4 py-3">No pages found.</p>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((virtualRow) => {
              const row = flatRows[virtualRow.index];
              const rowStyle: React.CSSProperties = {
                position: "absolute",
                top: 0,
                left: 0,
                width: "100%",
                height: virtualRow.size,
                transform: `translateY(${virtualRow.start}px)`,
              };

              if (row.kind === "header") {
                return (
                  <div key={`header-${row.type}`} style={rowStyle} className="mb-1">
                    <button
                      onClick={() => toggleGroup(row.type)}
                      className="w-full h-full flex items-center gap-2 px-3 hover:bg-accent/40 transition-colors"
                    >
                      <span className="material-symbols-outlined text-xs text-muted-foreground">
                        {row.isExpanded ? "expand_more" : "chevron_right"}
                      </span>
                      <span
                        className="material-symbols-outlined text-xs"
                        style={{ color: wikiTypeColor(row.type), fontSize: 13 }}
                      >
                        {wikiTypeIcon(row.type)}
                      </span>
                      <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide flex-1 text-left">
                        {wikiTypeGroupLabel(row.type)}
                      </span>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {row.count}
                      </span>
                    </button>
                  </div>
                );
              }

              const page = row.page;
              const isActive = page.slug === currentSlug;
              const isArmed = armedSlug === page.slug;
              const isDeleting = deletingSlug === page.slug;
              return (
                <div key={page.slug} style={rowStyle} className="ml-3 pr-1">
                  <div
                    data-slug={page.slug}
                    className={cn(
                      "group h-full flex items-center gap-1 rounded-lg mx-1 transition-all",
                      isActive ? "bg-primary/10" : "hover:bg-accent/50"
                    )}
                  >
                    {onPageSelect ? (
                      <button
                        onClick={() => onPageSelect(page.slug)}
                        className={cn(
                          "flex-1 flex items-center gap-2 px-2 py-1.5 text-xs min-w-0 transition-all text-left",
                          isActive
                            ? "text-primary font-medium"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        title={page.summary || page.title}
                      >
                        <span className="truncate">{page.title}</span>
                      </button>
                    ) : (
                      <Link
                        href={`/wiki/${page.slug}${linkQueryParams || ""}`}
                        className={cn(
                          "flex-1 flex items-center gap-2 px-2 py-1.5 text-xs min-w-0 transition-all",
                          isActive
                            ? "text-primary font-medium"
                            : "text-muted-foreground hover:text-foreground"
                        )}
                        title={page.summary || page.title}
                      >
                        <span className="truncate">{page.title}</span>
                      </Link>
                    )}

                    {/* Delete button — 2-stage */}
                    {isDeleting ? (
                      <span className="material-symbols-outlined text-xs text-destructive animate-pulse mr-1.5">
                        progress_activity
                      </span>
                    ) : isArmed ? (
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(page.slug); }}
                        className="shrink-0 mr-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-destructive text-destructive-foreground hover:bg-destructive/90 animate-pulse transition-colors"
                        title={`Click again to confirm delete "${page.title}"`}
                      >
                        Confirm
                      </button>
                    ) : (
                      <button
                        onClick={(e) => { e.preventDefault(); e.stopPropagation(); handleDelete(page.slug); }}
                        className="shrink-0 mr-1 opacity-0 group-hover:opacity-100 text-muted-foreground hover:text-destructive transition-all"
                        title={`Delete "${page.title}"`}
                      >
                        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>delete</span>
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
