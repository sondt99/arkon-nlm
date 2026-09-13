"use client";

import React from "react";
import Link from "next/link";
import { api, apiWithMeta } from "@/lib/api";
import { WikiPageSummary, WikiTreeItem, WikiStats } from "@/types/wiki";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import { WikiPageTree } from "@/components/wiki/wiki-page-tree";
import { WikiContent } from "@/components/wiki/wiki-content";
import { WikiTypeBadge, wikiTypeGroupLabel } from "@/components/wiki/wiki-type-badge";
import { ScopeBadge } from "@/components/shared/scope-badge";
import { WikiSearchDialog } from "@/components/wiki/wiki-search-dialog";
import { EmptyState } from "@/components/shared/empty-state";
import { Pagination } from "@/components/ui/pagination";
import { Skeleton } from "@/components/ui/skeleton";

const TYPE_TABS = ["all", "entity", "concept", "topic", "source", "synthesis"] as const;
const GRID_PAGE_SIZE = 60;

export default function WikiIndexPage() {
  // Nav tree (slim) + facet stats — the only two things fetched on mount.
  const [treePages, setTreePages] = React.useState<WikiTreeItem[]>([]);
  const [stats, setStats] = React.useState<WikiStats | null>(null);
  const [loading, setLoading] = React.useState(true);

  const [searchOpen, setSearchOpen] = React.useState(false);
  const [activeTab, setActiveTab] = React.useState<(typeof TYPE_TABS)[number]>("all");

  // Grid — paginated server-side per tab.
  const [gridPages, setGridPages] = React.useState<WikiPageSummary[]>([]);
  const [gridTotal, setGridTotal] = React.useState(0);
  const [gridPage, setGridPage] = React.useState(1);
  const [gridLoading, setGridLoading] = React.useState(true);

  // Index overview — large compiled markdown, loaded on demand only.
  const [indexOpen, setIndexOpen] = React.useState(false);
  const [indexMd, setIndexMd] = React.useState<string | null>(null);
  const [indexLoading, setIndexLoading] = React.useState(false);
  const [shellError, setShellError] = React.useState<string | null>(null);

  const loadShell = React.useCallback(() => {
    setLoading(true);
    setShellError(null);
    Promise.all([
      api<WikiStats>("/api/wiki/stats"),
      api<WikiTreeItem[]>("/api/wiki/tree"),
    ])
      .then(([s, tree]) => {
        setStats(s);
        setTreePages(Array.isArray(tree) ? tree : []);
      })
      // `.catch(() => {})` left stats null, which made totalPages 0, which made isEmpty
      // true — so a 500 on /api/wiki/stats told every user "Wiki is empty. Upload and
      // compile documents to start building your knowledge wiki." about a populated
      // enterprise knowledge base, with no error and no retry. Promise.all rejects on the
      // FIRST failure, so either endpoint being down produced that.
      .catch((err: unknown) => {
        setShellError(
          err instanceof Error ? err.message : "Failed to load the wiki",
        );
        setStats(null);
        setTreePages([]);
      })
      .finally(() => setLoading(false));
  }, []);

  const loadGrid = React.useCallback((tab: (typeof TYPE_TABS)[number], page: number) => {
    setGridLoading(true);
    const params = new URLSearchParams({
      limit: String(GRID_PAGE_SIZE),
      offset: String((page - 1) * GRID_PAGE_SIZE),
    });
    if (tab !== "all") params.set("page_type", tab);
    apiWithMeta<WikiPageSummary[]>(`/api/wiki/pages?${params.toString()}`)
      .then(({ data, headers }) => {
        setGridPages(Array.isArray(data) ? data : []);
        const total = Number(headers.get("X-Total-Count") ?? "0");
        setGridTotal(Number.isFinite(total) ? total : 0);
      })
      .catch(() => {
        setGridPages([]);
        setGridTotal(0);
      })
      .finally(() => setGridLoading(false));
  }, []);

  React.useEffect(() => {
    loadShell();
    const handler = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setSearchOpen(true);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [loadShell]);

  // (Re)load the grid whenever the tab or page changes.
  React.useEffect(() => {
    loadGrid(activeTab, gridPage);
  }, [activeTab, gridPage, loadGrid]);

  const toggleIndex = React.useCallback(() => {
    setIndexOpen((open) => {
      const next = !open;
      if (next && indexMd === null && !indexLoading) {
        setIndexLoading(true);
        api<{ content_md: string }>("/api/wiki/index")
          .then((idx) => setIndexMd(idx.content_md || ""))
          // Leave it null on failure. Setting "" reported "No compiled index available
          // yet" — a factual claim about the wiki — and because the refetch guard below
          // is `indexMd === null`, one transient error made that permanent for the rest
          // of the session.
          .catch(() => setIndexMd(null))
          .finally(() => setIndexLoading(false));
      }
      return next;
    });
  }, [indexMd, indexLoading]);

  const reloadAll = React.useCallback(() => {
    loadShell();
    loadGrid(activeTab, gridPage);
  }, [loadShell, loadGrid, activeTab, gridPage]);

  const totalPages = stats?.total ?? 0;
  const typeCounts = stats?.by_type ?? {};
  const lastUpdated = stats?.last_updated ?? null;
  // Only "loaded successfully and there is genuinely nothing" counts as empty.
  const isEmpty = !loading && !shellError && totalPages === 0;

  const gridPageCount = Math.max(1, Math.ceil(gridTotal / GRID_PAGE_SIZE));

  return (
    <>
      <PageHeader
        title="Knowledge Wiki"
        description="Compiled knowledge from your organization's documents."
        action={
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={() => setSearchOpen(true)} className="gap-2">
              <span className="material-symbols-outlined text-base" aria-hidden="true">search</span>
              Search
              <kbd className="hidden sm:inline-block ml-1 px-1.5 py-0.5 rounded border border-border text-xs font-mono text-muted-foreground">
                ⌘K
              </kbd>
            </Button>
            <Link
              href="/wiki/graph"
              className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors"
            >
              <span className="material-symbols-outlined text-base" aria-hidden="true">hub</span>
              Graph View
            </Link>
          </div>
        }
      />

      <div className="flex-1 flex gap-0 -mx-6 md:-mx-8 lg:-mx-10 -mb-6 md:-mb-8 lg:-mb-10 min-h-0 border-t border-border">
        {/* Page Tree */}
        <WikiPageTree pages={treePages} loading={loading} onDeleted={reloadAll} />

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-8 py-6">
          {shellError ? (
            <EmptyState
              icon="error"
              title="Couldn't load the wiki"
              description={shellError}
              action={
                <Button variant="outline" onClick={reloadAll}>
                  Try again
                </Button>
              }
            />
          ) : isEmpty ? (
            <EmptyState
              icon="auto_stories"
              title="Wiki is empty"
              description="Upload and compile documents to start building your knowledge wiki."
            />
          ) : (
            <>
              {/* Stats bar */}
              <div className="flex flex-wrap items-center gap-3 mb-8">
                {loading ? (
                  Array.from({ length: 5 }).map((_, i) => (
                    <Skeleton key={i} className="h-11 w-28 rounded-xl" />
                  ))
                ) : (
                  <>
                    <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-4 py-2.5 shadow-sahara">
                      <span className="material-symbols-outlined text-base text-primary" aria-hidden="true">article</span>
                      <span className="text-sm font-semibold text-foreground tabular-nums">{totalPages}</span>
                      <span className="text-xs text-muted-foreground">Pages</span>
                    </div>
                    {Object.entries(typeCounts).sort((a, b) => b[1] - a[1]).map(([type, count]) => (
                      <div key={type} className="flex items-center gap-1.5 bg-card border border-border rounded-xl px-3 py-2.5 shadow-sahara">
                        <WikiTypeBadge type={type} />
                        <span className="text-xs text-muted-foreground tabular-nums">{count}</span>
                      </div>
                    ))}
                    {lastUpdated && (
                      <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-4 py-2.5 shadow-sahara ml-auto">
                        <span className="material-symbols-outlined text-base text-muted-foreground" aria-hidden="true">schedule</span>
                        <span className="text-xs text-muted-foreground">
                          Updated {new Date(lastUpdated).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                        </span>
                      </div>
                    )}
                  </>
                )}
              </div>

              {/* Index overview — large compiled markdown, collapsed by default so it
                  stays off the initial load; fetched the first time it's opened. */}
              <div className="mb-8 rounded-xl border border-border bg-card/50 overflow-hidden">
                <button
                  onClick={toggleIndex}
                  aria-expanded={indexOpen}
                  className="flex w-full items-center gap-2 px-4 py-3 text-left transition-colors hover:bg-muted/40"
                >
                  <span className={`material-symbols-outlined text-base text-muted-foreground transition-transform ${indexOpen ? "rotate-90" : ""}`} aria-hidden="true">
                    chevron_right
                  </span>
                  <span className="material-symbols-outlined text-base text-primary" aria-hidden="true">auto_stories</span>
                  <span className="text-sm font-medium text-foreground">Wiki Index overview</span>
                  <span className="ml-auto text-xs text-muted-foreground">
                    {indexOpen ? "Hide" : "Show compiled overview"}
                  </span>
                </button>
                {indexOpen && (
                  <div className="border-t border-border px-6 py-5">
                    {indexLoading ? (
                      <div className="space-y-2">
                        {Array.from({ length: 6 }).map((_, i) => (
                          <Skeleton key={i} className="h-4" style={{ width: `${90 - i * 8}%` }} />
                        ))}
                      </div>
                    ) : indexMd ? (
                      <WikiContent markdown={indexMd} />
                    ) : (
                      <p className="text-sm text-muted-foreground">No compiled index available yet.</p>
                    )}
                  </div>
                )}
              </div>

              {/* Pages grid — server-paginated per tab */}
              <div>
                {/* Type tabs */}
                <div className="flex items-center gap-1 mb-5 border-b border-border overflow-x-auto">
                  {TYPE_TABS.map((tab) => {
                    const count = tab === "all" ? totalPages : typeCounts[tab] ?? 0;
                    if (tab !== "all" && count === 0) return null;
                    return (
                      <button
                        key={tab}
                        onClick={() => { setActiveTab(tab); setGridPage(1); }}
                        className={`shrink-0 px-3 py-2 text-xs font-medium capitalize border-b-2 transition-colors ${
                          activeTab === tab
                            ? "border-primary text-primary"
                            : "border-transparent text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {tab === "all" ? "All" : wikiTypeGroupLabel(tab)}
                        <span className="ml-1.5 tabular-nums text-muted-foreground">{count}</span>
                      </button>
                    );
                  })}
                </div>

                {gridLoading ? (
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                    {Array.from({ length: 9 }).map((_, i) => (
                      <Skeleton key={i} className="h-32 rounded-xl" />
                    ))}
                  </div>
                ) : gridPages.length === 0 ? (
                  <p className="text-sm text-muted-foreground py-10 text-center">No pages in this category.</p>
                ) : (
                  <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                    {gridPages.map((page) => (
                      <Link
                        key={page.slug}
                        href={`/wiki/${page.slug}`}
                        className="group block bg-card border border-border rounded-xl p-4 hover:border-primary/40 hover:shadow-sahara transition-all"
                      >
                        <div className="flex items-start justify-between gap-2 mb-2">
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <WikiTypeBadge type={page.page_type} />
                            {page.scope_type && page.scope_type !== "global" && (
                              <ScopeBadge scopeType={page.scope_type} scopeId={page.scope_id} />
                            )}
                          </div>
                          <span className="text-xs text-muted-foreground shrink-0">v{page.version}</span>
                        </div>
                        <h3 className="font-heading text-base font-normal text-foreground group-hover:text-primary transition-colors mb-1">
                          {page.title}
                        </h3>
                        {page.summary && (
                          <p className="text-xs text-muted-foreground line-clamp-2">{page.summary}</p>
                        )}
                        <p className="text-xs text-muted-foreground mt-3">
                          {new Date(page.updated_at).toLocaleDateString()}
                        </p>
                      </Link>
                    ))}
                  </div>
                )}

                <Pagination
                  page={gridPage}
                  totalPages={gridPageCount}
                  onPageChange={setGridPage}
                  className="mt-6"
                />
              </div>
            </>
          )}
        </div>
      </div>

      <WikiSearchDialog open={searchOpen} onOpenChange={setSearchOpen} />
    </>
  );
}
