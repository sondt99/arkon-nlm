"use client";

// genId() requires a secure context (HTTPS/localhost); fall back for plain HTTP
function genId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
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

async function copyChatText(text: string) {
  if (navigator.clipboard?.writeText) return navigator.clipboard.writeText(text);
  const node = document.createElement("textarea");
  node.value = text;
  node.style.position = "fixed";
  node.style.opacity = "0";
  document.body.appendChild(node);
  node.select();
  document.execCommand("copy");
  node.remove();
}

function ChatCopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        await copyChatText(text);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      }}
      className="inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-[10px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
      aria-label={copied ? "Copied" : label}
    >
      {copied ? <Check className="size-3" /> : <Copy className="size-3" />}
      {copied ? "Copied" : label}
    </button>
  );
}

function NotebookChatMarkdown({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-2 leading-6 last:mb-0">{children}</p>,
        h1: ({ children }) => <h1 className="mb-2 mt-4 text-lg font-bold first:mt-0">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-2 mt-4 text-base font-bold first:mt-0">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1.5 mt-3 font-semibold first:mt-0">{children}</h3>,
        ul: ({ children }) => <ul className="mb-2 ml-5 list-disc space-y-1">{children}</ul>,
        ol: ({ children }) => <ol className="mb-2 ml-5 list-decimal space-y-1">{children}</ol>,
        a: ({ href, children }) => <a href={href} target="_blank" rel="noreferrer" className="text-primary underline underline-offset-2">{children}</a>,
        code: ({ className, children }) => {
          const code = String(children).replace(/\n$/, "");
          if (!className) return <code className="rounded bg-primary/10 px-1 py-0.5 font-mono text-xs text-primary">{children}</code>;
          return (
            <div className="my-3 overflow-hidden rounded-xl border border-emerald-400/20 bg-[#06110d]">
              <div className="flex items-center justify-between border-b border-white/10 px-3 py-1.5 text-emerald-200/70">
                <span className="font-mono text-[9px] uppercase tracking-widest">{className.replace("language-", "") || "code"}</span>
                <ChatCopyButton text={code} label="Copy code" />
              </div>
              <pre className="overflow-x-auto p-3"><code className="font-mono text-xs leading-6 text-emerald-50">{code}</code></pre>
            </div>
          );
        },
        pre: ({ children }) => <>{children}</>,
        blockquote: ({ children }) => <blockquote className="my-2 border-l-2 border-primary/50 pl-3 text-muted-foreground">{children}</blockquote>,
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

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
  const [mode, setMode] = useState<"master" | "cookies">("master");
  const [cookieJson, setCookieJson] = useState("");
  const [masterJson, setMasterJson] = useState("");
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
      const res = await api<{ success: boolean; verified?: boolean; message: string; email?: string }>(
        "/api/notebooklm/auth/import-cookies",
        { method: "POST", body: { cookies } }
      );
      // The server now live-verifies the cookies. Only treat it as connected when
      // Google actually accepted them; otherwise show why (e.g. Chrome DBSC → use Firefox).
      if (res.verified === false) {
        setError(res.message || "Cookies were saved but Google rejected them.");
        return;
      }
      setCookieJson("");
      onConnected(res.email ?? null);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to import cookies");
    } finally {
      setLoading(false);
    }
  };

  const submitMaster = async () => {
    setError("");
    let parsed: { master_token?: string; email?: string; android_id?: string };
    try {
      parsed = JSON.parse(masterJson.trim());
    } catch {
      setError("Invalid JSON — paste the whole master_token.json file.");
      return;
    }
    if (!parsed.master_token || !parsed.email || !parsed.android_id) {
      setError("master_token.json must contain master_token, email and android_id.");
      return;
    }
    setLoading(true);
    try {
      const res = await api<{ success: boolean; verified?: boolean; message: string }>(
        "/api/notebooklm/auth/master-token",
        { method: "POST", body: { master_token: parsed.master_token, email: parsed.email, android_id: parsed.android_id } }
      );
      if (res.verified === false) {
        setError(res.message || "Master token was saved but Google rejected it.");
        return;
      }
      setMasterJson("");
      onConnected(parsed.email ?? null);
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to install master token");
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

        {/* Mode switch: master token (headless, recommended for servers) vs raw cookies */}
        <div className="flex items-center gap-1 mb-1 border-b border-border">
          {([["master", "Master token"], ["cookies", "Cookies"]] as const).map(([m, label]) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(""); }}
              className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                mode === m ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              {label}
              {m === "master" && <span className="ml-1.5 text-[10px] text-primary/70">recommended</span>}
            </button>
          ))}
        </div>

        {mode === "master" ? (
          <div className="space-y-4 pt-1">
            <div className="rounded-md bg-primary/5 border border-primary/20 px-3 py-2.5 text-[12px] text-foreground/80 space-y-1.5">
              <p>
                A <strong>master token</strong> lets this server mint NotebookLM sessions on its own —
                it survives cookie expiry and works where cookie import is blocked (device-bound / DBSC).
              </p>
              <p className="text-amber-700">
                ⚠ It is a <strong>full-account, long-lived</strong> credential (survives password changes).
                Use a <strong>dedicated / throwaway Google account</strong>, never your primary one.
              </p>
            </div>
            <ol className="space-y-2.5 text-[13px]">
              <li className="flex gap-2.5">
                <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">1</span>
                <span className="text-foreground/80">On any machine with a browser: <code className="text-[11px] bg-muted px-1 rounded">pip install &quot;notebooklm-py[headless]&quot;</code></span>
              </li>
              <li className="flex gap-2.5">
                <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">2</span>
                <span className="text-foreground/80">Run <code className="text-[11px] bg-muted px-1 rounded">notebooklm login --master-token</code> and sign in with the dedicated account (one time).</span>
              </li>
              <li className="flex gap-2.5">
                <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">3</span>
                <span className="text-foreground/80">Open the generated <code className="text-[11px] bg-muted px-1 rounded">master_token.json</code> and paste its contents below.</span>
              </li>
            </ol>
            <div>
              <label className="text-[13px] font-medium text-foreground mb-1.5 block">master_token.json</label>
              <textarea
                value={masterJson}
                onChange={(e) => setMasterJson(e.target.value)}
                placeholder='{"master_token": "aas_et/...", "email": "...", "android_id": "..."}'
                rows={5}
                className="w-full rounded-md border border-border bg-muted/30 px-3 py-2 text-[12px] font-mono text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
              />
            </div>
            {error && (
              <p className="text-[12px] text-red-600 flex items-start gap-1.5">
                <span className="material-symbols-outlined text-[13px] mt-0.5">error</span>
                {error}
              </p>
            )}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" onClick={onClose} disabled={loading}>Cancel</Button>
              <Button onClick={submitMaster} disabled={loading || !masterJson.trim()} className="gap-1.5">
                {loading ? (
                  <><span className="material-symbols-outlined text-[14px] animate-spin">progress_activity</span>Connecting…</>
                ) : (
                  <><span className="material-symbols-outlined text-[14px]">key</span>Install &amp; connect</>
                )}
              </Button>
            </div>
          </div>
        ) : (
        <div className="space-y-4 pt-1">
          <ol className="space-y-2.5 text-[13px]">
            <li className="flex gap-2.5">
              <span className="flex-none w-5 h-5 rounded-full bg-primary/10 text-primary text-[11px] font-semibold flex items-center justify-center mt-0.5">1</span>
              <span className="text-foreground/80">
                Install the{" "}
                <a href="https://cookie-editor.com" target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-2">Cookie-Editor</a>{" "}
                extension. Note: Google now blocks server-side cookie replay for many
                accounts (device-bound sessions / Workspace policy); if import keeps failing,
                a personal account without Advanced Protection is most likely to work.
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
        )}
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

