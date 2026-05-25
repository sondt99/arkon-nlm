"use client";

import React from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Source } from "./types";

type NotebookItem = {
  id: string;
  title: string;
  sources_count: number;
  created_at: string | null;
};

type Mode = "new" | "existing";

export function SendToNotebookLMDialog({
  source,
  onClose,
}: {
  source: Source;
  onClose: () => void;
}) {
  const router = useRouter();
  const [mode, setMode] = React.useState<Mode>("new");
  const [newTitle, setNewTitle] = React.useState(source.title || source.file_name || "Untitled");
  const [notebooks, setNotebooks] = React.useState<NotebookItem[]>([]);
  const [loadingNotebooks, setLoadingNotebooks] = React.useState(false);
  const [selectedNotebookId, setSelectedNotebookId] = React.useState<string>("");
  const [sending, setSending] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (mode === "existing" && notebooks.length === 0) {
      setLoadingNotebooks(true);
      setError(null);
      api<NotebookItem[]>("/api/notebooklm/nlm/notebooks")
        .then((data) => {
          const list = Array.isArray(data) ? data : [];
          setNotebooks(list);
          if (list.length > 0) setSelectedNotebookId(list[0].id);
        })
        .catch((e) => setError(e instanceof Error ? e.message : "Failed to load notebooks"))
        .finally(() => setLoadingNotebooks(false));
    }
  }, [mode]);

  const handleSend = async () => {
    setSending(true);
    setError(null);
    try {
      if (mode === "new") {
        if (!newTitle.trim()) { setError("Title is required"); return; }
        await api("/api/notebooklm/notebooks", {
          method: "POST",
          body: { title: newTitle.trim(), source_id: source.id },
        });
      } else {
        if (!selectedNotebookId) { setError("Please select a notebook"); return; }
        await api(`/api/notebooklm/nlm/notebooks/${selectedNotebookId}/sources`, {
          method: "POST",
          body: { kind: "arkon", source_id: source.id },
        });
      }
      onClose();
      router.push("/notebooklm");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to send to NotebookLM");
    } finally {
      setSending(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md max-h-[85vh] flex flex-col">
        <DialogHeader className="shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <span className="material-symbols-outlined text-blue-500" style={{ fontSize: 20 }}>
              book_2
            </span>
            Send to NotebookLM
          </DialogTitle>
          <p className="text-sm text-muted-foreground mt-1 truncate">{source.title}</p>
        </DialogHeader>

        <div className="flex flex-col gap-4 mt-2 min-h-0 flex-1 overflow-y-auto pr-1">
          {/* Mode selector */}
          <div className="grid grid-cols-2 gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setMode("new")}
              className={`flex flex-col items-center gap-1.5 p-3 rounded-lg border text-sm transition-colors ${
                mode === "new"
                  ? "border-primary bg-primary/5 text-primary"
                  : "border-border hover:bg-secondary/50 text-muted-foreground"
              }`}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 22 }}>add_notes</span>
              <span className="font-medium text-xs">Tạo notebook mới</span>
            </button>
            <button
              type="button"
              onClick={() => setMode("existing")}
              className={`flex flex-col items-center gap-1.5 p-3 rounded-lg border text-sm transition-colors ${
                mode === "existing"
                  ? "border-primary bg-primary/5 text-primary"
                  : "border-border hover:bg-secondary/50 text-muted-foreground"
              }`}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 22 }}>library_books</span>
              <span className="font-medium text-xs">Thêm vào notebook có sẵn</span>
            </button>
          </div>

          {/* New notebook: title input */}
          {mode === "new" && (
            <div className="flex flex-col gap-1.5 shrink-0">
              <Label>Tên notebook</Label>
              <Input
                value={newTitle}
                onChange={(e) => setNewTitle(e.target.value)}
                placeholder="Notebook title..."
                className="bg-background"
                autoFocus
              />
            </div>
          )}

          {/* Existing: notebook list */}
          {mode === "existing" && (
            <div className="flex flex-col gap-1.5 min-h-0">
              <Label className="shrink-0">Chọn notebook</Label>
              {loadingNotebooks ? (
                <div className="flex items-center justify-center py-8">
                  <span className="material-symbols-outlined animate-spin text-muted-foreground text-3xl">
                    progress_activity
                  </span>
                </div>
              ) : notebooks.length === 0 ? (
                <div className="text-sm text-muted-foreground text-center py-6 border rounded-lg bg-secondary/20">
                  Chưa có notebook nào. Hãy tạo notebook mới.
                </div>
              ) : (
                <div className="flex flex-col gap-0.5 overflow-y-auto border rounded-lg p-1 bg-background" style={{ maxHeight: 240 }}>
                  {notebooks.map((nb) => (
                    <button
                      key={nb.id}
                      type="button"
                      onClick={() => setSelectedNotebookId(nb.id)}
                      className={`flex items-center gap-3 px-3 py-2.5 rounded-md text-left w-full transition-colors ${
                        selectedNotebookId === nb.id
                          ? "bg-primary/10 text-primary"
                          : "hover:bg-secondary/50"
                      }`}
                    >
                      <span className="material-symbols-outlined shrink-0" style={{ fontSize: 18 }}>
                        {selectedNotebookId === nb.id ? "check_circle" : "menu_book"}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium truncate">{nb.title}</p>
                        <p className="text-[10px] text-muted-foreground">
                          {nb.sources_count} source{nb.sources_count !== 1 ? "s" : ""}
                          {nb.created_at && (
                            <>
                              {" · "}
                              {new Date(nb.created_at).toLocaleDateString("vi-VN", {
                                day: "numeric",
                                month: "short",
                                year: "numeric",
                              })}
                            </>
                          )}
                        </p>
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}

          {error && (
            <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg shrink-0">
              {error}
            </p>
          )}
        </div>

        {/* Footer — always visible */}
        <div className="flex justify-end gap-2 pt-3 shrink-0 border-t border-border mt-2">
          <Button variant="outline" onClick={onClose} disabled={sending}>
            Cancel
          </Button>
          <Button
            onClick={handleSend}
            disabled={sending || (mode === "existing" && (loadingNotebooks || !selectedNotebookId))}
            className="bg-blue-600 hover:bg-blue-700 text-white"
          >
            {sending ? (
              <span className="flex items-center gap-2">
                <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                Sending…
              </span>
            ) : mode === "new" ? "Tạo & gửi" : "Thêm vào notebook"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
