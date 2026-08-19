import { useState } from "react";
import { api, apiUpload } from "@/lib/api";
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
import { Source } from "./types";

const ALLOWED_EXTS = [".pdf", ".docx", ".doc", ".xlsx", ".csv", ".txt", ".md", ".pptx"];
const MAX_SIZE = 50 * 1024 * 1024; // 50 MB

// Token estimation: ~4 chars per token (conservative for English)
// PDF/DOCX have lower text density (~40-60% of file size is actual text)
const TEXT_DENSITY: Record<string, number> = {
  ".pdf": 0.45,
  ".docx": 0.35,
  ".doc": 0.40,
  ".pptx": 0.25,
  ".xlsx": 0.30,
  ".csv": 0.90,
  ".txt": 1.0,
  ".md": 1.0,
};
const CHARS_PER_TOKEN = 4;

// Known model context windows (sync with backend writer.py)
const MODEL_CONTEXT_WINDOWS: Record<string, number> = {
  "gemini-3.1": 1_000_000,
  "gemini-2.5": 1_000_000,
  "gpt-5": 1_000_000,
  "gpt-4.1": 1_000_000,
  "gpt-4o": 128_000,
  "claude-4": 1_000_000,
};
// 60% of context for source text (same as backend)
const SOURCE_BUDGET_RATIO = 0.60;
const DEFAULT_CONTEXT = 200_000; // conservative default

function estimateTokens(file: File): number {
  const ext = "." + file.name.split(".").pop()?.toLowerCase();
  const density = TEXT_DENSITY[ext] ?? 0.5;
  const estimatedChars = file.size * density;
  return Math.round(estimatedChars / CHARS_PER_TOKEN);
}

function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`;
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(0)}K`;
  return tokens.toString();
}

type TokenWarning = {
  level: "ok" | "caution" | "danger";
  message: string;
  estimatedTokens: number;
};

function getTokenWarning(tokens: number): TokenWarning {
  // Use default context window as baseline (most models now have 1M)
  const budget = Math.round(DEFAULT_CONTEXT * SOURCE_BUDGET_RATIO);

  if (tokens <= budget) {
    return {
      level: "ok",
      message: `~${formatTokens(tokens)} tokens — fits well within most model context windows.`,
      estimatedTokens: tokens,
    };
  }

  // Check against 1M context models (most frontier models)
  const largeBudget = Math.round(1_000_000 * SOURCE_BUDGET_RATIO);
  if (tokens <= largeBudget) {
    return {
      level: "caution",
      message: `~${formatTokens(tokens)} tokens — large document. Works with Gemini, GPT-5, Claude 4 (1M context). May need chunking for smaller models (GPT-4o).`,
      estimatedTokens: tokens,
    };
  }

  return {
    level: "danger",
    message: `~${formatTokens(tokens)} tokens — exceeds most model context windows. Consider splitting this document into smaller files for better results.`,
    estimatedTokens: tokens,
  };
}

