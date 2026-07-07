"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import { useDebounce } from "@/lib/hooks/use-debounce";
import { WikiSearchResult } from "@/types/wiki";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { wikiTypeIcon, wikiTypeColor, wikiTypeGroupLabel } from "./wiki-type-badge";

const GROUP_ORDER = ["entity", "concept", "topic", "source", "synthesis"];

type Status = "idle" | "loading" | "ready" | "error";

export function WikiSearchDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
}) {
  const router = useRouter();
  const [results, setResults] = React.useState<WikiSearchResult[]>([]);
  const [status, setStatus] = React.useState<Status>("idle");
  const [errorMsg, setErrorMsg] = React.useState<string | null>(null);
  const [selectedIndex, setSelectedIndex] = React.useState(0);
  const [query, setQuery] = React.useState("");
  const inputRef = React.useRef<HTMLInputElement>(null);
  const listRef = React.useRef<HTMLDivElement>(null);
  const debouncedQuery = useDebounce(query, 250);

  React.useEffect(() => {
    if (!open || !debouncedQuery.trim()) {
      setResults([]);
      setStatus("idle");
      return;
    }
    let cancelled = false;
    setStatus("loading");
    api<WikiSearchResult[]>(
      `/api/wiki/search?q=${encodeURIComponent(debouncedQuery)}&top_k=20`
    )
      .then((d) => {
        if (cancelled) return;
        setResults(Array.isArray(d) ? d : []);
        setStatus("ready");
      })
      .catch((err) => {
        if (cancelled) return;
        setResults([]);
        setStatus("error");
        setErrorMsg(
          err instanceof ApiError && err.status === 503
            ? "Search isn't configured yet — ask an admin to set an embedding model."
            : "Search failed. Try again."
        );
      });
    return () => {
      cancelled = true;
    };
  }, [open, debouncedQuery]);

  React.useEffect(() => setSelectedIndex(0), [results]);

  React.useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
    } else {
      setQuery("");
      setResults([]);
      setStatus("idle");
    }
  }, [open]);

  const grouped = React.useMemo(() => {
    const map = new Map<string, WikiSearchResult[]>();
    for (const r of results) {
      if (!map.has(r.page_type)) map.set(r.page_type, []);
      map.get(r.page_type)!.push(r);
    }
    return map;
  }, [results]);

  // Flattened in the same order results render, for keyboard navigation.
  const flatResults = React.useMemo(() => {
    const flat: WikiSearchResult[] = [];
    for (const type of GROUP_ORDER) {
      const items = grouped.get(type);
      if (items) flat.push(...items);
    }
    return flat;
  }, [grouped]);

  React.useEffect(() => {
    const el = listRef.current?.querySelector(`[data-index="${selectedIndex}"]`);
    el?.scrollIntoView({ block: "nearest" });
  }, [selectedIndex]);

  const navigate = (r: WikiSearchResult) => {
    const scoped = r.scope_type && r.scope_type !== "global" && r.scope_id;
    router.push(
      `/wiki/${r.slug}${scoped ? `?scopeType=${r.scope_type}&scopeId=${r.scope_id}` : ""}`
    );
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg p-0 overflow-hidden gap-0">
        {/* Search input */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-border">
          <span className="material-symbols-outlined text-muted-foreground text-base">
            search
          </span>
          <input
            ref={inputRef}
            type="text"
            placeholder="Search wiki pages..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                onOpenChange(false);
                return;
              }
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSelectedIndex((i) => Math.min(i + 1, flatResults.length - 1));
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setSelectedIndex((i) => Math.max(i - 1, 0));
                return;
              }
              if (e.key === "Enter" && flatResults[selectedIndex]) {
                e.preventDefault();
                navigate(flatResults[selectedIndex]);
              }
            }}
            className="flex-1 text-sm bg-transparent outline-none text-foreground placeholder:text-muted-foreground"
          />
          {query && (
            <button onClick={() => setQuery("")} className="text-muted-foreground hover:text-foreground">
              <span className="material-symbols-outlined text-base">close</span>
            </button>
          )}
        </div>

        {/* Results */}
        <div ref={listRef} className="max-h-96 overflow-y-auto py-2">
          {status === "idle" ? (
            <p className="text-sm text-muted-foreground px-4 py-6 text-center">
              Type to search wiki pages…
            </p>
          ) : status === "loading" ? (
            <div className="px-4 py-2 space-y-2">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-9 rounded-md bg-muted animate-pulse" style={{ opacity: 1 - i * 0.15 }} />
              ))}
            </div>
          ) : status === "error" ? (
            <p className="text-sm text-muted-foreground px-4 py-6 text-center">{errorMsg}</p>
          ) : results.length === 0 ? (
            <p className="text-sm text-muted-foreground px-4 py-6 text-center">
              No pages match your search.
            </p>
          ) : (
            GROUP_ORDER.filter((t) => grouped.has(t)).map((type) => (
              <div key={type} className="mb-1">
                <div className="flex items-center gap-2 px-4 py-1.5">
                  <span
                    className="material-symbols-outlined"
                    style={{ color: wikiTypeColor(type), fontSize: 13 }}
                  >
                    {wikiTypeIcon(type)}
                  </span>
                  <span className="text-xs font-semibold text-muted-foreground uppercase tracking-wide">
                    {wikiTypeGroupLabel(type)}
                  </span>
                </div>
                {grouped.get(type)!.map((page) => {
                  const flatIndex = flatResults.indexOf(page);
                  const isSelected = flatIndex === selectedIndex;
                  return (
                    <button
                      key={page.slug}
                      data-index={flatIndex}
                      onClick={() => navigate(page)}
                      onMouseEnter={() => setSelectedIndex(flatIndex)}
                      className={`w-full flex items-start gap-3 px-4 py-2 transition-colors text-left ${
                        isSelected ? "bg-accent/60" : "hover:bg-accent/50"
                      }`}
                    >
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-foreground truncate">
                          {page.title}
                        </p>
                        {page.summary && (
                          <p className="text-xs text-muted-foreground truncate mt-0.5">
                            {page.summary}
                          </p>
                        )}
                      </div>
                      <span className="text-xs text-muted-foreground mt-0.5 shrink-0">
                        {page.slug}
                      </span>
                    </button>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <div className="border-t border-border px-4 py-2 flex items-center gap-4 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 rounded border border-border font-mono text-xs">↑↓</kbd>
            navigate
          </span>
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 rounded border border-border font-mono text-xs">↵</kbd>
            open
          </span>
          <span className="flex items-center gap-1">
            <kbd className="px-1 py-0.5 rounded border border-border font-mono text-xs">Esc</kbd>
            close
          </span>
          <span className="ml-auto">{results.length} results</span>
        </div>
      </DialogContent>
    </Dialog>
  );
}
