import React, { useState, useMemo } from "react";
import { EmptyState } from "@/components/shared/empty-state";
import { WikiTypeBadge, wikiTypeGroupLabel } from "@/components/wiki/wiki-type-badge";
import { WikiPageTree } from "@/components/wiki/wiki-page-tree";
import { WikiContent } from "@/components/wiki/wiki-content";
import { WikiSidebarRight } from "@/components/wiki/wiki-backlinks";
import { ScopeBadge } from "@/components/shared/scope-badge";
import { WikiPageDetail, WikiPageSummary } from "@/types/wiki";
import { Project } from "./types";
import { WIKI_TYPE_TABS } from "./utils";
import { WikiDetailInline } from "./wiki-detail-inline";
import { WikiGraphInline } from "./wiki-graph-inline";

type Props = {
  project: Project;
  wikiPages: WikiPageSummary[];
  wikiLoading: boolean;
  wikiIndexMd: string | null;
  onWikiChanged?: () => void;
};

const PAGE_SIZE = 24;

export function WikiTab({ project, wikiPages, wikiLoading, wikiIndexMd, onWikiChanged }: Props) {
  const [wikiTypeTab, setWikiTypeTab] = useState<string>("all");
  const [selectedWikiSlug, setSelectedWikiSlug] = useState<string | null>(null);
  const [selectedWikiPage, setSelectedWikiPage] = useState<WikiPageDetail | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [pageNum, setPageNum] = useState(1);

  // Compute wiki counts
  const wikiTypeCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    wikiPages.forEach((p) => {
      const type = p.page_type;
      counts[type] = (counts[type] || 0) + 1;
    });
    return counts;
  }, [wikiPages]);

  // Filter pages for display
  const displayWikiPages = useMemo(() => {
    if (wikiTypeTab === "all") return wikiPages;
    return wikiPages.filter((p) => p.page_type === wikiTypeTab);
  }, [wikiPages, wikiTypeTab]);

  const pageCount = Math.max(1, Math.ceil(displayWikiPages.length / PAGE_SIZE));
  const currentPage = Math.min(pageNum, pageCount);
  const pagedWikiPages = useMemo(
    () => displayWikiPages.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE),
    [displayWikiPages, currentPage]
  );

  if (showGraph) {
    return <WikiGraphInline projectId={project.id} onBack={() => setShowGraph(false)} />;
  }

  return (
    <div className="flex gap-0 -mx-6 md:-mx-8 -mb-6 md:-mb-8 flex-1 min-h-0 border-t border-border overflow-hidden">
      {/* Page Tree sidebar — scoped to workspace */}
      <WikiPageTree
        pages={wikiPages}
        loading={wikiLoading}
        onDeleted={onWikiChanged}
        activeSlug={selectedWikiSlug ?? undefined}
        onPageSelect={(slug) => {
          setSelectedWikiSlug(slug);
          setSelectedWikiPage(null);
        }}
      />

      {/* Content area */}
      <div className="flex-1 overflow-y-auto px-8 py-6 min-w-0">
        {selectedWikiSlug ? (
          /* ---- Inline wiki page detail view ---- */
          <WikiDetailInline
            slug={selectedWikiSlug}
            projectId={project.id}
            onBack={() => {
              setSelectedWikiSlug(null);
              setSelectedWikiPage(null);
            }}
            onPageLoaded={setSelectedWikiPage}
            onNavigate={(slug) => {
              setSelectedWikiSlug(slug);
              setSelectedWikiPage(null);
            }}
          />
        ) : (
          /* ---- Wiki pages list view ---- */
          <>
            {wikiLoading ? (
              <div className="flex items-center justify-center h-32">
                <span className="material-symbols-outlined text-3xl text-muted-foreground animate-spin">
                  progress_activity
                </span>
              </div>
            ) : wikiPages.length === 0 ? (
              <EmptyState
                icon="auto_stories"
                title="No wiki pages yet"
                description="Upload documents in this workspace to automatically compile knowledge into wiki pages."
              />
            ) : (
              <>
                {/* Stats bar + Graph View button on same row */}
                <div className="flex flex-wrap items-center gap-3 mb-8">
                  <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-4 py-2.5 shadow-sahara">
                    <span className="material-symbols-outlined text-base text-primary">
                      article
                    </span>
                    <span className="text-sm font-semibold text-foreground">
                      {wikiPages.length}
                    </span>
                    <span className="text-xs text-muted-foreground">Pages</span>
                  </div>
                  {Object.entries(wikiTypeCounts)
                    .sort((a, b) => b[1] - a[1])
                    .map(([type, count]) => (
                      <div
                        key={type}
                        className="flex items-center gap-1.5 bg-card border border-border rounded-xl px-3 py-2.5 shadow-sahara"
                      >
                        <WikiTypeBadge type={type} />
                        <span className="text-xs text-muted-foreground tabular-nums">
                          {count}
                        </span>
                      </div>
                    ))}
                  <div className="flex items-center gap-2 ml-auto">
                    {wikiPages[0]?.updated_at && (
                      <div className="flex items-center gap-2 bg-card border border-border rounded-xl px-4 py-2.5 shadow-sahara">
                        <span className="material-symbols-outlined text-base text-muted-foreground">
                          schedule
                        </span>
                        <span className="text-xs text-muted-foreground">
                          Updated{" "}
                          {new Date(wikiPages[0].updated_at).toLocaleDateString("en-US", {
                            month: "short",
                            day: "numeric",
                          })}
                        </span>
                      </div>
                    )}
                    <button
                      onClick={() => setShowGraph(true)}
                      className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl text-sm font-medium bg-primary text-primary-foreground hover:bg-primary/90 transition-colors shadow-sahara"
                    >
                      <span className="material-symbols-outlined text-base">hub</span>
                      Graph View
                    </button>
                  </div>
                </div>

                {/* Wiki Index content — mirrors /wiki page */}
                {wikiIndexMd && <WikiContent markdown={wikiIndexMd} />}

                {/* Type filter tabs */}
                <div className="flex items-center gap-1 mb-5 border-b border-border">
                  {WIKI_TYPE_TABS.map((wt) => {
                    const count = wt === "all" ? wikiPages.length : wikiTypeCounts[wt] ?? 0;
                    if (wt !== "all" && count === 0) return null;
                    return (
                      <button
                        key={wt}
                        onClick={() => { setWikiTypeTab(wt); setPageNum(1); }}
                        className={`px-3 py-2 text-xs font-medium capitalize border-b-2 transition-colors ${
                          wikiTypeTab === wt
                            ? "border-primary text-primary"
                            : "border-transparent text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {wt === "all" ? "All" : wikiTypeGroupLabel(wt)}
                        <span className="ml-1.5 tabular-nums text-muted-foreground">
                          {count}
                        </span>
                      </button>
                    );
                  })}
                </div>

                {/* Wiki page cards — click opens inline */}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
                  {pagedWikiPages.map((page) => (
                    <button
                      key={page.slug}
                      onClick={() => setSelectedWikiSlug(page.slug)}
                      className="group block bg-card border border-border rounded-xl p-4 hover:border-primary/40 hover:shadow-sahara transition-all text-left"
                    >
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <div className="flex items-center gap-1.5 flex-wrap">
                          <WikiTypeBadge type={page.page_type} />
                          <ScopeBadge scopeType="workspace" />
                        </div>
                        <span className="text-xs text-muted-foreground shrink-0">
                          v{page.version}
                        </span>
                      </div>
                      <h3 className="font-heading text-base font-normal text-foreground group-hover:text-primary transition-colors mb-1">
                        {page.title}
                      </h3>
                      {page.summary && (
                        <p className="text-xs text-muted-foreground line-clamp-2">
                          {page.summary}
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground mt-3">
                        {new Date(page.updated_at).toLocaleDateString()}
                      </p>
                    </button>
                  ))}
                </div>

                {/* Pagination */}
                {pageCount > 1 && (
                  <div className="flex items-center justify-center gap-3 mt-6">
                    <button
                      onClick={() => setPageNum((p) => Math.max(1, p - 1))}
                      disabled={currentPage === 1}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 disabled:opacity-40 disabled:pointer-events-none transition-colors"
                    >
                      <span className="material-symbols-outlined" style={{ fontSize: 14 }}>chevron_left</span>
                      Previous
                    </button>
                    <span className="text-xs text-muted-foreground tabular-nums">
                      Page {currentPage} / {pageCount}
                    </span>
                    <button
                      onClick={() => setPageNum((p) => Math.min(pageCount, p + 1))}
                      disabled={currentPage === pageCount}
                      className="inline-flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-medium border border-border text-muted-foreground hover:text-foreground hover:border-primary/40 disabled:opacity-40 disabled:pointer-events-none transition-colors"
                    >
                      Next
                      <span className="material-symbols-outlined" style={{ fontSize: 14 }}>chevron_right</span>
                    </button>
                  </div>
                )}
              </>
            )}
          </>
        )}
      </div>

      {/* Right sidebar — shown when viewing a page, mirrors standalone wiki */}
      {selectedWikiSlug && selectedWikiPage && (
        <div className="hidden lg:flex shrink-0 overflow-hidden">
          <WikiSidebarRight slug={selectedWikiSlug} page={selectedWikiPage} />
        </div>
      )}
    </div>
  );
}
