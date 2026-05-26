"use client";

import React from "react";
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

type PageType = "synthesis" | "topic" | "concept" | "entity" | "source";

const PAGE_TYPE_OPTIONS: Array<{ value: PageType; label: string; icon: string; desc: string }> = [
  { value: "synthesis", icon: "chat_bubble", label: "Synthesis", desc: "AI-synthesized Q&A insight (recommended)" },
  { value: "topic",     icon: "topic",        label: "Topic",     desc: "Broad subject or domain overview" },
  { value: "concept",   icon: "lightbulb",    label: "Concept",   desc: "Definition or idea" },
  { value: "entity",    icon: "person",       label: "Entity",    desc: "Person, org, or system" },
  { value: "source",    icon: "description",  label: "Source",    desc: "Reference or document" },
];

type Props = {
  conversationId: string;
  defaultTitle: string;
  onClose: () => void;
};

export function AddToWikiDialog({ conversationId, defaultTitle, onClose }: Props) {
  const [title, setTitle] = React.useState(defaultTitle);
  const [pageType, setPageType] = React.useState<PageType>("synthesis");
  const [saving, setSaving] = React.useState(false);
  const [error, setError] = React.useState<string | null>(null);
  const [result, setResult] = React.useState<{ slug: string; title: string } | null>(null);

  const handleSave = async () => {
    if (!title.trim()) { setError("Title is required"); return; }
    setSaving(true);
    setError(null);
    try {
      const res = await api<{ slug: string; title: string }>(
        `/api/chat/conversations/${conversationId}/to-wiki`,
        {
          method: "POST",
          body: { title: title.trim(), page_type: pageType, scope_type: "global" },
          timeoutMs: 120_000,
        }
      );
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to create wiki page");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="material-symbols-outlined text-primary" style={{ fontSize: 20 }}>
              auto_stories
            </span>
            Add to Wiki
          </DialogTitle>
        </DialogHeader>

        {result ? (
          /* ── Success state ── */
          <div className="flex flex-col items-center gap-4 py-4">
            <div className="w-14 h-14 rounded-full bg-green-500/10 flex items-center justify-center">
              <span className="material-symbols-outlined text-green-600 text-3xl">check_circle</span>
            </div>
            <div className="text-center">
              <p className="font-semibold text-foreground">{result.title}</p>
              <p className="text-xs text-muted-foreground mt-1">Wiki page created successfully</p>
            </div>
            <div className="flex gap-2">
              <a
                href={`/wiki/${result.slug}`}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-primary text-primary-foreground text-sm font-medium hover:bg-primary/90 transition-colors"
              >
                <span className="material-symbols-outlined text-sm">open_in_new</span>
                View Wiki Page
              </a>
              <Button variant="outline" onClick={onClose}>Close</Button>
            </div>
          </div>
        ) : (
          /* ── Form state ── */
          <div className="flex flex-col gap-4 mt-2">
            {/* Info notice */}
            <div className="flex items-start gap-2.5 rounded-lg bg-primary/5 border border-primary/15 px-3 py-2.5 text-xs text-muted-foreground leading-relaxed">
              <span className="material-symbols-outlined text-primary shrink-0 mt-0.5" style={{ fontSize: 15 }}>
                info
              </span>
              The AI will synthesize this conversation into a structured wiki page.
              LLM generation may take a few seconds.
            </div>

            {/* Title */}
            <div className="flex flex-col gap-1.5">
              <Label>Wiki page title</Label>
              <Input
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. SQL Injection Prevention Techniques"
                autoFocus
              />
            </div>

            {/* Page type */}
            <div className="flex flex-col gap-1.5">
              <Label>Page type</Label>
              <div className="grid grid-cols-1 gap-1.5">
                {PAGE_TYPE_OPTIONS.map((opt) => (
                  <button
                    key={opt.value}
                    type="button"
                    onClick={() => setPageType(opt.value)}
                    className={`flex items-center gap-3 px-3 py-2.5 rounded-lg border text-left transition-colors ${
                      pageType === opt.value
                        ? "border-primary bg-primary/5 text-primary"
                        : "border-border hover:bg-secondary/40 text-muted-foreground"
                    }`}
                  >
                    <span
                      className="material-symbols-outlined shrink-0"
                      style={{ fontSize: 18 }}
                    >
                      {opt.icon}
                    </span>
                    <div className="min-w-0 flex-1">
                      <span className={`text-sm font-medium block ${pageType === opt.value ? "text-primary" : "text-foreground"}`}>
                        {opt.label}
                      </span>
                      <span className="text-xs text-muted-foreground">{opt.desc}</span>
                    </div>
                    {pageType === opt.value && (
                      <span className="material-symbols-outlined text-primary shrink-0" style={{ fontSize: 18 }}>
                        check_circle
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </div>

            {error && (
              <p className="text-destructive text-sm bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
                <span className="material-symbols-outlined text-sm">error</span>
                {error}
              </p>
            )}

            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" onClick={onClose} disabled={saving}>
                Cancel
              </Button>
              <Button onClick={handleSave} disabled={saving || !title.trim()}>
                {saving ? (
                  <span className="flex items-center gap-2">
                    <span className="material-symbols-outlined animate-spin text-sm">progress_activity</span>
                    Synthesizing…
                  </span>
                ) : (
                  <span className="flex items-center gap-2">
                    <span className="material-symbols-outlined text-sm">auto_stories</span>
                    Create Wiki Page
                  </span>
                )}
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
