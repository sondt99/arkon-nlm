"use client";

import React from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Source } from "./types";

type PlanCandidate = {
  slug: string;
  title: string;
  page_type: string;
  summary?: string;
  similarity: number;
  match_method?: string;
};

type PlanPage = {
  action: "CREATE" | "UPDATE";
  slug: string;
  title: string;
  page_type: string;
  entity_names?: string[];
  priority?: number;
  related_kb_pages?: string[];
  match_confidence?: number;
  match_method?: string;
  match_reason?: string;
  candidates?: PlanCandidate[];
};

type PlanData = {
  pages: PlanPage[];
  strategy?: string;
  compilation_notes?: string;
  estimated_page_count?: number;
  source_page_slug?: string;
};

type PlanResponse = {
  id: string;
  status: string;
  plan: PlanData;
  review_note: string | null;
};

const PAGE_TYPES = ["entity", "concept", "topic", "source"];

function EditForm({
  page,
  onSave,
  onCancel,
}: {
  page: PlanPage;
  onSave: (updated: PlanPage) => void;
  onCancel: () => void;
}) {
  const [draft, setDraft] = React.useState<PlanPage>({ ...page });
  const [entityInput, setEntityInput] = React.useState(
    (page.entity_names ?? []).join(", ")
  );

  const handleSave = () => {
    const names = entityInput
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    onSave({ ...draft, entity_names: names });
  };

  return (
    <div className="flex flex-col gap-3 p-3 rounded-lg border border-primary/40 bg-primary/5">
      <div className="grid grid-cols-2 gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">
            Title
          </label>
          <input
            className="text-sm rounded-md border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50"
            value={draft.title}
            onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
          />
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">
            Slug
          </label>
          <input
            className="text-sm rounded-md border border-border bg-background px-2 py-1.5 font-mono focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50"
            value={draft.slug}
            onChange={(e) => setDraft((d) => ({ ...d, slug: e.target.value }))}
          />
        </div>
      </div>

      <div className="grid grid-cols-3 gap-2">
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">
            Action
          </label>
          <select
            className="text-sm rounded-md border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
            value={draft.action}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                action: e.target.value as "CREATE" | "UPDATE",
              }))
            }
          >
            <option value="CREATE">CREATE</option>
            <option value="UPDATE">UPDATE</option>
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">
            Type
          </label>
          <select
            className="text-sm rounded-md border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
            value={draft.page_type}
            onChange={(e) =>
              setDraft((d) => ({ ...d, page_type: e.target.value }))
            }
          >
            {PAGE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-1">
          <label className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">
            Priority
          </label>
          <input
            type="number"
            min={1}
            className="text-sm rounded-md border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
            value={draft.priority ?? ""}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                priority: e.target.value ? Number(e.target.value) : undefined,
              }))
            }
          />
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <label className="text-[10px] text-muted-foreground font-medium uppercase tracking-wide">
          Entities / Topics covered{" "}
          <span className="normal-case font-normal">(comma-separated)</span>
        </label>
        <input
          className="text-sm rounded-md border border-border bg-background px-2 py-1.5 focus:outline-none focus:ring-2 focus:ring-primary/30"
          value={entityInput}
          onChange={(e) => setEntityInput(e.target.value)}
          placeholder="entity A, entity B, ..."
        />
      </div>

      {(draft.candidates?.length ?? 0) > 0 && (
        <div className="rounded-lg border border-border bg-muted/25 p-2.5">
          <p className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
            Existing page candidates
          </p>
          <div className="space-y-1.5">
            {draft.candidates!.slice(0, 3).map((candidate) => (
              <button
                key={candidate.slug}
                type="button"
                onClick={() => setDraft((current) => ({
                  ...current,
                  action: "UPDATE",
                  slug: candidate.slug,
                  title: candidate.title,
                  page_type: candidate.page_type,
                  match_confidence: candidate.similarity,
                  match_method: candidate.match_method || "reviewer_selected",
                  match_reason: "Reviewer selected an existing candidate",
                }))}
                className={cn(
                  "flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors",
                  draft.action === "UPDATE" && draft.slug === candidate.slug
                    ? "border-primary/50 bg-primary/10"
                    : "border-border bg-background hover:border-primary/30 hover:bg-primary/5"
                )}
              >
                <span className="material-symbols-outlined mt-0.5 text-[14px] text-primary">merge</span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs font-medium">{candidate.title}</span>
                  <span className="block truncate font-mono text-[9px] text-muted-foreground">{candidate.slug}</span>
                  {candidate.summary && (
                    <span className="mt-1 line-clamp-2 block text-[10px] leading-4 text-muted-foreground">
                      {candidate.summary}
                    </span>
                  )}
                </span>
                <span className="font-mono text-[10px] text-primary">{Math.round(candidate.similarity * 100)}%</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={handleSave}>
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>
            check
          </span>
          Save
        </Button>
      </div>
    </div>
  );
}

function PlanPageRow({
  page,
  onEdit,
  onDelete,
}: {
  page: PlanPage;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex items-start gap-3 p-3 rounded-lg border border-border bg-card group">
      <Badge
        variant="outline"
        className={`shrink-0 text-[10px] font-medium h-5 px-1.5 mt-0.5 ${
          page.action === "CREATE"
            ? "border-green-500/50 text-green-600"
            : "border-yellow-500/50 text-yellow-600"
        }`}
      >
        {page.action}
      </Badge>

      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-medium">{page.title}</span>
          <span className="text-[10px] text-muted-foreground font-mono">
            {page.slug}
          </span>
          <Badge variant="secondary" className="text-[10px] h-4 px-1.5">
            {page.page_type}
          </Badge>
          {page.priority !== undefined && (
            <span className="text-[10px] text-muted-foreground">
              #{page.priority}
            </span>
          )}
        </div>
        {page.entity_names && page.entity_names.length > 0 && (
          <p className="text-[11px] text-muted-foreground mt-1 truncate">
            {page.entity_names.slice(0, 6).join(", ")}
            {page.entity_names.length > 6 &&
              ` +${page.entity_names.length - 6} more`}
          </p>
        )}
        {(page.match_reason || page.match_confidence !== undefined) && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-border/70 bg-muted/30 px-2.5 py-2 text-[10px] text-muted-foreground">
            <span className="material-symbols-outlined text-[13px] text-primary">troubleshoot</span>
            <span className="min-w-0 flex-1">
              {page.match_reason || page.match_method || "Knowledge-base reconciliation"}
            </span>
            {page.match_confidence !== undefined && (
              <span className="shrink-0 font-mono text-primary">{Math.round(page.match_confidence * 100)}%</span>
            )}
          </div>
        )}
        {(page.candidates?.length ?? 0) > 0 && page.action === "CREATE" && (
          <p className="mt-1.5 text-[10px] text-amber-600 dark:text-amber-300">
            {page.candidates!.length} trang cũ gần giống — Edit để chọn nếu đây là cùng một concept.
          </p>
        )}
      </div>

      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
        <button
          type="button"
          onClick={onEdit}
          className="p-1 rounded hover:bg-secondary text-muted-foreground hover:text-foreground transition-colors"
          title="Edit"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
            edit
          </span>
        </button>
        <button
          type="button"
          onClick={onDelete}
          className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive transition-colors"
          title="Delete"
        >
          <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
            delete
          </span>
        </button>
      </div>
    </div>
  );
}

export function PlanReviewDialog({
  source,
  onClose,
  onDone,
}: {
  source: Source;
  onClose: () => void;
  onDone: () => void;
}) {
  const [planMeta, setPlanMeta] = React.useState<Omit<PlanData, "pages"> | null>(null);
  const [pages, setPages] = React.useState<PlanPage[]>([]);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [submitting, setSubmitting] = React.useState<"approve" | "reject" | null>(null);
  const [reviewNote, setReviewNote] = React.useState("");
  const [confirmReject, setConfirmReject] = React.useState(false);
  const [editingIdx, setEditingIdx] = React.useState<number | null>(null);
  const [deleteConfirmIdx, setDeleteConfirmIdx] = React.useState<number | null>(null);

  React.useEffect(() => {
    api<PlanResponse>(`/api/sources/${source.id}/plan`)
      .then((res) => {
        const { pages: pg, ...rest } = res.plan;
        setPlanMeta(rest);
        setPages([...(pg ?? [])].sort((a, b) => (a.priority ?? 99) - (b.priority ?? 99)));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Failed to load plan"))
      .finally(() => setLoading(false));
  }, [source.id]);

  const handleSaveEdit = (idx: number, updated: PlanPage) => {
    setPages((prev) => prev.map((p, i) => (i === idx ? updated : p)));
    setEditingIdx(null);
  };

  const handleDelete = (idx: number) => {
    if (deleteConfirmIdx !== idx) {
      setDeleteConfirmIdx(idx);
      return;
    }
    setPages((prev) => prev.filter((_, i) => i !== idx));
    setDeleteConfirmIdx(null);
  };

  const handleAddPage = () => {
    // Derived from the highest number in use, not from `pages.length`: add / add /
    // delete-the-first / add produced two pages with the slug `new-page-2`, and the
    // approve request carried both. `key={idx}` on the list meant React never warned.
    const takenSlugs = new Set(pages.map((p) => p.slug));
    let n = pages.length + 1;
    while (takenSlugs.has(`new-page-${n}`)) n += 1;
    const nextPriority = pages.reduce((max, p) => Math.max(max, p.priority ?? 0), 0) + 1;

    const newPage: PlanPage = {
      action: "CREATE",
      slug: `new-page-${n}`,
      title: "New Page",
      page_type: "concept",
      entity_names: [],
      priority: nextPriority,
    };
    setPages((prev) => [...prev, newPage]);
    setEditingIdx(pages.length);
  };

  const handleApprove = async () => {
    setSubmitting("approve");
    setError(null);
    try {
      await api(`/api/sources/${source.id}/plan/approve`, {
        method: "POST",
        body: {
          note: reviewNote || "Approved via UI",
          modified_plan: planMeta ? { ...planMeta, pages } : { pages },
        },
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to approve plan");
      setSubmitting(null);
    }
  };

  const handleReject = async () => {
    if (!confirmReject) {
      setConfirmReject(true);
      return;
    }
    setSubmitting("reject");
    setError(null);
    try {
      await api(`/api/sources/${source.id}/plan/reject`, {
        method: "POST",
        body: { note: reviewNote || "Rejected via UI" },
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to reject plan");
      setSubmitting(null);
    }
  };

  const creates = pages.filter((p) => p.action === "CREATE");
  const updates = pages.filter((p) => p.action === "UPDATE");

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-2xl max-h-[90vh] flex flex-col">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="material-symbols-outlined text-blue-500" style={{ fontSize: 20 }}>
              fact_check
            </span>
            Review Compilation Plan
          </DialogTitle>
          <p className="text-sm text-muted-foreground mt-1">
            {source.title} — chỉnh sửa plan nếu cần, sau đó Approve để bắt đầu viết wiki.
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
            <div className="text-sm text-destructive bg-destructive/10 px-4 py-2 rounded-lg mb-4">
              {error}
            </div>
          )}

          {!loading && planMeta !== null && (
            <div className="flex flex-col gap-3">
              {/* Summary */}
              <div className="flex items-center gap-4 text-sm text-muted-foreground flex-wrap">
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-green-500" />
                  {creates.length} trang tạo mới
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-yellow-500" />
                  {updates.length} trang cập nhật
                </span>
                {planMeta.strategy && (
                  <span className="flex items-center gap-1.5">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>analytics</span>
                    {planMeta.strategy}
                  </span>
                )}
              </div>

              {/* Planner notes */}
              {planMeta.compilation_notes && (
                <div className="text-xs text-muted-foreground bg-secondary/40 rounded-lg px-3 py-2 border border-border">
                  <span className="font-medium text-foreground">Ghi chú: </span>
                  {planMeta.compilation_notes}
                </div>
              )}

              {/* Page list */}
              <div className="flex flex-col gap-2">
                {pages.map((page, idx) =>
                  editingIdx === idx ? (
                    <EditForm
                      key={idx}
                      page={page}
                      onSave={(updated) => handleSaveEdit(idx, updated)}
                      onCancel={() => setEditingIdx(null)}
                    />
                  ) : deleteConfirmIdx === idx ? (
                    <div
                      key={idx}
                      className="flex items-center justify-between gap-3 p-3 rounded-lg border border-destructive/40 bg-destructive/5"
                    >
                      <span className="text-sm text-destructive">
                        Xóa trang <strong>{page.title}</strong>?
                      </span>
                      <div className="flex items-center gap-2 shrink-0">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => setDeleteConfirmIdx(null)}
                        >
                          Hủy
                        </Button>
                        <Button
                          variant="destructive"
                          size="sm"
                          onClick={() => handleDelete(idx)}
                        >
                          Xóa
                        </Button>
                      </div>
                    </div>
                  ) : (
                    <PlanPageRow
                      key={idx}
                      page={page}
                      onEdit={() => {
                        setDeleteConfirmIdx(null);
                        setEditingIdx(idx);
                      }}
                      onDelete={() => {
                        setEditingIdx(null);
                        handleDelete(idx);
                      }}
                    />
                  )
                )}
              </div>

              {/* Add page */}
              <button
                type="button"
                onClick={handleAddPage}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-dashed border-border text-sm text-muted-foreground hover:text-foreground hover:border-primary/50 hover:bg-secondary/40 transition-colors"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 16 }}>add</span>
                Thêm trang mới
              </button>
            </div>
          )}
        </div>

        {/* Review note */}
        <div className="mt-4 shrink-0">
          <textarea
            value={reviewNote}
            onChange={(e) => setReviewNote(e.target.value)}
            placeholder="Ghi chú review (tùy chọn)"
            className="w-full text-sm rounded-lg border border-border bg-background px-3 py-2 resize-none h-14 focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50"
          />
        </div>

        <div className="flex items-center justify-between gap-2 mt-3 pt-3 border-t border-border shrink-0">
          <Button variant="ghost" onClick={onClose} disabled={submitting !== null}>
            Đóng
          </Button>

          <div className="flex items-center gap-2">
            {!confirmReject ? (
              <Button
                variant="outline"
                onClick={() => setConfirmReject(true)}
                disabled={loading || submitting !== null}
                className="text-destructive border-destructive/30 hover:bg-destructive/10"
              >
                <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                Từ chối
              </Button>
            ) : (
              <Button
                variant="destructive"
                onClick={handleReject}
                disabled={submitting !== null}
              >
                {submitting === "reject" ? (
                  <span className="material-symbols-outlined animate-spin" style={{ fontSize: 16 }}>
                    progress_activity
                  </span>
                ) : (
                  <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                )}
                Xác nhận từ chối
              </Button>
            )}
            <Button
              onClick={handleApprove}
              disabled={loading || submitting !== null || pages.length === 0}
            >
              {submitting === "approve" ? (
                <span className="material-symbols-outlined animate-spin" style={{ fontSize: 16 }}>
                  progress_activity
                </span>
              ) : (
                <span className="material-symbols-outlined" style={{ fontSize: 16 }}>check</span>
              )}
              Approve & Compile
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