/** Wiki-ingest statuses that no longer move on their own — stop polling on these. */
function isIngestSettled(status: string): boolean {
  return status === "ready" || status === "error" || status === "plan_ready";
}

/* ─── Load failure panel ─────────────────────────────────────────────────────
   A failed load must never fall through to an empty state: "no notebooks yet" /
   "no sources yet" reads as an answer, and the user acts on it (creates a
   duplicate, or walks away) instead of retrying. */

function LoadErrorPanel({
  title,
  message,
  onRetry,
}: {
  title: string;
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex flex-col items-center justify-center px-4 py-8 text-center">
      <span className="material-symbols-outlined text-[36px] text-red-400/70 mb-3">cloud_off</span>
      <p className="text-[13px] font-medium text-foreground">{title}</p>
      <p className="text-[12px] text-muted-foreground/80 mt-1 max-w-sm break-words">{message}</p>
      <Button variant="outline" size="sm" className="mt-4 gap-1.5" onClick={onRetry}>
        <span className="material-symbols-outlined text-[13px]">refresh</span>
        Retry
      </Button>
    </div>
  );
}

/* ─── Sources tab ────────────────────────────────────────────────────────── */

function SourcesTab({
  notebookId,
  sources,
  loading,
  error,
  onRefresh,
}: {
  notebookId: string;
  sources: NLMSourceNative[];
  loading: boolean;
  error: string | null;
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
      <div className="flex items-center justify-between px-6 py-3 border-b border-border shrink-0">
        <span className="text-[12px] text-muted-foreground">
          {error ? "Sources unavailable" : `${sources.length} source${sources.length !== 1 ? "s" : ""}`}
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
        ) : error ? (
          <LoadErrorPanel title="Could not load sources" message={error} onRetry={onRefresh} />
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
                className="group flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2.5"
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

    // Previews are opened one after another and a report can take far longer than a
    // video. Without this, the earlier response still lands: it kills the spinner and
    // paints the previous artifact's content inside the current artifact's dialog.
    const controller = new AbortController();
    let objectUrl: string | null = null;

    const load = async () => {
      try {
        if (TEXT_ARTIFACT_KINDS.has(artifact.kind)) {
          const data = await api<PreviewPayload>(
            `/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/${artifact.id}/preview-data`,
            { signal: controller.signal }
          );
          if (controller.signal.aborted) return;
          setPreviewData(data);
        } else if (PREVIEWABLE_BINARY_KINDS.has(artifact.kind)) {
          const token = getToken();
          const base = process.env.NEXT_PUBLIC_API_URL ?? "";
          const resp = await fetch(
            `${base}/api/notebooklm/nlm/notebooks/${notebookId}/artifacts/${artifact.id}/download`,
            { headers: token ? { Authorization: `Bearer ${token}` } : {}, signal: controller.signal }
          );
          if (!resp.ok) throw new Error(`Download failed: HTTP ${resp.status}`);
          const buffer = await resp.arrayBuffer();
          if (controller.signal.aborted) return;
          const mimeMap: Partial<Record<string, string>> = {
            audio: "audio/mpeg", video: "video/mp4", infographic: "image/png", slide_deck: "application/pdf",
          };
          const blob = new Blob([buffer], {
            type: mimeMap[artifact.kind] ?? "application/octet-stream",
          });
          objectUrl = URL.createObjectURL(blob);
          setBlobUrl(objectUrl);
        }
      } catch (e: unknown) {
        if (controller.signal.aborted) return;
        setPreviewError(e instanceof Error ? e.message : "Preview failed");
      } finally {
        if (!controller.signal.aborted) setLoadingPreview(false);
      }
    };
    load();

    return () => {
      controller.abort();
      // A blob created in the same tick the dialog closed never reaches state, so the
      // [blobUrl] cleanup below would never free it — revoke it here instead.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
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
                  : "border-border bg-card"
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
          className="prose prose-sm max-w-none overflow-y-auto rounded-lg border border-border bg-card px-6 py-5 dark:prose-invert"
          style={{ maxHeight: "72vh" }}
        >
          <NotebookChatMarkdown text={previewData.markdown} />
        </div>
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
                <tr key={ri} className={ri % 2 === 0 ? "bg-card" : "bg-muted/20"}>
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
  error,
  onRefresh,
}: {
  notebookId: string;
  artifacts: NLMArtifactNative[];
  loading: boolean;
  error: string | null;
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
    const hasActive = [...ingestTracks.values()].some((t) => !isIngestSettled(t.status));
    if (!hasActive) return;
    ingestPollRef.current = setInterval(async () => {
      for (const [artId, track] of ingestTracksRef.current) {
        if (isIngestSettled(track.status)) continue;
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
      <div className="flex items-center justify-between px-6 py-3 border-b border-border shrink-0">
        <span className="text-[12px] text-muted-foreground">
          {error ? "Content unavailable" : `${artifacts.length} artifact${artifacts.length !== 1 ? "s" : ""}`}
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
        ) : error ? (
          <LoadErrorPanel title="Could not load Studio content" message={error} onRetry={onRefresh} />
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
                <div key={art.id} className="flex items-start gap-3 rounded-lg border border-border bg-card p-3">
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

/* ─── Chat state ─────────────────────────────────────────────────────────────
   Owned by the page, not by `ChatTab`, because `ChatTab` unmounts on every tab
   switch. While the tab owned this state, stepping over to Sources threw away the
   transcript *and* `conversationId` — the NotebookLM thread handle — so the next
   question silently opened a brand-new conversation with no memory of the earlier
   turns, and an answer still in flight (a turn can take 120 s) was written to a
   dead component and lost. */

type ChatIngestTrack = { sourceId: string; status: string };

function useNotebookChat(notebookId: string | null, notebookTitle: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [addingToWiki, setAddingToWiki] = useState(false);
  const [ingestTrack, setIngestTrack] = useState<ChatIngestTrack | null>(null);
  const ingestTrackRef = useRef<ChatIngestTrack | null>(null);
  const ingestPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Now that a turn outlives the tab, it can also outlive the notebook it was
  // asked in. Its answer and thread handle belong to that notebook only, so a
  // late reply must never land in whichever notebook the user moved on to.
  const activeNotebookRef = useRef(notebookId);

  useEffect(() => { ingestTrackRef.current = ingestTrack; }, [ingestTrack]);

  useEffect(() => {
    activeNotebookRef.current = notebookId;
    setIngestTrack(null);
    setMessages([]);
    setConversationId(null);
    setInput("");
    setSending(false);
    setAddingToWiki(false);
  }, [notebookId]);

  useEffect(() => {
    if (ingestPollRef.current) clearInterval(ingestPollRef.current);
    if (!ingestTrack || isIngestSettled(ingestTrack.status)) return;
    ingestPollRef.current = setInterval(async () => {
      const track = ingestTrackRef.current;
      if (!track || isIngestSettled(track.status)) return;
      try {
        const res = await api<{ status: string }>(`/api/sources/${track.sourceId}/progress`);
        setIngestTrack((prev) => prev ? { ...prev, status: res.status } : null);
      } catch { /* ignore */ }
    }, 5000);
    return () => { if (ingestPollRef.current) clearInterval(ingestPollRef.current); };
  }, [ingestTrack]);

  const addToWiki = useCallback(async () => {
    if (!notebookId || messages.length === 0) return;
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
      if (activeNotebookRef.current !== notebookId) return;
      setIngestTrack({ sourceId: res.source_id, status: "processing" });
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Failed to add to wiki");
    } finally {
      if (activeNotebookRef.current === notebookId) setAddingToWiki(false);
    }
  }, [messages, notebookId, notebookTitle]);

  const sendMessage = useCallback(async () => {
    const q = input.trim();
    if (!notebookId || !q || sending) return;
    const askedIn = notebookId;
    setInput("");
    setSending(true);

    const userMsg: ChatMessage = { id: genId(), role: "user", text: q };
    setMessages((prev) => [...prev, userMsg]);

    try {
      const res = await api<{
        answer: string;
        conversation_id: string;
        references: ChatReference[];
      }>(`/api/notebooklm/nlm/notebooks/${askedIn}/chat`, {
        method: "POST",
        body: { question: q, conversation_id: conversationId },
        timeoutMs: 120_000,
      });
      if (activeNotebookRef.current !== askedIn) return;
      setConversationId(res.conversation_id);
      const assistantMsg: ChatMessage = {
        id: genId(),
        role: "assistant",
        text: res.answer,
        references: res.references,
      };
      setMessages((prev) => [...prev, assistantMsg]);
    } catch (e: unknown) {
      if (activeNotebookRef.current !== askedIn) return;
      const errMsg: ChatMessage = {
        id: genId(),
        role: "assistant",
        text: `Error: ${e instanceof Error ? e.message : "Failed to get answer"}`,
      };
      setMessages((prev) => [...prev, errMsg]);
    } finally {
      if (activeNotebookRef.current === askedIn) setSending(false);
    }
  }, [conversationId, input, notebookId, sending]);

  const startNewConversation = useCallback(() => {
    setMessages([]);
    setConversationId(null);
  }, []);

  const markIngestProcessing = useCallback(() => {
    setIngestTrack((prev) => prev ? { ...prev, status: "processing" } : null);
  }, []);

  return {
    messages,
    input,
    setInput,
    sending,
    conversationId,
    addingToWiki,
    ingestTrack,
    sendMessage,
    addToWiki,
    startNewConversation,
    markIngestProcessing,
  };
}

type NotebookChat = ReturnType<typeof useNotebookChat>;

/* ─── Chat tab ───────────────────────────────────────────────────────────── */

function ChatTab({ chat, notebookTitle, sourceCount }: { chat: NotebookChat; notebookTitle: string; sourceCount: number }) {
  const {
    messages,
    input,
    setInput,
    sending,
    conversationId,
    addingToWiki,
    ingestTrack,
    sendMessage,
    addToWiki,
    startNewConversation,
    markIngestProcessing,
  } = chat;

  const [reviewOpen, setReviewOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const wikiButton = messages.length > 0 && (
    !ingestTrack ? (
      <Button size="sm" variant="outline" className="h-7 px-2.5 text-[12px] gap-1.5"
        onClick={addToWiki} disabled={addingToWiki}>
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
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2.5 sm:px-6 shrink-0">
        <div className="flex min-w-0 items-center gap-2 text-[12px] text-muted-foreground">
          <span className="size-2 shrink-0 rounded-full bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,.65)]" />
          <span className="truncate">Tự động dùng {sourceCount} nguồn trong notebook</span>
          {messages.length > 0 && <span className="hidden sm:inline">· {messages.length} tin nhắn</span>}
        </div>
        {wikiButton}
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-4 sm:px-6 space-y-4">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full text-center py-12">
            <span className="material-symbols-outlined text-[40px] text-muted-foreground/20 mb-3">chat</span>
            <p className="text-[14px] text-foreground font-medium">Hỏi bất kỳ điều gì về notebook</p>
            <p className="max-w-md text-[12px] text-muted-foreground mt-1">Arkon tự chọn toàn bộ nguồn đã xử lý. Bạn không cần tích từng nguồn trước khi hỏi.</p>
          </div>
        ) : (
          messages.map((msg) => (
            <div key={msg.id} className={cn("flex gap-3", msg.role === "user" ? "justify-end" : "justify-start")}>
              {msg.role === "assistant" && (
                <span className="material-symbols-outlined text-[18px] text-primary/60 shrink-0 mt-1">smart_toy</span>
              )}
              <div
                className={cn(
                  "group/message relative max-w-[92%] rounded-2xl px-4 py-3 text-[13px] sm:max-w-[82%]",
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground rounded-br-sm"
                    : "border border-border/70 bg-card text-foreground shadow-sm rounded-bl-sm"
                )}
              >
                {msg.role === "assistant"
                  ? <NotebookChatMarkdown text={msg.text} />
                  : <p className="whitespace-pre-wrap leading-relaxed">{msg.text}</p>}
                {msg.references && msg.references.length > 0 && (
                  <details className="mt-3 border-t border-border pt-2">
                    <summary className="cursor-pointer text-[11px] font-medium text-primary">{msg.references.length} nguồn trích dẫn</summary>
                    <div className="mt-2 space-y-1.5">
                      {msg.references.map((ref, i) => (
                        <p key={`${ref.source_id}-${i}`} className="rounded-md bg-muted/50 px-2 py-1.5 text-[11px] leading-5 text-muted-foreground">
                          [{ref.citation_number ?? i + 1}] {ref.cited_text || "Nguồn tham chiếu"}
                        </p>
                      ))}
                    </div>
                  </details>
                )}
                <div className={cn("mt-1 flex justify-end", msg.role === "user" && "[&_button]:text-primary-foreground/70")}>
                  <ChatCopyButton text={msg.text} label="Copy" />
                </div>
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
            <div className="flex items-center gap-2 rounded-2xl rounded-bl-sm border border-border bg-card px-4 py-2.5 shadow-sm">
              <span className="flex gap-1" aria-label="NotebookLM đang trả lời">
                <i className="size-1.5 animate-pulse rounded-full bg-primary [animation-delay:-.3s]" />
                <i className="size-1.5 animate-pulse rounded-full bg-primary [animation-delay:-.15s]" />
                <i className="size-1.5 animate-pulse rounded-full bg-primary" />
              </span>
              <span className="text-[11px] text-muted-foreground">Đang đọc {sourceCount} nguồn…</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="shrink-0 border-t border-border bg-background/80 px-3 py-3 backdrop-blur sm:px-6 flex items-end gap-2">
        {conversationId && (
          <button
            className="shrink-0 text-muted-foreground/50 hover:text-muted-foreground text-[11px] underline underline-offset-2"
            onClick={startNewConversation}
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
          className="flex-1 rounded-xl border border-border bg-card px-4 py-2.5 text-[13px] text-foreground placeholder:text-muted-foreground/50 focus:outline-none focus:ring-2 focus:ring-primary/30 resize-none"
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
            markIngestProcessing();
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
  const [notebooksError, setNotebooksError] = useState<string | null>(null);
  const [sourcesError, setSourcesError] = useState<string | null>(null);
  const [artifactsError, setArtifactsError] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [authOk, setAuthOk] = useState<boolean | null>(null);
  const [authEmail, setAuthEmail] = useState<string | null>(null);

  const [showCreate, setShowCreate] = useState(false);
  const [showImport, setShowImport] = useState(false);
  const [disconnecting, setDisconnecting] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState<number | null>(null);
  const [sessionMsg, setSessionMsg] = useState<string | null>(null);

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const selectedNotebook = notebooks.find((n) => n.id === selectedId) ?? null;

  const chat = useNotebookChat(selectedId, selectedNotebook?.title ?? "");

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
    setSessionMsg(null);
    try {
      const r = await api<{ success: boolean; message: string; last_refreshed?: number }>("/api/notebooklm/auth/refresh", { method: "POST" });
      if (r.success) {
        setAuthOk(true);
        if (r.last_refreshed) setLastRefreshed(r.last_refreshed);
      } else {
        // The status endpoint only checks that the cookie file exists; a live
        // refresh is what actually proves the session works. If it fails, the
        // session is dead — flip to the reconnect prompt and say why.
        setAuthOk(false);
        setSessionMsg(r.message || "Session expired. Reconnect to continue.");
      }
    } catch (e) {
      if (isSessionExpired(e)) setAuthOk(false);
      setSessionMsg(e instanceof Error ? e.message : "Could not refresh the session.");
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
    setNotebooksError(null);
    try {
      const data = await api<NLMNotebookNative[]>("/api/notebooklm/nlm/notebooks");
      setNotebooks(data);
      if (data.length > 0 && !selectedId) setSelectedId(data[0].id);
    } catch (e: unknown) {
      if (isSessionExpired(e)) setAuthOk(false);
      else setNotebooksError(e instanceof Error ? e.message : "Request failed");
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
    setSourcesError(null);
    try {
      const data = await api<NLMSourceNative[]>(`/api/notebooklm/nlm/notebooks/${nbId}/sources`);
      setSources(data);
    } catch (e: unknown) {
      if (isSessionExpired(e)) setAuthOk(false);
      else setSourcesError(e instanceof Error ? e.message : "Request failed");
      setSources([]);
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
      setArtifactsError(null);
    } catch (e: unknown) {
      if (isSessionExpired(e)) setAuthOk(false);
      // A failed background poll keeps the list it already has; only a foreground
      // load can leave the panel with nothing to show, and that needs the error.
      else if (!silent) {
        setArtifactsError(e instanceof Error ? e.message : "Request failed");
        setArtifacts([]);
      }
    } finally {
      if (!silent) setArtifactsLoading(false);
    }
  }, []);

  /* ── Load data when notebook or tab changes ── */
  useEffect(() => {
    if (!selectedId) {
      setSources([]); setArtifacts([]);
      setSourcesError(null); setArtifactsError(null);
      return;
    }
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
              <span className="text-[12px] text-amber-700 ml-2">
                {sessionMsg || "Import your Google cookies to connect."}
              </span>
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
        <div className="w-64 shrink-0 border-r border-border flex flex-col">
          <div className="px-4 py-3 border-b border-border">
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
            ) : notebooksError ? (
              <LoadErrorPanel
                title="Could not load notebooks"
                message={notebooksError}
                onRetry={loadNotebooks}
              />
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
                <div
                  key={nb.id}
                  className={cn(
                    "group w-full flex items-start gap-2.5 px-3 py-2.5 transition-colors",
                    selectedId === nb.id ? "bg-black/[0.04]" : "hover:bg-black/[0.02]"
                  )}
                >
                  <button
                    onClick={() => { setSelectedId(nb.id); setTab("sources"); }}
                    className="flex-1 min-w-0 flex items-start gap-2.5 text-left"
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
                  </button>
                  <button
                    className="opacity-0 group-hover:opacity-100 shrink-0 text-muted-foreground/40 hover:text-red-500 transition-all"
                    onClick={() => handleDeleteNotebook(nb.id)}
                    title="Delete notebook"
                  >
                    <span className="material-symbols-outlined text-[15px]">delete</span>
                  </button>
                </div>
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
              <div className="shrink-0 border-b border-border">
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
                  error={sourcesError}
                  onRefresh={() => loadSources(selectedId!)}
                />
              )}
              {tab === "studio" && (
                <StudioTab
                  notebookId={selectedId!}
                  artifacts={artifacts}
                  loading={artifactsLoading}
                  error={artifactsError}
                  onRefresh={() => loadArtifacts(selectedId!)}
                />
              )}
              {tab === "chat" && (
                <ChatTab
                  chat={chat}
                  notebookTitle={selectedNotebook?.title ?? ""}
                  sourceCount={selectedNotebook?.sources_count ?? 0}
                />
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
          setSessionMsg(null);
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