export function AddDocumentModal({
  open,
  onOpenChange,
  projectId,
  availableSources,
  onDone,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  projectId: string;
  availableSources: Source[];
  onDone: () => void;
}) {
  const [mode, setMode] = useState<"upload" | "link">("upload");
  const [dragOver, setDragOver] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [selectedSourceId, setSelectedSourceId] = useState("");
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [tokenWarning, setTokenWarning] = useState<TokenWarning | null>(null);

  const reset = () => {
    setSelectedFile(null);
    setFileError(null);
    setError(null);
    setSelectedSourceId("");
    setUploading(false);
    setLinking(false);
    setTokenWarning(null);
  };

  const validateFile = (file: File): string | null => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!ALLOWED_EXTS.includes(ext)) return `Unsupported file type: ${ext}`;
    if (file.size > MAX_SIZE) return `File too large (${(file.size / 1024 / 1024).toFixed(1)} MB). Max: 50 MB`;
    return null;
  };

  const handleFile = (file: File) => {
    setFileError(null);
    setTokenWarning(null);
    const err = validateFile(file);
    if (err) { setFileError(err); return; }
    setSelectedFile(file);

    // Estimate tokens and set warning
    const tokens = estimateTokens(file);
    setTokenWarning(getTokenWarning(tokens));
  };

  const handleUpload = async () => {
    if (!selectedFile) return;
    setUploading(true);
    setError(null);
    try {
      const formData = new FormData();
      formData.append("file", selectedFile);
      formData.append("title", selectedFile.name);
      await apiUpload(`/api/projects/${projectId}/sources/upload`, formData);
      reset();
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const handleLink = async () => {
    if (!selectedSourceId) return;
    setLinking(true);
    setError(null);
    try {
      await api(`/api/projects/${projectId}/sources`, {
        method: "POST",
        body: { source_id: selectedSourceId },
      });
      reset();
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to link document");
    } finally {
      setLinking(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!v) reset(); onOpenChange(v); }}>
      <DialogContent className="sm:max-w-[520px] p-0 overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-0">
          <DialogTitle className="text-lg font-heading">Add Document</DialogTitle>
        </DialogHeader>

        {/* Mode tabs */}
        <div className="flex border-b border-border mx-6">
          {(["upload", "link"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setError(null); }}
              className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors ${mode === m
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
                }`}
            >
              <span className="material-symbols-outlined text-sm">
                {m === "upload" ? "cloud_upload" : "add"}
              </span>
              {m === "upload" ? "Upload File" : "Add Existing"}
            </button>
          ))}
        </div>

        <div className="px-6 pb-6 pt-4">
          {/* Error */}
          {error && (
            <div className="mb-4 text-sm text-destructive bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
              <span className="material-symbols-outlined text-sm">error</span>
              {error}
            </div>
          )}

          {mode === "upload" ? (
            /* ---- Upload tab ---- */
            <div className="flex flex-col gap-4">
              {/* Drop zone */}
              {/*
                A <label> wrapping the input is what makes this keyboard-reachable: the whole zone
                still opens the picker on click, but activation no longer depends on a div's onClick.
              */}
              <label
                onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  const file = e.dataTransfer.files?.[0];
                  if (file) handleFile(file);
                }}
                className={`cursor-pointer rounded-xl border-2 border-dashed p-8 flex flex-col items-center gap-3 transition-all has-[input:focus-visible]:border-primary has-[input:focus-visible]:ring-3 has-[input:focus-visible]:ring-ring/50 ${dragOver
                  ? "border-primary bg-primary/5"
                  : selectedFile
                    ? "border-green-400/50 bg-green-500/5"
                    : "border-border hover:border-primary/40 hover:bg-primary/5"
                  }`}
              >
                {/* sr-only, not hidden: display:none drops the input out of the tab order and the a11y tree. */}
                <input
                  type="file"
                  className="sr-only"
                  accept={ALLOWED_EXTS.join(",")}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) handleFile(file);
                    e.target.value = "";
                  }}
                />
                {selectedFile ? (
                  <>
                    <div className="w-10 h-10 rounded-full bg-green-500/10 flex items-center justify-center">
                      <span className="material-symbols-outlined text-green-600" style={{ fontSize: 22 }}>check_circle</span>
                    </div>
                    <div className="text-center min-w-0 max-w-full">
                      <p className="text-sm font-medium text-foreground truncate max-w-[360px]">{selectedFile.name}</p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        {(selectedFile.size / 1024).toFixed(0)} KB · Click to change
                      </p>
                    </div>
                  </>
                ) : (
                  <>
                    <div className={`w-10 h-10 rounded-full flex items-center justify-center ${dragOver ? "bg-primary/20" : "bg-primary/10"
                      }`}>
                      <span className="material-symbols-outlined text-primary" style={{ fontSize: 22 }}>cloud_upload</span>
                    </div>
                    <div className="text-center">
                      <p className="text-sm font-medium text-foreground">
                        {dragOver ? "Drop file here" : "Drag & drop or click to browse"}
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5">
                        PDF, DOCX, XLSX, CSV, TXT, MD, PPTX · Max 50 MB
                      </p>
                    </div>
                  </>
                )}
              </label>

              {/* Token estimation warning */}
              {tokenWarning && (
                <div className={`text-sm px-3 py-2.5 rounded-lg flex items-start gap-2 ${
                  tokenWarning.level === "ok"
                    ? "bg-green-500/10 text-green-700 dark:text-green-400"
                    : tokenWarning.level === "caution"
                      ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
                      : "bg-destructive/10 text-destructive"
                }`}>
                  <span className="material-symbols-outlined text-sm mt-0.5 shrink-0">
                    {tokenWarning.level === "ok" ? "check_circle" : tokenWarning.level === "caution" ? "warning" : "error"}
                  </span>
                  <span>{tokenWarning.message}</span>
                </div>
              )}

              {fileError && (
                <p className="text-sm text-destructive flex items-center gap-1.5">
                  <span className="material-symbols-outlined text-sm">warning</span>
                  {fileError}
                </p>
              )}

              <Button
                disabled={!selectedFile || uploading}
                onClick={handleUpload}
                className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
              >
                {uploading ? (
                  <>
                    <span className="material-symbols-outlined text-base mr-2 animate-spin">progress_activity</span>
                    Uploading...
                  </>
                ) : (
                  <>
                    <span className="material-symbols-outlined text-base mr-2">upload</span>
                    Upload
                  </>
                )}
              </Button>
            </div>
          ) : (
            /* ---- Link tab ---- */
            <div className="flex flex-col gap-4">
              {availableSources.length === 0 ? (
                <div className="py-8 flex flex-col items-center gap-2">
                  <span className="material-symbols-outlined text-3xl text-muted-foreground/40">description</span>
                  <p className="text-sm text-muted-foreground">No available documents to link</p>
                  <p className="text-xs text-muted-foreground">All documents are already in this workspace.</p>
                </div>
              ) : (
                <>
                  <Select value={selectedSourceId} onValueChange={(v) => setSelectedSourceId(v ?? "")}>
                    <SelectTrigger className="bg-background w-full">
                      {selectedSourceId ? (
                        <span className="truncate">
                          {availableSources.find(s => s.id === selectedSourceId)?.title || selectedSourceId}
                        </span>
                      ) : (
                        <SelectValue placeholder="Select an existing document to add..." />
                      )}
                    </SelectTrigger>
                    <SelectContent>
                      {availableSources.map((s) => (
                        <SelectItem key={s.id} value={s.id}>
                          <div className="flex items-center gap-2">
                            <span className="material-symbols-outlined text-sm text-muted-foreground">
                              {s.source_type === "url" ? "link" : "description"}
                            </span>
                            {s.title || s.id}
                            {s.knowledge_type_name && (
                              <span className="text-[10px] text-muted-foreground bg-accent/50 px-1.5 py-0.5 rounded">
                                {s.knowledge_type_name}
                              </span>
                            )}
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>

                  <Button
                    disabled={!selectedSourceId || linking}
                    onClick={handleLink}
                    className="w-full bg-primary text-primary-foreground hover:bg-primary/90"
                  >
                    {linking ? (
                      <>
                        <span className="material-symbols-outlined text-base mr-2 animate-spin">progress_activity</span>
                        Linking...
                      </>
                    ) : (
                      <>
                        <span className="material-symbols-outlined text-base mr-2">add_link</span>
                        Link to Workspace
                      </>
                    )}
                  </Button>
                </>
              )}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
