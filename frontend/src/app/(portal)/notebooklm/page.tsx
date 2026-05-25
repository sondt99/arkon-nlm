"use client";

// genId() requires a secure context (HTTPS/localhost); fall back for plain HTTP
function genId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return genId();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiUpload, ApiError, getToken } from "@/lib/api";
import { PageHeader } from "@/components/shared/page-header";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import {
  ARTIFACT_ICONS,
  ARTIFACT_LABELS,
  REPORT_FORMAT_LABELS,
  type ArtifactType,
  type NLMArtifactNative,
  type NLMNotebookNative,
  type NLMSourceNative,
  type ChatMessage,
  type ChatReference,
  type ReportFormat,
} from "@/types/notebooklm";
import { PlanReviewDialog } from "@/components/knowledge/knowledge-table/plan-review-dialog";

/* ─── Import Cookies dialog ──────────────────────────────────────────────── */

function ImportCookiesDialog({
  open,
  onClose,
  onConnected,
}: {
  open: boolean;
  onClose: () => void;
  onConnected: (email: string | null) => void;
}) {
  const [cookieJson, setCookieJson] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setError("");
    let cookies: unknown;
    try {
      cookies = JSON.parse(cookieJson.trim());
    } catch {
      setError("Invalid JSON — paste the full array exported by Cookie-Editor.");
      return;
    }
    if (!Array.isArray(cookies)) {
      setError("Expected a JSON array of cookies.");
      return;
    }
    setLoading(true);
    try {
      const res = await api<{ success: boolean; message: string; email?: string }>(
        "/api/notebooklm/auth/import-cookies",
        { method: "POST", body: { cookies } }
      );
      setCookieJson("");
      onConnected(res.email ?? null);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to import cookies");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect NotebookLM Session</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 pt-1">
          <ol className="space-y-2.5 text-[13px]">
            <li className="flex gap-2.5">
              <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">1</span>
              <span className="text-foreground/80">
                Install the{" "}
                <a href="https://cookie-editor.com" target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">Cookie-Editor</a>{" "}
                browser extension (Chrome or Firefox).
              </span>
            </li>
            <li className="flex gap-2.5">
              <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">2</span>
              <span className="text-foreground/80">
                Visit{" "}
                <a href="https://notebooklm.google.com" target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">notebooklm.google.com</a>{" "}
                and make sure you are signed in.
              </span>
            </li>
            <li className="flex gap-2.5">
              <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">3</span>
              <span className="text-foreground/80">Open Cookie-Editor → <strong>Export</strong> → <strong>Export as JSON</strong>, copy the result.</span>
            </li>
            <li className="flex gap-2.5">
              <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">4</span>
              <span className="text-foreground/80">Paste the JSON below and click Connect.</span>
            </li>
          </ol>
          <div>
            <label className="text-[13px] font-medium text-foreground mb-1.5 block">Cookie JSON</label>
            <textarea
              value={cookieJson}
              onChange={(e) => setCookieJson(e.target.value)}
              placeholder='[{"name": "SID", "value": "...", ...}]'
              rows={6}
              className="w-full rounded-md border border-border bg-muted/30 px-3 py-2 text-[12px] font-mono text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
            />
          </div>
          {error && (
            <p className="text-[12px] text-red-600 flex items-center gap-1.5">
              <span className="material-symbols-outlined text-[13px]">error</span>
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" onClick={onClose} disabled={loading}>Cancel</Button>
            <Button onClick={submit} disabled={loading || !cookieJson.trim()} className="gap-1.5">
              {loading ? (
                <><span className="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>Connecting…</>
              ) : (
                <><span className="material-symbols-outlined text-[14px]">login</span>Connect</>
              )}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── Create Notebook dialog ─────────────────────────────────────────────── */

function CreateNotebookDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (nb: NLMNotebookNative) => void;
}) {
  const [title, setTitle] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    if (!title.trim()) return;
    setLoading(true);
    setError("");
    try {
      const nb = await api<NLMNotebookNative>("/api/notebooklm/nlm/notebooks", {
        method: "POST",
        body: { title: title.trim() },
      });
      onCreated(nb);
      setTitle("");
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to create notebook");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>New Notebook</DialogTitle></DialogHeader>
        <div className="space-y-4 pt-2">
          <Input
            placeholder="My Research Notebook"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            autoFocus
          />
          {error && <p className="text-[12px] text-red-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" onClick={onClose} disabled={loading}>Cancel</Button>
            <Button onClick={submit} disabled={loading || !title.trim()}>
              {loading ? "Creating…" : "Create"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── Add Source dialog ──────────────────────────────────────────────────── */

type SourceKind = "url" | "text" | "file" | "drive";

const SOURCE_KIND_LABELS: Record<SourceKind, string> = {
  url: "URL / YouTube",
  text: "Paste text",
  file: "Upload file",
  drive: "Google Drive",
};

const ACCEPTED_FILE_TYPES = ".pdf,.docx,.doc,.md,.markdown,.txt,.csv,.epub,.jpg,.jpeg,.png";

function AddSourceDialog({
  notebookId,
  open,
  onClose,
  onAdded,
}: {
  notebookId: string;
  open: boolean;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [kind, setKind] = useState<SourceKind>("url");
  const [url, setUrl] = useState("");
  const [driveUrl, setDriveUrl] = useState("");
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const reset = () => { setUrl(""); setDriveUrl(""); setTitle(""); setContent(""); setFile(null); setError(""); };

  const submitJson = async (body: object) => {
    await api(`/api/notebooklm/nlm/notebooks/${notebookId}/sources`, { method: "POST", body });
  };

  const submitFile = async () => {
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    if (title.trim()) formData.append("title", title.trim());
    await apiUpload(`/api/notebooklm/nlm/notebooks/${notebookId}/sources/upload`, formData);
  };

  const submit = async () => {
    setLoading(true);
    setError("");
    try {
      if (kind === "url") {
        await submitJson({ kind: "url", url: url.trim() });
      } else if (kind === "text") {
        await submitJson({ kind: "text", title: title.trim() || "Pasted text", content: content.trim() });
      } else if (kind === "file") {
        await submitFile();
      } else if (kind === "drive") {
        await submitJson({ kind: "drive", url: driveUrl.trim(), title: title.trim() || undefined });
      }
      reset();
      onAdded();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to add source");
    } finally {
      setLoading(false);
    }
  };

  const isDisabled = loading || (
    kind === "url" ? !url.trim() :
    kind === "text" ? !content.trim() :
    kind === "file" ? !file :
    !driveUrl.trim()
  );

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) { reset(); onClose(); } }}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>Add Source</DialogTitle></DialogHeader>
        <div className="space-y-4 pt-2">
          {/* Kind selector */}
          <div className="grid grid-cols-4 gap-1.5">
            {(["url", "text", "file", "drive"] as SourceKind[]).map((k) => (
              <button
                key={k}
                onClick={() => { setKind(k); setError(""); }}
                className={cn(
                  "rounded-md py-1.5 text-[12px] font-medium transition-colors border",
                  kind === k
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-transparent text-muted-foreground border-border hover:bg-muted/40"
                )}
              >
                {SOURCE_KIND_LABELS[k]}
              </button>
            ))}
          </div>

          {kind === "url" && (
            <Input
              placeholder="https://example.com/article  or  YouTube URL"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              autoFocus
            />
          )}

          {kind === "text" && (
            <>
              <Input
                placeholder="Source title (optional)"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <textarea
                value={content}
                onChange={(e) => setContent(e.target.value)}
                placeholder="Paste your text here…"
                rows={8}
                className="w-full rounded-md border border-border bg-muted/30 px-3 py-2 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
              />
            </>
          )}

          {kind === "file" && (
            <div className="space-y-2.5">
              <input
                ref={fileInputRef}
                type="file"
                accept={ACCEPTED_FILE_TYPES}
                className="hidden"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
              <button
                onClick={() => fileInputRef.current?.click()}
                className="w-full rounded-md border-2 border-dashed border-border py-6 text-center text-[13px] text-muted-foreground hover:border-primary/40 hover:bg-muted/20 transition-colors"
              >
                {file ? (
                  <span className="text-foreground font-medium">{file.name}</span>
                ) : (
                  <>
                    <span className="material-symbols-outlined block text-[28px] mb-1 text-muted-foreground/40">upload_file</span>
                    Click to select a file
                    <span className="block text-[11px] mt-0.5 text-muted-foreground/60">PDF, DOCX, MD, TXT, CSV, EPUB, image</span>
                  </>
                )}
              </button>
              <Input
                placeholder="Display title (optional, defaults to filename)"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
            </div>
          )}

          {kind === "drive" && (
            <div className="space-y-2.5">
              <Input
                placeholder="https://docs.google.com/document/d/…"
                value={driveUrl}
                onChange={(e) => setDriveUrl(e.target.value)}
                autoFocus
              />
              <Input
                placeholder="Display title (optional)"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <p className="text-[11px] text-muted-foreground/70">
                Supports Google Docs, Slides, and Sheets. The notebook must be authorized to access the file.
              </p>
            </div>
          )}

          {error && <p className="text-[12px] text-red-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" onClick={() => { reset(); onClose(); }} disabled={loading}>Cancel</Button>
            <Button onClick={submit} disabled={isDisabled}>
              {loading ? "Adding…" : "Add Source"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── Generate Artifact dialog ───────────────────────────────────────────── */

const ARTIFACT_TYPES: { value: ArtifactType; label: string }[] = [
  { value: "audio",       label: "🎙 Podcast (Audio)" },
  { value: "video",       label: "🎬 Video Overview" },
  { value: "report",      label: "📄 Report" },
  { value: "quiz",        label: "❓ Quiz" },
  { value: "flashcards",  label: "🗂 Flashcards" },
  { value: "slide_deck",  label: "📊 Slide Deck" },
  { value: "infographic", label: "🖼 Infographic" },
  { value: "data_table",  label: "📋 Data Table" },
];

function GenerateDialog({
  notebookId,
  open,
  onClose,
  onGenerated,
}: {
  notebookId: string;
  open: boolean;
  onClose: () => void;
  onGenerated: () => void;
}) {
  const [artifactType, setArtifactType] = useState<ArtifactType>("audio");
  const [reportFormat, setReportFormat] = useState<ReportFormat>("briefing_doc");
  const [instructions, setInstructions] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const isCustomReport = artifactType === "report" && reportFormat === "custom";

  const submit = async () => {
    if (isCustomReport && !instructions.trim()) {
      setError("Custom report requires instructions.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      await api(`/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/generate`, {
        method: "POST",
        body: {
          artifact_type: artifactType,
          report_format: artifactType === "report" ? reportFormat : undefined,
          instructions: instructions.trim() || undefined,
        },
      });
      onGenerated();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to start generation");
    } finally {
      setLoading(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="max-w-md">
        <DialogHeader><DialogTitle>Generate Content</DialogTitle></DialogHeader>
        <div className="space-y-4 pt-2">
          <div>
            <label className="text-[13px] font-medium mb-1.5 block">Content type</label>
            <Select value={artifactType} onValueChange={(v) => setArtifactType(v as ArtifactType)}>
              <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ARTIFACT_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {artifactType === "report" && (
            <div>
              <label className="text-[13px] font-medium mb-1.5 block">Report format</label>
              <Select value={reportFormat} onValueChange={(v) => setReportFormat(v as ReportFormat)}>
                <SelectTrigger className="h-9"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="briefing_doc">Briefing Document</SelectItem>
                  <SelectItem value="study_guide">Study Guide</SelectItem>
                  <SelectItem value="blog_post">Blog Post</SelectItem>
                  <SelectItem value="custom">✏️ Custom (from instructions)</SelectItem>
                </SelectContent>
              </Select>
            </div>
          )}

          <div>
            <label className="text-[13px] font-medium mb-1.5 block">
              {isCustomReport ? "Instructions (required)" : "Custom instructions"}
              {!isCustomReport && (
                <span className="text-muted-foreground font-normal ml-1">— optional</span>
              )}
            </label>
            <textarea
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              rows={4}
              placeholder={
                isCustomReport
                  ? "Describe exactly what to generate, e.g. Tạo báo cáo chi tiết về quy trình vận hành, liệt kê từng bước và lưu ý quan trọng…"
                  : "e.g. Tập trung vào nội dung kỹ thuật, cực kỳ chi tiết, không bỏ sót thông tin nào…"
              }
              className="w-full rounded-md border border-input bg-background px-3 py-2 text-[13px] placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring resize-none"
            />
          </div>

          {error && <p className="text-[12px] text-red-600">{error}</p>}
          <div className="flex justify-end gap-2 pt-1">
            <Button variant="outline" onClick={onClose} disabled={loading}>Cancel</Button>
            <Button onClick={submit} disabled={loading}>
              {loading ? "Starting…" : "Generate"}
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── Status helpers ─────────────────────────────────────────────────────── */

function artifactStatusLabel(status: number): { label: string; className: string } {
  if (status === 1) return { label: "Generating", className: "bg-blue-100 text-blue-700" };
  if (status === 2) return { label: "Pending",    className: "bg-muted text-muted-foreground" };
  if (status === 3) return { label: "Done",       className: "bg-green-100 text-green-700" };
  if (status === 4) return { label: "Failed",     className: "bg-red-100 text-red-700" };
  return { label: String(status), className: "bg-muted text-muted-foreground" };
}

function sourceStatusDot(status: number) {
  if (status === 1) return <span className="w-1.5 h-1.5 rounded-full bg-blue-400 animate-pulse inline-block" />;
  if (status === 2) return <span className="w-1.5 h-1.5 rounded-full bg-green-500 inline-block" />;
  if (status === 3) return <span className="w-1.5 h-1.5 rounded-full bg-red-400 inline-block" />;
  return null;
}

/* ─── Sources tab ────────────────────────────────────────────────────────── */

function SourcesTab({
  notebookId,
  sources,
  loading,
  onRefresh,
}: {
  notebookId: string;
  sources: NLMSourceNative[];
  loading: boolean;
  onRefresh: () => void;
}) {
  const [showAdd, setShowAdd] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const handleDelete = async (sourceId: string) => {
    if (!confirm("Remove this source from the notebook?")) return;
    setDeletingId(sourceId);
    try {
      await api(`/api/notebooklm/nlm/notebooks/${notebookId}/sources/${sourceId}`, { method: "DELETE" });
      onRefresh();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex items-center justify-between px-6 py-3 border-b border-black/[0.06] shrink-0">
        <span className="text-[12px] text-muted-foreground">
          {sources.length} source{sources.length !== 1 ? "s" : ""}
        </span>
        <Button size="sm" className="gap-1.5 h-7 px-3 text-[12px]" onClick={() => setShowAdd(true)}>
          <span className="material-symbols-outlined text-[13px]">add</span>
          Add Source
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <span className="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
            Loading…
          </div>
        ) : sources.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <span className="material-symbols-outlined text-[40px] text-muted-foreground/20 mb-3">attach_file</span>
            <p className="text-[14px] text-muted-foreground font-medium">No sources yet</p>
            <p className="text-[12px] text-muted-foreground/60 mt-1">Add URLs or paste text to give the notebook context.</p>
            <Button variant="outline" size="sm" className="mt-4 gap-1.5" onClick={() => setShowAdd(true)}>
              <span className="material-symbols-outlined text-[13px]">add</span>
              Add Source
            </Button>
          </div>
        ) : (
          <div className="space-y-1.5">
            {sources.map((src) => (
              <div
                key={src.id}
                className="group flex items-center gap-3 rounded-lg border border-black/[0.06] bg-white px-3 py-2.5"
              >
                <span className="material-symbols-outlined text-[18px] text-muted-foreground/50 shrink-0" style={{ fontVariationSettings: "'FILL' 0, 'wght' 300" }}>
                  {src.url ? "link" : "article"}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    {sourceStatusDot(src.status)}
                    <p className="text-[13px] truncate text-foreground/90">
                      {src.title || src.url || src.id}
                    </p>
                  </div>
                  {src.url && (
                    <p className="text-[11px] text-muted-foreground truncate mt-0.5">{src.url}</p>
                  )}
                </div>
                <button
                  className="opacity-0 group-hover:opacity-100 shrink-0 text-muted-foreground/40 hover:text-red-500 transition-all disabled:opacity-30"
                  onClick={() => handleDelete(src.id)}
                  disabled={deletingId === src.id}
                  title="Remove source"
                >
                  <span className="material-symbols-outlined text-[15px]">delete</span>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      <AddSourceDialog
        notebookId={notebookId}
        open={showAdd}
        onClose={() => setShowAdd(false)}
        onAdded={() => { onRefresh(); setShowAdd(false); }}
      />
    </div>
  );
}

/* ─── Preview helpers ────────────────────────────────────────────────────── */

const TEXT_ARTIFACT_KINDS = new Set(["report", "quiz", "flashcards", "data_table"]);
const PREVIEWABLE_BINARY_KINDS = new Set(["audio", "video", "infographic", "slide_deck"]);

type QuizQuestion = { question: string; options: { text: string; correct: boolean }[]; hint: string };
type PreviewPayload =
  | { kind: "quiz";       title: string; questions: QuizQuestion[] }
  | { kind: "flashcards"; title: string; cards: { front: string; back: string }[] }
  | { kind: "report";     markdown: string }
  | { kind: "data_table"; csv: string };

function parseCSV(csv: string): { headers: string[]; rows: string[][] } {
  const lines = csv.trim().split("\n").filter(Boolean);
  const parse = (line: string) =>
    line.split(",").map((c) => c.trim().replace(/^"|"$/g, "").replace(/""/g, '"'));
  if (lines.length === 0) return { headers: [], rows: [] };
  return { headers: parse(lines[0]), rows: lines.slice(1).map(parse) };
}

function mdToHtml(md: string): string {
  return md
    .replace(/^#### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/^[-*] (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>\n?)+/g, (m) => `<ul>${m}</ul>`)
    .replace(/\n{2,}/g, "</p><p>")
    .replace(/^(?!<[hpuo]|<li)(.+)$/gm, "<p>$1</p>");
}

/* ─── Preview Dialog ─────────────────────────────────────────────────────── */

function PreviewDialog({
  notebookId,
  artifact,
  onClose,
}: {
  notebookId: string;
  artifact: NLMArtifactNative | null;
  onClose: () => void;
}) {
  const [previewData, setPreviewData] = useState<PreviewPayload | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // Quiz state
  const [quizIndex, setQuizIndex] = useState(0);
  const [revealed, setRevealed] = useState<Set<number>>(new Set());
  // Flashcard state
  const [cardIndex, setCardIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);

  useEffect(() => {
    if (!artifact) return;
    setPreviewData(null);
    setBlobUrl(null);
    setPreviewError(null);
    setQuizIndex(0);
    setRevealed(new Set());
    setCardIndex(0);
    setFlipped(false);
    setLoadingPreview(true);

    const load = async () => {
      try {
        if (TEXT_ARTIFACT_KINDS.has(artifact.kind)) {
          const data = await api<PreviewPayload>(
            `/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/${artifact.id}/preview-data`
          );
          setPreviewData(data);
        } else if (PREVIEWABLE_BINARY_KINDS.has(artifact.kind)) {
          const token = getToken();
          const base = process.env.NEXT_PUBLIC_API_URL ?? "";
          const resp = await fetch(
            `${base}/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/${artifact.id}/download`,
            { headers: token ? { Authorization: `Bearer ${token}` } : {} }
          );
          if (!resp.ok) throw new Error(`Download failed: HTTP ${resp.status}`);
          const mimeMap: Partial<Record<string, string>> = {
            audio: "audio/mpeg", video: "video/mp4", infographic: "image/png", slide_deck: "application/pdf",
          };
          const blob = new Blob([await resp.arrayBuffer()], {
            type: mimeMap[artifact.kind] ?? "application/octet-stream",
          });
          setBlobUrl(URL.createObjectURL(blob));
        }
      } catch (e: unknown) {
        setPreviewError(e instanceof Error ? e.message : "Preview failed");
      } finally {
        setLoadingPreview(false);
      }
    };
    load();
  }, [artifact, notebookId]);

  useEffect(() => {
    return () => { if (blobUrl) URL.revokeObjectURL(blobUrl); };
  }, [blobUrl]);

  if (!artifact) return null;

  const renderContent = () => {
    if (loadingPreview) {
      return (
        <div className="flex items-center justify-center py-16 gap-2 text-muted-foreground">
          <span className="material-symbols-outlined text-[16px] animate-spin">progress_activity</span>
          <span className="text-[13px]">Loading preview…</span>
        </div>
      );
    }
    if (previewError) {
      return <p className="text-[13px] text-red-600 py-6">{previewError}</p>;
    }

    /* ── Quiz ── */
    if (previewData?.kind === "quiz") {
      const q = previewData.questions[quizIndex];
      const isRevealed = revealed.has(quizIndex);
      if (!q) return null;
      return (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-[12px] text-muted-foreground">
            <span className="font-medium">{previewData.title}</span>
            <span>Question {quizIndex + 1} / {previewData.questions.length}</span>
          </div>
          <div className="flex gap-1 flex-wrap">
            {previewData.questions.map((_, i) => (
              <button key={i} onClick={() => { setQuizIndex(i); setRevealed(new Set()); }}
                className={cn("w-6 h-6 rounded text-[11px] font-semibold transition-colors",
                  i === quizIndex ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/70"
                )}>{i + 1}</button>
            ))}
          </div>
          <div className="rounded-xl border border-border bg-muted/20 px-5 py-4">
            <p className="text-[14px] font-medium leading-relaxed">{q.question}</p>
          </div>
          <div className="space-y-2">
            {q.options.map((opt, i) => (
              <div key={i} className={cn(
                "flex items-center gap-3 rounded-lg border px-4 py-2.5 text-[13px] transition-colors",
                isRevealed
                  ? opt.correct
                    ? "border-green-300 bg-green-50 text-green-800"
                    : "border-border/40 bg-muted/10 text-muted-foreground"
                  : "border-border bg-white"
              )}>
                <span className={cn("w-5 h-5 rounded-full border text-[11px] font-bold flex items-center justify-center shrink-0",
                  isRevealed && opt.correct ? "border-green-500 bg-green-500 text-white" : "border-border text-muted-foreground"
                )}>{String.fromCharCode(65 + i)}</span>
                <span className="flex-1">{opt.text}</span>
                {isRevealed && opt.correct && (
                  <span className="material-symbols-outlined text-[16px] text-green-600">check_circle</span>
                )}
              </div>
            ))}
          </div>
          {isRevealed && q.hint && (
            <div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3">
              <p className="text-[12px] font-semibold text-blue-700 mb-1">Hint / Explanation</p>
              <p className="text-[13px] text-blue-800 leading-relaxed">{q.hint}</p>
            </div>
          )}
          <div className="flex items-center justify-between gap-2 pt-1">
            <Button size="sm" variant="outline" className="h-8 px-3"
              disabled={quizIndex === 0}
              onClick={() => { setQuizIndex((i) => i - 1); setRevealed(new Set()); }}>
              <span className="material-symbols-outlined text-[14px]">arrow_back</span>
            </Button>
            {!isRevealed && (
              <Button size="sm" variant="outline" className="h-8 px-4 text-[12px]"
                onClick={() => setRevealed((prev) => new Set([...prev, quizIndex]))}>
                Reveal Answer
              </Button>
            )}
            <Button size="sm" variant="outline" className="h-8 px-3"
              disabled={quizIndex === previewData.questions.length - 1}
              onClick={() => { setQuizIndex((i) => i + 1); setRevealed(new Set()); }}>
              <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
            </Button>
          </div>
        </div>
      );
    }

    /* ── Flashcards ── */
    if (previewData?.kind === "flashcards") {
      const card = previewData.cards[cardIndex];
      if (!card) return null;
      return (
        <div className="space-y-4">
          <div className="flex items-center justify-between text-[12px] text-muted-foreground">
            <span className="font-medium">{previewData.title}</span>
            <span>{cardIndex + 1} / {previewData.cards.length}</span>
          </div>
          <button
            onClick={() => setFlipped((f) => !f)}
            className="w-full text-left focus:outline-none"
            style={{ perspective: "800px" }}
          >
            <div className={cn(
              "relative min-h-[200px] rounded-2xl border-2 p-7 flex items-center justify-center text-center transition-all duration-300",
              flipped
                ? "border-primary/40 bg-primary/5 shadow-md"
                : "border-border bg-gradient-to-b from-white to-muted/20 shadow-sm"
            )}>
              <div className="space-y-2">
                <p className={cn("text-[10px] font-bold uppercase tracking-widest mb-3",
                  flipped ? "text-primary/60" : "text-muted-foreground/50"
                )}>{flipped ? "ANSWER" : "QUESTION"}</p>
                <p className="text-[15px] leading-relaxed font-medium">
                  {flipped ? card.back : card.front}
                </p>
              </div>
            </div>
          </button>
          <p className="text-center text-[11px] text-muted-foreground/60">Click card to flip</p>
          <div className="flex items-center justify-between gap-2">
            <Button size="sm" variant="outline" className="h-8 px-3"
              disabled={cardIndex === 0}
              onClick={() => { setCardIndex((i) => i - 1); setFlipped(false); }}>
              <span className="material-symbols-outlined text-[14px]">arrow_back</span>
            </Button>
            <Button size="sm" variant="ghost" className="h-8 px-4 text-[12px]"
              onClick={() => setFlipped((f) => !f)}>
              <span className="material-symbols-outlined text-[14px] mr-1">flip</span>
              Flip
            </Button>
            <Button size="sm" variant="outline" className="h-8 px-3"
              disabled={cardIndex === previewData.cards.length - 1}
              onClick={() => { setCardIndex((i) => i + 1); setFlipped(false); }}>
              <span className="material-symbols-outlined text-[14px]">arrow_forward</span>
            </Button>
          </div>
        </div>
      );
    }

    /* ── Report (markdown) ── */
    if (previewData?.kind === "report") {
      return (
        <div
          className="prose prose-sm max-w-none overflow-y-auto rounded-lg border border-border bg-white px-6 py-5"
          style={{ maxHeight: "72vh" }}
          dangerouslySetInnerHTML={{ __html: mdToHtml(previewData.markdown) }}
        />
      );
    }

    /* ── Data table (CSV) ── */
    if (previewData?.kind === "data_table") {
      const { headers, rows } = parseCSV(previewData.csv);
      return (
        <div className="overflow-auto rounded-lg border border-border" style={{ maxHeight: "72vh" }}>
          <table className="w-full text-[12px] border-collapse">
            <thead className="sticky top-0 bg-muted z-10">
              <tr>
                {headers.map((h, i) => (
                  <th key={i} className="text-left px-3 py-2 border-b border-border font-semibold whitespace-nowrap">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, ri) => (
                <tr key={ri} className={ri % 2 === 0 ? "bg-white" : "bg-muted/20"}>
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-3 py-1.5 border-b border-border/30 align-top">{cell}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <p className="text-[13px] text-muted-foreground text-center py-6">No data rows</p>}
        </div>
      );
    }

    /* ── Audio ── */
    if (blobUrl && artifact.kind === "audio") {
      return (
        <div className="py-6 px-2 flex flex-col items-center gap-4">
          <span className="material-symbols-outlined text-[48px] text-primary/30">podcasts</span>
          <audio controls src={blobUrl} className="w-full" />
        </div>
      );
    }

    /* ── Video ── */
    if (blobUrl && artifact.kind === "video") {
      return <video controls src={blobUrl} className="w-full rounded-lg max-h-[58vh]" />;
    }

    /* ── Infographic ── */
    if (blobUrl && artifact.kind === "infographic") {
      return (
        <div className="overflow-auto flex items-center justify-center p-2" style={{ maxHeight: "74vh" }}>
          <img src={blobUrl} alt="Infographic" className="max-w-full object-contain rounded-lg" />
        </div>
      );
    }

    /* ── Slide deck (PDF) ── */
    if (blobUrl && artifact.kind === "slide_deck") {
      return (
        <iframe
          src={blobUrl}
          className="w-full rounded-lg border border-border"
          style={{ height: "74vh" }}
          title="Slide deck"
        />
      );
    }

    return (
      <p className="text-[13px] text-muted-foreground py-8 text-center">
        Preview not available for this artifact type.
      </p>
    );
  };

  return (
    <Dialog open={!!artifact} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="w-[92vw] max-w-5xl sm:max-w-5xl flex flex-col" style={{ maxHeight: "92vh" }}>
        <DialogHeader>
          <DialogTitle className="truncate pr-6">
            {artifact.title || ARTIFACT_LABELS[artifact.kind] || artifact.kind}
          </DialogTitle>
        </DialogHeader>
        <div className="flex-1 min-h-0 overflow-y-auto">
          {renderContent()}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/* ─── Studio tab ─────────────────────────────────────────────────────────── */

function StudioTab({
  notebookId,
  artifacts,
  loading,
  onRefresh,
}: {
  notebookId: string;
  artifacts: NLMArtifactNative[];
  loading: boolean;
  onRefresh: () => void;
}) {
  type IngestTrack = { sourceId: string; title: string; status: string };

  const [showGenerate, setShowGenerate] = useState(false);
  const [ingestingId, setIngestingId] = useState<string | null>(null);
  const [previewArtifact, setPreviewArtifact] = useState<NLMArtifactNative | null>(null);
  const [ingestTracks, setIngestTracks] = useState<Map<string, IngestTrack>>(new Map());
  const [reviewArtifactId, setReviewArtifactId] = useState<string | null>(null);
  const ingestTracksRef = useRef<Map<string, IngestTrack>>(new Map());
  const ingestPollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => { ingestTracksRef.current = ingestTracks; }, [ingestTracks]);

  useEffect(() => { setIngestTracks(new Map()); }, [notebookId]);

  useEffect(() => {
    if (ingestPollRef.current) clearInterval(ingestPollRef.current);
    const hasActive = [...ingestTracks.values()].some(
      (t) => t.status !== "ready" && t.status !== "error" && t.status !== "plan_ready"
    );
    if (!hasActive) return;
    ingestPollRef.current = setInterval(async () => {
      for (const [artId, track] of ingestTracksRef.current) {
        if (track.status === "ready" || track.status === "error" || track.status === "plan_ready") continue;
        try {
          const res = await api<{ status: string }>(`/api/sources/${track.sourceId}/progress`);
          setIngestTracks((prev) => {
            const next = new Map(prev);
            const t = next.get(artId);
            if (t) next.set(artId, { ...t, status: res.status });
            return next;
          });
        } catch { /* ignore */ }
      }
    }, 5000);
    return () => { if (ingestPollRef.current) clearInterval(ingestPollRef.current); };
  }, [ingestTracks]);

  const handleIngest = async (artifactId: string, title: string) => {
    setIngestingId(artifactId);
    try {
      const res = await api<{ source_id: string }>(
        `/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/${artifactId}/ingest`,
        { method: "POST" }
      );
      setIngestTracks((prev) =>
        new Map(prev).set(artifactId, { sourceId: res.source_id, title, status: "processing" })
      );
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Ingest failed");
    } finally {
      setIngestingId(null);
    }
  };

  const handleDownload = async (artifact: NLMArtifactNative) => {
    const token = getToken();
    const base = process.env.NEXT_PUBLIC_API_URL ?? "";
    const url = `${base}/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/${artifact.id}/download`;
    try {
      const resp = await fetch(url, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = blobUrl;
      a.download = artifact.title || artifact.kind;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Download failed");
    }
  };

  const reviewTrack = reviewArtifactId ? (ingestTracks.get(reviewArtifactId) ?? null) : null;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex items-center justify-between px-6 py-3 border-b border-black/[0.06] shrink-0">
        <span className="text-[12px] text-muted-foreground">
          {artifacts.length} artifact{artifacts.length !== 1 ? "s" : ""}
        </span>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="ghost" className="h-7 px-2 text-[12px] gap-1" onClick={onRefresh}>
            <span className="material-symbols-outlined text-[13px]">refresh</span>
          </Button>
          <Button size="sm" className="gap-1.5 h-7 px-3 text-[12px]" onClick={() => setShowGenerate(true)}>
            <span className="material-symbols-outlined text-[13px]">auto_awesome</span>
            Generate
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4">
        {loading ? (
          <div className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <span className="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
            Loading…
          </div>
        ) : artifacts.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center">
            <span className="material-symbols-outlined text-[40px] text-muted-foreground/20 mb-3">auto_awesome</span>
            <p className="text-[14px] text-muted-foreground font-medium">No content generated yet</p>
            <p className="text-[12px] text-muted-foreground/60 mt-1">Generate a podcast, report, quiz, and more.</p>
            <Button variant="outline" size="sm" className="mt-4 gap-1.5" onClick={() => setShowGenerate(true)}>
              <span className="material-symbols-outlined text-[13px]">add</span>
              Generate
            </Button>
          </div>
        ) : (
          <div className="space-y-2.5">
            {artifacts.map((art) => {
              const icon = ARTIFACT_ICONS[art.kind] ?? "article";
              const label = ARTIFACT_LABELS[art.kind] ?? art.kind;
              const { label: statusLabel, className: statusClass } = artifactStatusLabel(art.status);
              const isActive = art.status === 1 || art.status === 2;
              const isIngesting = ingestingId === art.id;
              const track = ingestTracks.get(art.id);

              return (
                <div key={art.id} className="flex items-start gap-3 rounded-lg border border-black/[0.06] bg-white p-3">
                  <span
                    className="material-symbols-outlined text-[22px] text-muted-foreground/60 shrink-0 mt-0.5"
                    style={{ fontVariationSettings: "'FILL' 0, 'wght' 300, 'GRAD' 0, 'opsz' 22" }}
                  >
                    {icon}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-[13px] font-medium">{label}</span>
                      {art.report_subtype && (
                        <span className="text-[11px] text-muted-foreground">
                          ({REPORT_FORMAT_LABELS[art.report_subtype as ReportFormat] ?? art.report_subtype})
                        </span>
                      )}
                      <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium", statusClass)}>
                        {statusLabel}
                      </span>
                      {isActive && (
                        <span className="material-symbols-outlined text-[14px] text-blue-500 animate-spin">progress_activity</span>
                      )}
                    </div>
                    {art.title && (
                      <p className="text-[12px] text-muted-foreground mt-0.5 truncate">{art.title}</p>
                    )}
                  </div>

                  {art.status === 3 && (
                    <div className="flex gap-1.5 shrink-0 flex-wrap justify-end">
                      {(TEXT_ARTIFACT_KINDS.has(art.kind) || PREVIEWABLE_BINARY_KINDS.has(art.kind)) && (
                        <Button
                          size="sm"
                          variant="outline"
                          className="h-7 px-2 text-[12px] gap-1"
                          onClick={() => setPreviewArtifact(art)}
                        >
                          <span className="material-symbols-outlined text-[13px]">visibility</span>
                          Preview
                        </Button>
                      )}
                      {(art.kind === "report" || art.kind === "slide_deck") && (
                        !track ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-[12px] gap-1"
                            onClick={() => handleIngest(art.id, art.title || label)}
                            disabled={isIngesting}
                          >
                            {isIngesting ? (
                              <span className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
                            ) : (
                              <span className="material-symbols-outlined text-[13px]" style={{ fontVariationSettings: "'FILL' 0" }}>library_add</span>
                            )}
                            Add to Wiki
                          </Button>
                        ) : track.status === "plan_ready" ? (
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-[12px] gap-1 border-amber-400 text-amber-700 hover:bg-amber-50"
                            onClick={() => setReviewArtifactId(art.id)}
                          >
                            <span className="material-symbols-outlined text-[13px]">fact_check</span>
                            Review Plan
                          </Button>
                        ) : track.status === "ready" ? (
                          <span className="inline-flex items-center gap-1 h-7 px-2 text-[12px] text-green-700 font-medium">
                            <span className="material-symbols-outlined text-[13px]">check_circle</span>
                            In Wiki
                          </span>
                        ) : track.status === "error" ? (
                          <span className="inline-flex items-center gap-1 h-7 px-2 text-[12px] text-red-600 font-medium">
                            <span className="material-symbols-outlined text-[13px]">error</span>
                            Failed
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 h-7 px-2 text-[12px] text-muted-foreground">
                            <span className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
                            Processing…
                          </span>
                        )
                      )}
                      {art.is_binary && (
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-7 px-2 text-[12px] gap-1"
                          onClick={() => handleDownload(art)}
                        >
                          <span className="material-symbols-outlined text-[13px]">download</span>
                          Download
                        </Button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      <GenerateDialog
        notebookId={notebookId}
        open={showGenerate}
        onClose={() => setShowGenerate(false)}
        onGenerated={onRefresh}
      />
      <PreviewDialog
        notebookId={notebookId}
        artifact={previewArtifact}
        onClose={() => setPreviewArtifact(null)}
      />
      {reviewTrack && (
        <PlanReviewDialog
          source={{ id: reviewTrack.sourceId, title: reviewTrack.title, status: reviewTrack.status, created_at: "" }}
          onClose={() => setReviewArtifactId(null)}
          onDone={() => {
            const artId = reviewArtifactId!;
            setReviewArtifactId(null);
            setIngestTracks((prev) => {
              const next = new Map(prev);
              const t = next.get(artId);
              if (t) next.set(artId, { ...t, status: "processing" });
              return next;
            });
          }}
        />
      )}
    </div>
  );
}

/* ─── Chat tab ───────────────────────────────────────────────────────────── */

function ChatTab({ notebookId, notebookTitle }: { notebookId: string; notebookTitle: string }) {
  type IngestTrack = { sourceId: string; status: string };

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [addingToWiki, setAddingToWiki] = useState(false);
  const [ingestTrack, setIngestTrack] = useState<IngestTrack | null>(null);
  const [reviewOpen, setReviewOpen] = useState(false);
  const ingestTrackRef = useRef<IngestTrack | null>(null);
  const ingestPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { ingestTrackRef.current = ingestTrack; }, [ingestTrack]);

  useEffect(() => { setIngestTrack(null); }, [notebookId]);

  useEffect(() => {
    if (ingestPollRef.current) clearInterval(ingestPollRef.current);
    if (!ingestTrack || ingestTrack.status === "ready" || ingestTrack.status === "error" || ingestTrack.status === "plan_ready") return;
    ingestPollRef.current = setInterval(async () => {
      const track = ingestTrackRef.current;
      if (!track || track.status === "ready" || track.status === "error" || track.status === "plan_ready") return;
      try {
        const res = await api<{ status: string }>(`/api/sources/${track.sourceId}/progress`);
        setIngestTrack((prev) => prev ? { ...prev, status: res.status } : null);
      } catch { /* ignore */ }
    }, 5000);
    return () => { if (ingestPollRef.current) clearInterval(ingestPollRef.current); };
  }, [ingestTrack]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const handleAddToWiki = async () => {
    if (messages.length === 0) return;
    setAddingToWiki(true);
    try {
      const lines: string[] = [`# Chat: ${notebookTitle}`, ""];
      for (const msg of messages) {
        if (msg.role === "user") {
          lines.push(`**Q:** ${msg.text}`, "");
        } else {
          lines.push(`**A:** ${msg.text}`, "");
        }
      }
      const res = await api<{ source_id: string }>(
        `/api/notebooklm/nlm/notebooks/${notebookId}/chat/ingest`,
        { method: "POST", body: { title: `Chat: ${notebookTitle}`, content: lines.join("\n") } }
      );
      setIngestTrack({ sourceId: res.source_id, status: "processing" });
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed to add to wiki");
    } finally {
      setAddingToWiki(false);
    }
  };

  const sendMessage = async () => {
    const q = input.trim();
    if (!q || sending) return;
    setInput("");
    setSending(true);

    const userMsg: ChatMessage = { id: genId(), role: "user", text: q };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await api<{
        answer: string;
        conversation_id: string;
        references: ChatReference[];
      }>(`/api/notebooklm/nlm/notebooks/${notebookId}/chat`, {
        method: "POST",
        body: { question: q, conversation_id: conversationId },
        timeoutMs: 120_000,
      });
      setConversationId(res.conversation_id);
      const assistantMsg: ChatMessage = {
        id: genId(),
        role: "assistant",
        text: res.answer,
        references: res.references,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (e: unknown) {
      const errMsg: ChatMessage = {
        id: genId(),
        role: "assistant",
        text: `Error: ${e instanceof Error ? e.message : "Failed to get answer"}`,
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      setSending(false);
    }
  };

  const wikiButton = messages.length > 0 && (
    !ingestTrack ? (
      <Button size="sm" variant="outline" className="h-7 px-2.5 text-[12px] gap-1.5"
        onClick={handleAddToWiki} disabled={addingToWiki}>
        {addingToWiki
          ? <span className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
          : <span className="material-symbols-outlined text-[13px]" style={{ fontVariationSettings: "'FILL' 0" }}>library_add</span>
        }
        Add to Wiki
      </Button>
    ) : ingestTrack.status === "plan_ready" ? (
      <Button size="sm" variant="outline"
        className="h-7 px-2.5 text-[12px] gap-1.5 border-amber-400 text-amber-700 hover:bg-amber-50"
        onClick={() => setReviewOpen(true)}>
        <span className="material-symbols-outlined text-[13px]">fact_check</span>
        Review Plan
      </Button>
    ) : ingestTrack.status === "ready" ? (
      <span className="inline-flex items-center gap-1 text-[12px] text-green-700 font-medium">
        <span className="material-symbols-outlined text-[13px]">check_circle</span>
        In Wiki
      </span>
    ) : ingestTrack.status === "error" ? (
      <span className="inline-flex items-center gap-1 text-[12px] text-red-600 font-medium">
        <span className="material-symbols-outlined text-[13px]">error</span>
        Failed
      </span>
    ) : (
      <span className="inline-flex items-center gap-1 text-[12px] text-muted-foreground">
        <span className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
        Processing…
      </span>
    )
  );

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex items-center justify-between px-6 py-2.5 border-b border-black/[0.06] shrink-0">
        <span className="text-[12px] text-muted-foreground">
          {messages.length > 0 ? `${messages.length} message${messages.length !== 1 ? "s" : ""}` : "Chat"}
        </span>
        {wikiButton}
      </div>

      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-12">
            <span className="material-symbols-outlined text-[40px] text-muted-foreground/20 mb-3">chat</span>
            <p className="text-[14px] text-muted-foreground font-medium">Ask anything about your sources</p>
            <p className="text-[12px] text-muted-foreground/60 mt-1">NotebookLM will answer based on the notebook sources.</p>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} className={cn("flex gap-3", msg.role === "user" ? "justify-end" : "justify-start")}>
              {msg.role === "assistant" && (
                <span className="material-symbols-outlined text-[18px] text-primary/60 shrink-0 mt-1">smart_toy</span>
              )}
              <div
                className={cn(
                  "max-w-[80%] rounded-2xl px-4 py-2.5 text-[13px]",
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground rounded-br-sm"
                    : "bg-black/[0.04] text-foreground rounded-bl-sm"
                )}
              >
                <p className="whitespace-pre-wrap leading-relaxed">{msg.text}</p>
                {msg.references && msg.references.length > 0 && (
                  <div className="mt-2 pt-2 border-t border-black/10 space-y-1">
                    {msg.references.slice(0, 3).map((ref, i) => (
                      <p key={i} className="text-[11px] text-foreground/50 line-clamp-2">
                        [{ref.citation_number ?? i + 1}] {ref.cited_text}
                      </p>
                    ))}
                  </div>
                )}
              </div>
              {msg.role === "user" && (
                <span className="material-symbols-outlined text-[18px] text-muted-foreground/40 shrink-0 mt-1">person</span>
              )}
            </div>
          ))
        )}
        {sending && (
          <div className="flex gap-3 justify-start">
            <span className="material-symbols-outlined text-[18px] text-primary/60 shrink-0 mt-1">smart_toy</span>
            <div className="bg-black/[0.04] rounded-2xl rounded-bl-sm px-4 py-2.5">
              <span className="material-symbols-outlined text-[14px] animate-spin text-muted-foreground">progress_activity</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 px-6 py-3 border-t border-black/[0.06] flex items-end gap-2">
        {conversationId && (
          <button
            className="shrink-0 text-muted-foreground/50 hover:text-muted-foreground text-[11px] underline underline-offset-2"
            onClick={() => { setMessages([]); setConversationId(null); }}
            title="Start a new conversation"
          >
            New chat
          </button>
        )}
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
          }}
          placeholder="Ask a question… (Enter to send, Shift+Enter for new line)"
          rows={2}
          className="flex-1 rounded-xl border border-border bg-white px-4 py-2.5 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
          disabled={sending}
        />
        <Button
          size="sm"
          className="h-9 w-9 p-0 shrink-0"
          onClick={sendMessage}
          disabled={sending || !input.trim()}
        >
          <span className="material-symbols-outlined text-[16px]">send</span>
        </Button>
      </div>

      {reviewOpen && ingestTrack && (
        <PlanReviewDialog
          source={{ id: ingestTrack.sourceId, title: `Chat: ${notebookTitle}`, status: ingestTrack.status, created_at: "" }}
          onClose={() => setReviewOpen(false)}
          onDone={() => {
            setReviewOpen(false);
            setIngestTrack((prev) => prev ? { ...prev, status: "processing" } : null);
          }}
        />
      )}
    </div>
  );
}

/* ─── Main page ──────────────────────────────────────────────────────────── */

function isSessionExpired(e: unknown): boolean {
  return e instanceof ApiError && e.status === 401;
}

type Tab = "sources" | "studio" | "chat";

export default function NotebookLMPage() {
  const [notebooks, setNotebooks] = useState<NLMNotebookNative[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("sources");

  const [sources, setSources] = useState<NLMSourceNative[]>([]);
  const [artifacts, setArtifacts] = useState<NLMArtifactNative[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [artifactsLoading, setArtifactsLoading] = useState(false);

  const [loading, setLoading] = useState(true);
  const [authOk, setAuthOk] = useState<boolean | null>(null);
  const [authEmail, setAuthEmail] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<number | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const selectedNotebook = notebooks.find((n) => n.id === selectedId) ?? null;

  /* ── Auth ── */
  const checkAuth = useCallback(async () => {
    try {
      const r = await api<{ authenticated: boolean; email?: string | null; last_refreshed?: number | null }>("/api/notebooklm/auth/status");
      setAuthOk(r.authenticated);
      setAuthEmail(r.email ?? null);
      if (r.last_refreshed) setLastRefreshed(r.last_refreshed);
    } catch {
      setAuthOk(false);
      setAuthEmail(null);
    }
  }, []);

  const handleRefreshSession = async () => {
    setRefreshing(true);
    try {
      const r = await api<{ success: boolean; message: string; last_refreshed?: number }>("/api/notebooklm/auth/refresh", { method: "POST" });
      if (r.success && r.last_refreshed) setLastRefreshed(r.last_refreshed);
    } catch {
      // ignore
    } finally {
      setRefreshing(false);
    }
  };

  useEffect(() => { checkAuth(); }, [checkAuth]);

  const handleDisconnect = async () => {
    if (!confirm("Disconnect the NotebookLM session?")) return;
    setDisconnecting(true);
    try {
      await api("/api/notebooklm/auth/session", { method: "DELETE" });
      setAuthOk(false);
      setAuthEmail(null);
      setNotebooks([]);
      setSelectedId(null);
    } catch {
      // ignore
    } finally {
      setDisconnecting(false);
    }
  };

  /* ── Notebooks ── */
  const loadNotebooks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api<NLMNotebookNative[]>("/api/notebooklm/nlm/notebooks");
      setNotebooks(data);
      if (data.length > 0 && !selectedId) setSelectedId(data[0].id);
    } catch (e: unknown) {
      if (isSessionExpired(e)) setAuthOk(false);
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  useEffect(() => {
    if (authOk) loadNotebooks();
    else if (authOk === false) setLoading(false);
  }, [authOk]); // eslint-disable-line react-hooks/exhaustive-deps

  const handleDeleteNotebook = async (nbId: string) => {
    if (!confirm("Delete this notebook from NotebookLM?")) return;
    try {
      await api(`/api/notebooklm/nlm/notebooks/${nbId}`, { method: "DELETE" });
      setNotebooks((prev) => prev.filter((n) => n.id !== nbId));
      if (selectedId === nbId) setSelectedId(null);
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  };

  /* ── Sources ── */
  const loadSources = useCallback(async (nbId: string) => {
    setSourcesLoading(true);
    try {
      const data = await api<NLMSourceNative[]>(`/api/notebooklm/nlm/notebooks/${nbId}/sources`);
      setSources(data);
    } catch (e: unknown) {
      if (isSessionExpired(e)) setAuthOk(false);
      else setSources([]);
    } finally {
      setSourcesLoading(false);
    }
  }, []);

  /* ── Artifacts ── */
  const loadArtifacts = useCallback(async (nbId: string, silent = false) => {
    if (!silent) setArtifactsLoading(true);
    try {
      const data = await api<NLMArtifactNative[]>(`/api/notebooklm/nlm/notebooks/${nbId}/artifacts`);
      setArtifacts(data);
    } catch (e: unknown) {
      if (isSessionExpired(e)) setAuthOk(false);
      else if (!silent) setArtifacts([]);
    } finally {
      if (!silent) setArtifactsLoading(false);
    }
  }, []);

  /* ── Load data when notebook or tab changes ── */
  useEffect(() => {
    if (!selectedId) { setSources([]); setArtifacts([]); return; }
    if (tab === "sources") loadSources(selectedId);
    if (tab === "studio") loadArtifacts(selectedId);
  }, [selectedId, tab, loadSources, loadArtifacts]);

  /* ── Poll artifacts while any are in-progress ── */
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!selectedId || tab !== "studio") return;
    const hasActive = artifacts.some((a) => a.status === 1 || a.status === 2);
    if (!hasActive) return;
    pollRef.current = setInterval(() => loadArtifacts(selectedId, true), 5000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [artifacts, selectedId, tab, loadArtifacts]);

  return (
    <div className="flex flex-col h-full">
      <PageHeader
        title="NotebookLM"
        description="Browse and use your Google NotebookLM notebooks"
        action={
          authOk && (
            <Button size="sm" onClick={() => setShowCreate(true)} className="gap-1.5">
              <span className="material-symbols-outlined text-[15px]" style={{ fontVariationSettings: "'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 15" }}>add</span>
              New Notebook
            </Button>
          )
        }
      />

      {/* Session status bar */}
      <div className="mx-6 mt-3 mb-1">
        {authOk === null ? null : authOk ? (
          <div className="flex items-center gap-3 rounded-lg bg-green-50 border border-green-200 px-4 py-2.5">
            <span className="material-symbols-outlined text-[16px] text-green-600">verified_user</span>
            <div className="flex-1 min-w-0">
              <span className="text-[13px] font-medium text-green-800">NotebookLM connected</span>
              {authEmail && <span className="text-[12px] text-green-600 ml-2">{authEmail}</span>}
              {lastRefreshed && (
                <span className="text-[11px] text-green-500 ml-2">
                  · refreshed {Math.round((Date.now() / 1000 - lastRefreshed) / 60)}m ago
                </span>
              )}
            </div>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2.5 text-[12px] text-green-700 hover:bg-green-100 gap-1"
              onClick={handleRefreshSession}
              disabled={refreshing}
              title="Refresh session cookies"
            >
              <span className={`material-symbols-outlined text-[13px] ${refreshing ? "animate-spin" : ""}`}>refresh</span>
              Refresh
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2.5 text-[12px] text-green-700 hover:text-red-600 hover:bg-red-50 gap-1"
              onClick={handleDisconnect}
              disabled={disconnecting}
            >
              {disconnecting
                ? <span className="material-symbols-outlined text-[13px] animate-spin">progress_activity</span>
                : <span className="material-symbols-outlined text-[13px]">logout</span>
              }
              Disconnect
            </Button>
          </div>
        ) : (
          <div className="flex items-center gap-3 rounded-lg bg-amber-50 border border-amber-200 px-4 py-2.5">
            <span className="material-symbols-outlined text-[16px] text-amber-600">warning</span>
            <div className="flex-1 min-w-0">
              <span className="text-[13px] font-medium text-amber-800">No active NotebookLM session</span>
              <span className="text-[12px] text-amber-700 ml-2">Import your Google cookies to connect.</span>
            </div>
            <Button size="sm" className="h-7 px-3 text-[12px] gap-1.5 shrink-0" onClick={() => setShowImport(true)}>
              <span className="material-symbols-outlined text-[13px]">login</span>
              Connect
            </Button>
          </div>
        )}
      </div>

      <div className="flex flex-1 min-h-0">
        {/* ── Notebook list (left panel) ── */}
        <div className="w-64 shrink-0 border-r border-black/[0.06] flex flex-col">
          <div className="px-4 py-3 border-b border-black/[0.06]">
            <p className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/60">Notebooks</p>
          </div>
          <div className="flex-1 overflow-y-auto py-1">
            {loading ? (
              <div className="flex items-center gap-2 px-4 py-3 text-[13px] text-muted-foreground">
                <span className="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>
                Loading…
              </div>
            ) : !authOk ? (
              <div className="px-4 py-6 text-center">
                <span className="material-symbols-outlined text-[32px] text-muted-foreground/30 block mb-2">lock</span>
                <p className="text-[12px] text-muted-foreground">Connect to view notebooks</p>
              </div>
            ) : notebooks.length === 0 ? (
              <div className="px-4 py-6 text-center">
                <span className="material-symbols-outlined text-[32px] text-muted-foreground/30 block mb-2">book_2</span>
                <p className="text-[13px] text-muted-foreground">No notebooks yet</p>
                <Button variant="outline" size="sm" className="mt-3" onClick={() => setShowCreate(true)}>
                  Create one
                </Button>
              </div>
            ) : (
              notebooks.map((nb) => (
                <button
                  key={nb.id}
                  onClick={() => { setSelectedId(nb.id); setTab("sources"); }}
                  className={cn(
                    "group w-full flex items-start gap-2.5 px-3 py-2.5 text-left transition-colors",
                    selectedId === nb.id ? "bg-black/[0.04]" : "hover:bg-black/[0.02]"
                  )}
                >
                  <span
                    className="material-symbols-outlined text-[18px] text-muted-foreground/50 shrink-0 mt-0.5"
                    style={{ fontVariationSettings: selectedId === nb.id ? "'FILL' 1" : "'FILL' 0, 'wght' 300, 'GRAD' 0, 'opsz' 18" }}
                  >
                    book_2
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className={cn("text-[13px] truncate", selectedId === nb.id ? "font-semibold text-foreground" : "text-foreground/80")}>
                      {nb.title}
                    </p>
                    <p className="text-[11px] text-muted-foreground mt-0.5">
                      {nb.sources_count} source{nb.sources_count !== 1 ? "s" : ""}
                    </p>
                  </div>
                  <button
                    className="opacity-0 group-hover:opacity-100 shrink-0 text-muted-foreground/40 hover:text-red-500 transition-all"
                    onClick={(e) => { e.stopPropagation(); handleDeleteNotebook(nb.id); }}
                    title="Delete notebook"
                  >
                    <span className="material-symbols-outlined text-[15px]">delete</span>
                  </button>
                </button>
              ))
            )}
          </div>
        </div>

        {/* ── Right panel ── */}
        <div className="flex-1 flex flex-col min-h-0 min-w-0">
          {!selectedNotebook ? (
            <div className="flex-1 flex items-center justify-center text-center px-8">
              <div>
                <span className="material-symbols-outlined text-[48px] text-muted-foreground/20 block mb-3">book_2</span>
                <p className="text-[14px] text-muted-foreground">
                  {authOk ? "Select a notebook to get started" : "Connect your NotebookLM session to begin"}
                </p>
              </div>
            </div>
          ) : (
            <>
              {/* Notebook header + tabs */}
              <div className="shrink-0 border-b border-black/[0.06]">
                <div className="px-6 pt-3 pb-0">
                  <h2 className="text-[15px] font-semibold truncate">{selectedNotebook.title}</h2>
                </div>
                <div className="flex gap-0 px-6 mt-2">
                  {(["sources", "studio", "chat"] as Tab[]).map((t) => (
                    <button
                      key={t}
                      onClick={() => setTab(t)}
                      className={cn(
                        "px-4 py-2 text-[13px] font-medium border-b-2 transition-colors capitalize",
                        tab === t
                          ? "border-primary text-primary"
                          : "border-transparent text-muted-foreground hover:text-foreground"
                      )}
                    >
                      {t === "studio" ? "Studio" : t.charAt(0).toUpperCase() + t.slice(1)}
                    </button>
                  ))}
                </div>
              </div>

              {/* Tab content */}
              {tab === "sources" && (
                <SourcesTab
                  notebookId={selectedId!}
                  sources={sources}
                  loading={sourcesLoading}
                  onRefresh={() => loadSources(selectedId!)}
                />
              )}
              {tab === "studio" && (
                <StudioTab
                  notebookId={selectedId!}
                  artifacts={artifacts}
                  loading={artifactsLoading}
                  onRefresh={() => loadArtifacts(selectedId!)}
                />
              )}
              {tab === "chat" && (
                <ChatTab notebookId={selectedId!} notebookTitle={selectedNotebook?.title ?? ""} />
              )}
            </>
          )}
        </div>
      </div>

      <ImportCookiesDialog
        open={showImport}
        onClose={() => setShowImport(false)}
        onConnected={(email) => {
          setAuthOk(true);
          setAuthEmail(email);
          loadNotebooks();
        }}
      />
      <CreateNotebookDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(nb) => {
          setNotebooks((prev) => [nb, ...prev]);
          setSelectedId(nb.id);
          setTab("sources");
        }}
      />
    </div>
  );
}
