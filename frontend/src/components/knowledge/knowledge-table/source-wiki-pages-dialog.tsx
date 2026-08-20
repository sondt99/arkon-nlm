"use client";

import React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Source } from "./types";

type WikiPageItem = {
  id: string;
  slug: string;
  title: string;
  page_type: string;
  summary: string;
  updated_at: string | null;
};

const PAGE_TYPE_COLORS: Record<string, string> = {
  entity: "bg-blue-500/10 text-blue-600 border-blue-500/30",
  concept: "bg-purple-500/10 text-purple-600 border-purple-500/30",
  topic: "bg-green-500/10 text-green-600 border-green-500/30",
  source: "bg-orange-500/10 text-orange-600 border-orange-500/30",
  index: "bg-gray-500/10 text-gray-600 border-gray-500/30",
};

export function SourceWikiPagesDialog({
  source,
  onClose,
}: {
  source: Source;
  onClose: () => void;
}) {
  const [pages, setPages] = React.useState<WikiPageItem[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    api<WikiPageItem[]>(`/api/sources/${source.id}/wiki-pages`)
      .then(setPages)
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load pages"))
      .finally(() => setLoading(false));
  }, [source.id]);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary" style={{ fontSize: 20 }}>
              auto_stories
            </span>
            Wiki from this document
          </DialogTitle>
          <p className="text-sm text-muted-foreground mt-1 truncate">
            {source.title}
          </p>
        </DialogHeader>

        <div className="flex-1 overflow-y-auto min-h-0 mt-2">
          {loading && (
            <div className="flex items-center justify-center py-12">
              <span className="material-symbols-outlined animate-spin text-muted-foreground text-3xl">
                progress_activity
              </span>
            </div>
          )}

          {error && (
            <div className="text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg">
              {error}
            </div>
          )}

          {!loading && !error && pages.length === 0 && (
            <div className="flex flex-col items-center justify-center py-12 text-center gap-2">
              <span className="material-symbols-outlined text-muted-foreground text-4xl">
                article
              </span>
              <p className="text-sm text-muted-foreground">
                This document has not created any wiki pages yet.
              </p>
            </div>
          )}

          {!loading && pages.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <p className="text-xs text-muted-foreground mb-2">
                {pages.length} wiki page{pages.length !== 1 ? "s" : ""}
              </p>
              {pages.map((page) => (
                <Link
                  key={page.id}
                  href={`/wiki/${page.slug}`}
                  onClick={onClose}
                  className="flex items-start gap-3 p-3 rounded-lg border border-border bg-card hover:bg-secondary/40 transition-colors group"
                >
                  <span
                    className="material-symbols-outlined text-muted-foreground shrink-0 mt-0.5 group-hover:text-primary transition-colors"
                    style={{ fontSize: 18 }}
                  >
                    {page.page_type === "source" ? "description" : "article"}
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium group-hover:text-primary transition-colors">
                        {page.title}
                      </span>
                      <Badge
                        variant="outline"
                        className={`text-[10px] h-4 px-1.5 ${PAGE_TYPE_COLORS[page.page_type] ?? ""}`}
                      >
                        {page.page_type}
                      </Badge>
                    </div>
                    {page.summary && (
                      <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">
                        {page.summary}
                      </p>
                    )}
                    {page.updated_at && (
                      <p className="text-[10px] text-muted-foreground/60 mt-1">
                        Updated{" "}
                        {new Date(page.updated_at).toLocaleDateString("en-US", {
                          day: "numeric",
                          month: "short",
                          year: "numeric",
                        })}
                      </p>
                    )}
                  </div>
                  <span
                    className="material-symbols-outlined text-muted-foreground shrink-0 opacity-0 group-hover:opacity-100 transition-opacity"
                    style={{ fontSize: 16 }}
                  >
                    open_in_new
                  </span>
                </Link>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
