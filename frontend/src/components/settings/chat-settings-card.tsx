"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function ChatSettingsCard() {
  const [ragEnabled, setRagEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => { void load(); }, []);

  async function load() {
    try {
      const data = await api<Record<string, unknown>>("/api/settings");
      const val = data["chat_rag_enabled"];
      setRagEnabled(val === undefined || val === null || val === "" || String(val).toLowerCase() !== "false");
    } finally {
      setLoading(false);
    }
  }

  async function toggle() {
    const next = !ragEnabled;
    setRagEnabled(next);
    setSaving(true);
    setSaved(false);
    try {
      await api("/api/settings", {
        method: "PUT",
        body: { settings: { chat_rag_enabled: next ? "true" : "false" } },
      });
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch {
      setRagEnabled(!next);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
      <div className="flex items-center gap-3 px-6 pt-5 pb-4">
        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
          <span className="material-symbols-outlined text-primary text-base">manage_search</span>
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-semibold text-foreground">Chat Settings</h3>
          <p className="text-xs text-muted-foreground">Configure chatbot behaviour</p>
        </div>
      </div>

      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium text-foreground">RAG / KB Search</span>
            <span className="text-xs text-muted-foreground">
              {ragEnabled
                ? "Chatbot searches the knowledge base before answering"
                : "Chatbot answers from model knowledge only — KB search is disabled"}
            </span>
          </div>

          <button
            onClick={toggle}
            disabled={loading || saving}
            aria-pressed={ragEnabled}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
              ragEnabled ? "bg-primary" : "bg-muted"
            }`}
          >
            <span
              className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm ring-0 transition-transform duration-200 ${
                ragEnabled ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>

        {saved && (
          <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
            <span className="material-symbols-outlined text-sm">check_circle</span>
            Saved
          </p>
        )}

        {!ragEnabled && (
          <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-400">
            <span className="material-symbols-outlined text-sm mt-0.5">warning</span>
            <span>KB search is off. Chatbot will not use the knowledge base. Enable it once a valid embedding provider is configured.</span>
          </div>
        )}
      </div>
    </div>
  );
}
