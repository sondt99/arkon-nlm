"use client";

import React from "react";
import { api } from "@/lib/api";
import { DraftResponse } from "@/types/wiki";
import { WikiContent } from "./wiki-content";
import { Button } from "@/components/ui/button";

type Props = {
  drafts: DraftResponse[];
  /** Live page body, so a reviewer can diff the draft against what is published.
   * Callers that do not have it hide the tab rather than show a blank panel. */
  currentContentMd?: string;
  onApproved: (draftId: string) => void;
  onRejected: (draftId: string) => void;
};

export function WikiDraftBanner({ drafts, currentContentMd, onApproved, onRejected }: Props) {
  const [idx, setIdx] = React.useState(0);
  const [tab, setTab] = React.useState<"proposed" | "current">("proposed");
  const [rejecting, setRejecting] = React.useState(false);
  const [rejectNote, setRejectNote] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  const draft = drafts[idx];
  if (!draft) return null;

  const total = drafts.length;

  /**
   * Move to another draft, discarding everything that belonged to the previous one.
   *
   * The pager used to call `setIdx` directly and reset none of `rejecting`, `rejectNote`,
   * `error` or `tab`. Every handler reads `drafts[idx]` at call time, so: arm Reject on
   * draft A, type the reason, page to B to check something, press Confirm — and the
   * request went to B carrying A's reviewer note. That destroys another contributor's
   * submission under a note written about someone else's work, and the rejection is
   * recorded as legitimate.
   */
  const goTo = (next: number) => {
    setIdx(Math.min(Math.max(0, next), total - 1));
    setRejecting(false);
    setRejectNote("");
    setError(null);
    setTab("proposed");
  };

  const handleApprove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api(`/api/wiki/drafts/${draft.id}/approve`, {
        method: "POST",
        body: {},
      });
      onApproved(draft.id);
      goTo(idx - 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Approve failed");
    } finally {
      setBusy(false);
    }
  };

  const handleReject = async () => {
    if (!rejectNote.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/api/wiki/drafts/${draft.id}/reject`, {
        method: "POST",
        body: { reviewer_note: rejectNote },
      });
      onRejected(draft.id);
      goTo(idx - 1);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reject failed");
    } finally {
      setBusy(false);
    }
  };

  const tabs: Array<"proposed" | "current"> =
    currentContentMd === undefined ? ["proposed"] : ["proposed", "current"];
  const previewMd = tab === "current" ? currentContentMd ?? "" : draft.content_md;

  const authorLabel = draft.author_name ?? "Unknown";
  const dateLabel = new Date(draft.created_at).toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

  return (
    <div className="rounded-xl border border-amber-300/60 bg-amber-50/80 dark:bg-amber-950/20 dark:border-amber-700/40 overflow-hidden shadow-sm">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-amber-200/60 dark:border-amber-700/30">
        <span className="material-symbols-outlined text-amber-600 dark:text-amber-400" style={{ fontSize: 18 }}>
          pending
        </span>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-amber-900 dark:text-amber-200">
            Pending draft by <span className="font-semibold">{authorLabel}</span>
            <span className="font-normal text-amber-700 dark:text-amber-400"> · {dateLabel}</span>
          </p>
          {draft.note && (
            <p className="text-xs text-amber-700 dark:text-amber-400 truncate mt-0.5">"{draft.note}"</p>
          )}
        </div>

        {/* Pagination */}
        {total > 1 && (
          <div className="flex items-center gap-1 shrink-0">
            <button
              type="button"
              onClick={() => goTo(idx - 1)}
              disabled={idx === 0}
              className="w-6 h-6 flex items-center justify-center rounded hover:bg-amber-200/60 dark:hover:bg-amber-800/40 disabled:opacity-30 transition-colors"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>chevron_left</span>
            </button>
            <span className="text-xs text-amber-700 dark:text-amber-400 tabular-nums">{idx + 1}/{total}</span>
            <button
              type="button"
              onClick={() => goTo(idx + 1)}
              disabled={idx === total - 1}
              className="w-6 h-6 flex items-center justify-center rounded hover:bg-amber-200/60 dark:hover:bg-amber-800/40 disabled:opacity-30 transition-colors"
            >
              <span className="material-symbols-outlined" style={{ fontSize: 14 }}>chevron_right</span>
            </button>
          </div>
        )}
      </div>

      {/* Tab toggle */}
      <div className="flex gap-1 px-4 pt-3">
        {tabs.map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`px-3 py-1 rounded-md text-xs font-medium transition-colors capitalize ${
              tab === t
                ? "bg-amber-300/70 dark:bg-amber-700/50 text-amber-900 dark:text-amber-100"
                : "text-amber-700 dark:text-amber-400 hover:bg-amber-100/60 dark:hover:bg-amber-800/30"
            }`}
          >
            {t === "proposed" ? "Proposed" : "Current page"}
          </button>
        ))}
      </div>

      {/* Content preview */}
      <div className="px-4 py-3 max-h-72 overflow-y-auto">
        {previewMd.trim() ? (
          <WikiContent markdown={previewMd} anchors={false} />
        ) : (
          <p className="text-sm text-amber-700/60 italic">
            {tab === "proposed" ? "Empty content." : "This page has no content yet."}
          </p>
        )}
      </div>

      {/* Reject note field */}
      {rejecting && (
        <div className="px-4 pb-3">
          <label className="block text-xs font-medium text-amber-800 dark:text-amber-300 mb-1">
            Rejection reason (required)
          </label>
          <textarea
            value={rejectNote}
            onChange={(e) => setRejectNote(e.target.value)}
            rows={2}
            placeholder="Tell the contributor why this draft was rejected…"
            className="w-full rounded-lg border border-amber-300/70 dark:border-amber-700/40 bg-white/70 dark:bg-black/20 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400/40 resize-none placeholder:text-amber-400/70"
          />
        </div>
      )}

      {error && (
        <p className="px-4 pb-2 text-xs text-destructive">{error}</p>
      )}

      {/* Actions */}
      <div className="flex items-center justify-end gap-2 px-4 pb-3">
        {rejecting ? (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => { setRejecting(false); setRejectNote(""); }}
              disabled={busy}
              className="border-amber-300/60 dark:border-amber-700/40"
            >
              Cancel
            </Button>
            <Button
              size="sm"
              variant="destructive"
              onClick={handleReject}
              disabled={busy || !rejectNote.trim()}
              className="gap-1.5"
            >
              {busy ? (
                <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
              ) : (
                <span className="material-symbols-outlined text-sm">cancel</span>
              )}
              Confirm Reject
            </Button>
          </>
        ) : (
          <>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setRejecting(true)}
              disabled={busy}
              className="border-amber-300/60 dark:border-amber-700/40 text-amber-800 dark:text-amber-300 hover:bg-amber-100/60 dark:hover:bg-amber-800/30"
            >
              <span className="material-symbols-outlined text-sm mr-1">cancel</span>
              Reject
            </Button>
            <Button
              size="sm"
              onClick={handleApprove}
              disabled={busy}
              className="gap-1.5 bg-amber-600 hover:bg-amber-700 text-white"
            >
              {busy ? (
                <span className="material-symbols-outlined text-sm animate-spin">progress_activity</span>
              ) : (
                <span className="material-symbols-outlined text-sm">check_circle</span>
              )}
              Approve
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
