"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { api, apiUpload } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

type KnowledgeType = { id: string; slug: string; name: string; color: string };
type Department    = { id: string; name: string };

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  types: KnowledgeType[];
  departments: Department[];
  onUploaded: () => void;
};

const ACCEPTED_EXTENSIONS = ["pdf", "docx", "doc", "xlsx", "csv", "txt", "md", "pptx"];
const ACCEPTED_MIMES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "application/msword",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
  "text/plain", "text/csv", "text/markdown",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation",
];
const ACCEPT_STRING = [...ACCEPTED_EXTENSIONS.map((e) => `.${e}`), ".zip"].join(",");

type FileStatus = "pending" | "uploading" | "done" | "error";
type ZipResult  = { created: number; skipped: string[] };
type FileEntry  = { id: string; file: File; status: FileStatus; error?: string; zipResult?: ZipResult };

function ext(name: string) { return (name.split(".").pop() || "").toLowerCase(); }
function isZip(f: File)    { return ext(f.name) === "zip"; }
function fmtSize(b: number) {
  if (b < 1024) return `${b} B`;
  if (b < 1048576) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1048576).toFixed(1)} MB`;
}
function validateFile(f: File): string | null {
  if (isZip(f)) {
    if (f.size > 100 * 1024 * 1024) return "Zip exceeds 100 MB";
    return null;
  }
  if (!ACCEPTED_EXTENSIONS.includes(ext(f.name)) && !ACCEPTED_MIMES.includes(f.type))
    return `Unsupported type ".${ext(f.name)}"`;
  if (f.size > 50 * 1024 * 1024) return "Exceeds 50 MB";
  return null;
}

const STATUS_ICON: Record<FileStatus, { icon: string; cls: string }> = {
  pending:   { icon: "description",       cls: "text-muted-foreground/40" },
  uploading: { icon: "progress_activity", cls: "text-primary animate-spin" },
  done:      { icon: "check_circle",      cls: "text-green-500" },
  error:     { icon: "error",             cls: "text-destructive" },
};

export function UploadDialog({ open, onOpenChange, types, departments, onUploaded }: Props) {
  const [entries,       setEntries]       = useState<FileEntry[]>([]);
  const [addError,      setAddError]      = useState("");
  const [typeId,        setTypeId]        = useState("");
  const [selectedDepts, setSelectedDepts] = useState<string[]>([]);
  const [scopeType,     setScopeType]     = useState("global");
  const [scopeId,       setScopeId]       = useState("");
  const [projects,      setProjects]      = useState<{ id: string; name: string }[]>([]);
  const [uploading,     setUploading]     = useState(false);
  const [dragOver,      setDragOver]      = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = useCallback(() => {
    setEntries([]); setAddError(""); setTypeId("");
    setSelectedDepts([]); setScopeType("global"); setScopeId("");
  }, []);

  useEffect(() => {
    if (!open) { reset(); return; }
    api<{ id: string; name: string }[]>("/api/projects")
      .then((d) => setProjects(Array.isArray(d) ? d : []))
      .catch(() => setProjects([]));
  }, [open, reset]);

  const addFiles = useCallback((incoming: File[]) => {
    setAddError("");
    const toAdd: FileEntry[] = [];
    const errs: string[] = [];
    for (const f of incoming) {
      const e = validateFile(f);
      if (e) { errs.push(`${f.name}: ${e}`); continue; }
      toAdd.push({ id: `${f.name}-${f.size}-${f.lastModified}`, file: f, status: "pending" });
    }
    if (errs.length) setAddError(errs.join(" · "));
    setEntries((prev) => {
      const ids = new Set(prev.map((e) => e.id));
      return [...prev, ...toAdd.filter((e) => !ids.has(e.id))];
    });
  }, []);

  const removeEntry = (id: string) => setEntries((p) => p.filter((e) => e.id !== id));

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault(); e.stopPropagation(); setDragOver(false);
    addFiles(Array.from(e.dataTransfer.files));
  }, [addFiles]);

  const handleDragOver  = useCallback((e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setDragOver(true);  }, []);
  const handleDragLeave = useCallback((e: React.DragEvent) => { e.preventDefault(); e.stopPropagation(); setDragOver(false); }, []);

  const handleUpload = async () => {
    const toUpload = entries.filter((e) => e.status === "pending" || e.status === "error");
    if (!toUpload.length) return;
    setUploading(true);
    let anyError = false;

    for (const entry of toUpload) {
      setEntries((p) => p.map((e) => e.id === entry.id ? { ...e, status: "uploading", error: undefined } : e));
      try {
        const fd = new FormData();
        fd.append("file", entry.file);
        if (typeId)               fd.append("knowledge_type_id", typeId);
        if (selectedDepts.length) fd.append("department_ids", selectedDepts.join(","));
        fd.append("scope_type", scopeType);
        if (scopeType !== "global" && scopeId) fd.append("scope_id", scopeId);

        if (isZip(entry.file)) {
          const res = await apiUpload<ZipResult>("/api/sources/upload-zip", fd);
          setEntries((p) => p.map((e) =>
            e.id === entry.id ? { ...e, status: "done", zipResult: res } : e
          ));
        } else {
          await apiUpload("/api/sources/upload", fd);
          setEntries((p) => p.map((e) => e.id === entry.id ? { ...e, status: "done" } : e));
        }
      } catch (err) {
        anyError = true;
        const msg = err instanceof Error ? err.message : "Upload failed";
        setEntries((p) => p.map((e) => e.id === entry.id ? { ...e, status: "error", error: msg } : e));
      }
    }

    setUploading(false);
    onUploaded();
    if (!anyError) onOpenChange(false);
  };

  const pendingCount = entries.filter((e) => e.status === "pending").length;
  const errorCount   = entries.filter((e) => e.status === "error").length;
  const doneCount    = entries.filter((e) => e.status === "done").length;
  const retryCount   = pendingCount + errorCount;

  const uploadLabel = uploading
    ? `Uploading… (${doneCount}/${entries.length})`
    : errorCount > 0 && !pendingCount
      ? `Retry ${errorCount} failed`
      : `Upload ${retryCount} file${retryCount !== 1 ? "s" : ""}`;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/*
        flex flex-col + max-h keeps the dialog bounded.
        The base DialogContent uses `grid`; flex overrides it via tailwind-merge.
        overflow-hidden clips children; scrolling happens on the inner body div.
      */}
      <DialogContent className="flex flex-col w-full max-w-[calc(100%-1.5rem)] sm:max-w-lg overflow-hidden max-h-[90dvh]">

        {/* ── Fixed header ── */}
        <DialogHeader className="shrink-0 pb-3 border-b border-border">
          <DialogTitle className="text-base font-semibold">Upload Documents</DialogTitle>
        </DialogHeader>

        {/* ── Scrollable body ── */}
        <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-3 py-3">

          {/* Dropzone — compact when files exist */}
          <div
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => !uploading && fileInputRef.current?.click()}
            className={cn(
              "flex items-center gap-3 rounded-lg border-2 border-dashed transition-all duration-200 cursor-pointer select-none",
              entries.length > 0 ? "px-3 py-2.5" : "flex-col justify-center px-4 py-8",
              dragOver    ? "border-primary bg-primary/5"
              : entries.length > 0 ? "border-border hover:border-primary/50 hover:bg-accent/20"
              : "border-border hover:border-primary/40 hover:bg-accent/30",
              uploading && "pointer-events-none opacity-60"
            )}
          >
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={ACCEPT_STRING}
              onChange={(e) => { if (e.target.files) addFiles(Array.from(e.target.files)); e.target.value = ""; }}
              className="hidden"
            />
            <div className={cn(
              "rounded-full flex items-center justify-center shrink-0 transition-colors",
              entries.length > 0 ? "w-7 h-7" : "w-10 h-10",
              dragOver ? "bg-primary/10" : "bg-accent/60"
            )}>
              <span className={cn("material-symbols-outlined", dragOver ? "text-primary" : "text-muted-foreground")}
                style={{ fontSize: entries.length > 0 ? 16 : 20 }}>
                upload_file
              </span>
            </div>
            {entries.length > 0 ? (
              <span className="text-[13px] text-muted-foreground">
                {dragOver ? "Drop to add more" : "Click or drop to add more files"}
              </span>
            ) : (
              <div className="text-center">
                <p className="text-sm font-medium text-foreground">
                  {dragOver ? "Drop files here" : "Drag & drop or click to browse"}
                </p>
                <p className="text-xs text-muted-foreground mt-0.5">
                  PDF, DOCX, XLSX, CSV, TXT, MD, PPTX · Max 50 MB
                </p>
                <p className="text-xs text-muted-foreground/70 mt-0.5">
                  Or upload a <span className="font-medium text-amber-600 dark:text-amber-400">ZIP archive</span> containing multiple files · Max 100 MB
                </p>
              </div>
            )}
          </div>

          {/* Validation error */}
          {addError && (
            <p className="text-xs text-destructive flex items-start gap-1.5 -mt-1">
              <span className="material-symbols-outlined shrink-0" style={{ fontSize: 13, marginTop: 1 }}>error</span>
              <span className="break-words">{addError}</span>
            </p>
          )}

          {/* File list */}
          {entries.length > 0 && (
            <div className="rounded-lg border border-border overflow-hidden bg-background">
              <div className="overflow-y-auto" style={{ maxHeight: "11rem" }}>
                {entries.map((entry) => {
                  const { icon, cls } = STATUS_ICON[entry.status];
                  const zip = isZip(entry.file);
                  const zipDone = zip && entry.status === "done" && entry.zipResult;
                  return (
                    <div key={entry.id} className="flex items-center gap-2 px-3 py-2 border-b border-border/50 last:border-0">
                      <span
                        className={cn("material-symbols-outlined shrink-0", zip && entry.status === "pending" ? "text-amber-500" : cls)}
                        style={{ fontSize: 15 }}
                      >
                        {zip && entry.status === "pending" ? "folder_zip" : icon}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-[13px] truncate leading-snug">{entry.file.name}</p>
                        <p className={cn("text-[11px] truncate leading-tight", entry.error ? "text-destructive" : "text-muted-foreground")}>
                          {entry.error
                            ? entry.error
                            : zipDone
                              ? `${entry.zipResult!.created} file${entry.zipResult!.created !== 1 ? "s" : ""} extracted${entry.zipResult!.skipped.length ? ` · ${entry.zipResult!.skipped.length} skipped` : ""}`
                              : zip
                                ? `${fmtSize(entry.file.size)} · ZIP archive`
                                : `${fmtSize(entry.file.size)} · ${ext(entry.file.name).toUpperCase()}`}
                        </p>
                        {zipDone && entry.zipResult!.skipped.length > 0 && (
                          <p className="text-[10px] text-amber-600 dark:text-amber-400 truncate leading-tight mt-0.5">
                            Skipped: {entry.zipResult!.skipped.slice(0, 3).join(", ")}
                            {entry.zipResult!.skipped.length > 3 && ` +${entry.zipResult!.skipped.length - 3} more`}
                          </p>
                        )}
                      </div>
                      {!uploading && entry.status !== "done" && (
                        <button type="button" onClick={() => removeEntry(entry.id)}
                          className="shrink-0 w-5 h-5 flex items-center justify-center rounded text-muted-foreground/40 hover:text-destructive hover:bg-destructive/10 transition-colors">
                          <span className="material-symbols-outlined" style={{ fontSize: 13 }}>close</span>
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
              {/* Summary bar */}
              <div className="flex items-center justify-between px-3 py-1.5 bg-muted/30 border-t border-border text-[11px] text-muted-foreground">
                <span>
                  {entries.length} file{entries.length !== 1 ? "s" : ""}
                  {doneCount > 0 && <span className="text-green-600 ml-1.5">· {doneCount} done</span>}
                  {errorCount > 0 && <span className="text-destructive ml-1.5">· {errorCount} failed</span>}
                </span>
                {!uploading && (
                  <button type="button" onClick={() => setEntries([])}
                    className="hover:text-foreground transition-colors">
                    Clear all
                  </button>
                )}
              </div>
            </div>
          )}

          {/* Knowledge Type + Visibility — side by side on sm+ */}
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">Knowledge Type</Label>
              <Select value={typeId} onValueChange={(v) => setTypeId(v ?? "")}>
                <SelectTrigger className="bg-background h-8 text-[13px]">
                  {typeId ? (() => {
                    const t = types.find((x) => x.id === typeId);
                    return t ? (
                      <div className="flex items-center gap-1.5">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: t.color }} />
                        <span className="truncate">{t.name}</span>
                      </div>
                    ) : <SelectValue placeholder="Optional" />;
                  })() : <SelectValue placeholder="Optional" />}
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="">None</SelectItem>
                  {types.map((t) => (
                    <SelectItem key={t.id} value={t.id}>
                      <div className="flex items-center gap-2">
                        <span className="w-2 h-2 rounded-full shrink-0" style={{ backgroundColor: t.color }} />
                        {t.name}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">Visibility</Label>
              <Select value={scopeType} onValueChange={(v) => { const val = v ?? "global"; setScopeType(val); if (val === "global") setScopeId(""); }}>
                <SelectTrigger className="bg-background h-8 text-[13px]">
                  <div className="flex items-center gap-1.5">
                    <span className="material-symbols-outlined" style={{ fontSize: 13 }}>
                      {scopeType === "global" ? "public" : "folder_special"}
                    </span>
                    <span>{scopeType === "project" ? "Workspace" : "Global"}</span>
                  </div>
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="global">
                    <div className="flex items-center gap-2">
                      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>public</span>
                      Global
                    </div>
                  </SelectItem>
                  <SelectItem value="project">
                    <div className="flex items-center gap-2">
                      <span className="material-symbols-outlined" style={{ fontSize: 13 }}>folder_special</span>
                      Workspace
                    </div>
                  </SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>

          {/* Global warning */}
          {scopeType === "global" && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400 flex items-start gap-1.5 -mt-1 bg-amber-50 dark:bg-amber-950/30 rounded-lg px-2.5 py-2">
              <span className="material-symbols-outlined shrink-0" style={{ fontSize: 12, marginTop: 1 }}>warning</span>
              Content will be compiled into the shared wiki, visible to all employees. Only upload non-sensitive documents.
            </p>
          )}

          {/* Target Workspace */}
          {scopeType === "project" && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">Target Workspace</Label>
              <Select value={scopeId} onValueChange={(v) => setScopeId(v ?? "")}>
                <SelectTrigger className="bg-background h-8 text-[13px]">
                  <span>{scopeId ? (projects.find((p) => p.id === scopeId)?.name ?? "Select…") : "Select workspace…"}</span>
                </SelectTrigger>
                <SelectContent>
                  {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          )}

          {/* Departments */}
          {departments.length > 0 && (
            <div className="flex flex-col gap-1.5">
              <Label className="text-xs">
                Department Access
                <span className="ml-1.5 font-normal text-muted-foreground">(optional)</span>
              </Label>
              <div className="border border-border rounded-lg overflow-hidden bg-background" style={{ maxHeight: "8rem" }}>
                <div className="overflow-y-auto h-full">
                  {departments.map((d) => (
                    <label key={d.id}
                      className="flex items-center gap-2 px-3 py-1.5 cursor-pointer hover:bg-muted/50 border-b border-border/40 last:border-0">
                      <input
                        type="checkbox"
                        checked={selectedDepts.includes(d.id)}
                        onChange={() => setSelectedDepts((p) => p.includes(d.id) ? p.filter((x) => x !== d.id) : [...p, d.id])}
                        className="rounded border-border shrink-0"
                      />
                      <span className="text-[13px]">{d.name}</span>
                    </label>
                  ))}
                </div>
              </div>
              {selectedDepts.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {selectedDepts.map((id) => {
                    const name = departments.find((d) => d.id === id)?.name ?? id;
                    return (
                      <span key={id}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-primary/10 text-primary">
                        {name}
                        <button type="button"
                          onClick={() => setSelectedDepts((p) => p.filter((x) => x !== id))}
                          className="hover:text-destructive leading-none">×</button>
                      </span>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Fixed footer ── */}
        <div className="shrink-0 flex justify-end gap-2 pt-3 border-t border-border">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)} disabled={uploading}>
            {errorCount > 0 && !pendingCount && doneCount > 0 ? "Close" : "Cancel"}
          </Button>
          <Button size="sm" disabled={retryCount === 0 || uploading} onClick={handleUpload}
            className="min-w-[110px]">
            {uploading
              ? <><span className="material-symbols-outlined animate-spin mr-1.5" style={{ fontSize: 13 }}>progress_activity</span>{uploadLabel}</>
              : uploadLabel}
          </Button>
        </div>

      </DialogContent>
    </Dialog>
  );
}
