"use client";

import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

type EmbeddingSpec = {
  id: string;
  provider: string;
  model_id: string;
  dimension: number;
  label: string;
  cost_per_1m_tokens: number | null;
  notes: string | null;
  api_key_configured: boolean;
};

type CatalogResp = {
  active_spec_id: string | null;
  specs: EmbeddingSpec[];
};

type StatusResp = {
  active_spec_id: string | null;
  total_pages: number;
  embedded_pages: number;
  current_job: JobResp | null;
};

type JobResp = {
  id: string;
  model_spec_id: string;
  status: "pending" | "running" | "completed" | "failed" | "cancelled";
  total_pages: number;
  done_pages: number;
  error_message: string | null;
};

// ── Provider definitions ──────────────────────────────────────────────────────

type ProviderDef = {
  value: string;
  label: string;
  icon: string;
  needsKey: boolean;
  canFetch: boolean;
  defaultBaseUrl?: string;
  keyPlaceholder?: string;
  modelPlaceholder?: string;
};

const EMBEDDING_PROVIDERS: ProviderDef[] = [
  {
    value: "google",
    label: "Google",
    icon: "g_mobiledata",
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "AIza...",
    modelPlaceholder: "e.g. gemini-embedding-2",
  },
  {
    value: "openai",
    label: "OpenAI",
    icon: "smart_toy",
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "sk-...",
    modelPlaceholder: "e.g. text-embedding-3-small",
  },
  {
    value: "anthropic",
    label: "Anthropic",
    icon: "psychology",
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "sk-ant-...",
    modelPlaceholder: "custom model ID",
  },
  {
    value: "ollama",
    label: "Ollama",
    icon: "computer",
    needsKey: false,
    canFetch: true,
    defaultBaseUrl: "http://host.docker.internal:11434/v1",
    keyPlaceholder: "ollama",
    modelPlaceholder: "e.g. nomic-embed-text",
  },
  {
    value: "ninerouter",
    label: "9Router",
    icon: "hub",
    needsKey: true,
    canFetch: true,
    keyPlaceholder: "bearer-token",
    modelPlaceholder: "e.g. openai/text-embedding-3-small",
  },
];

// ── Component ─────────────────────────────────────────────────────────────────

export function EmbeddingSettingsCard() {
  const [catalog, setCatalog] = useState<CatalogResp | null>(null);
  const [status, setStatus] = useState<StatusResp | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string>("");
  const [selectedSpecId, setSelectedSpecId] = useState<string | null>(null);
  const [customModel, setCustomModel] = useState(false);
  const [customModelInput, setCustomModelInput] = useState("");
  const [customDimension, setCustomDimension] = useState<number>(1536);
  const [maskedKeys, setMaskedKeys] = useState<Record<string, string>>({});
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");

  // Fetch state
  const [fetchedModels, setFetchedModels] = useState<string[] | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState("");

  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const [error, setError] = useState("");

  useEffect(() => { void refresh(true); }, []);

  useEffect(() => {
    setApiKey(selectedProvider ? (maskedKeys[selectedProvider] ?? "") : "");
  }, [selectedProvider, maskedKeys]);

  useEffect(() => {
    const job = status?.current_job;
    if (!job || (job.status !== "pending" && job.status !== "running")) return;
    const t = setInterval(() => void refreshStatus(), 2000);
    return () => clearInterval(t);
  }, [status?.current_job?.id, status?.current_job?.status]);

  async function refresh(initSelection = false) {
    try {
      const [c, s, settings] = await Promise.all([
        api<CatalogResp>("/api/settings/embeddings/catalog"),
        api<StatusResp>("/api/settings/embeddings/status"),
        api<Record<string, unknown>>("/api/settings"),
      ]);
      setCatalog(c);
      setStatus(s);

      const masked: Record<string, string> = {};
      for (const p of ["google", "openai", "anthropic", "ollama", "ninerouter"]) {
        const v = settings[`embedding_api_key__${p}`];
        if (typeof v === "string" && v.length > 0) masked[p] = v;
      }
      setMaskedKeys(masked);

      const savedBaseUrl = settings["embedding_base_url"];
      if (typeof savedBaseUrl === "string") setBaseUrl(savedBaseUrl);

      if (initSelection) {
        const activeId = c.active_spec_id ?? c.specs[0]?.id ?? null;
        const activeSpec = c.specs.find((sp) => sp.id === activeId);
        if (activeSpec) {
          setSelectedProvider(activeSpec.provider);
          setSelectedSpecId(activeId);
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load embedding catalog");
    }
  }

  async function refreshStatus() {
    try {
      const s = await api<StatusResp>("/api/settings/embeddings/status");
      setStatus(s);
    } catch { /* keep last known */ }
  }

  async function handleFetchModels() {
    if (!baseUrl) return;
    setFetching(true);
    setFetchError("");
    setFetchedModels(null);
    try {
      const key = apiKey.startsWith("••••") ? "" : apiKey;
      const res = await api<{ models: string[] }>("/api/settings/fetch-models", {
        method: "POST",
        body: { base_url: baseUrl, api_key: key },
      });
      setFetchedModels(res.models);
      // Reset model selection after fresh fetch
      setSelectedSpecId(null);
      setCustomModel(false);
      setCustomModelInput("");
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : "Failed to fetch models");
    } finally {
      setFetching(false);
    }
  }

  function handleProviderSelect(provider: string) {
    const def = EMBEDDING_PROVIDERS.find((p) => p.value === provider)!;
    setSelectedProvider(provider);
    setCustomModel(false);
    setCustomModelInput("");
    setFetchedModels(null);
    setFetchError("");
    setBaseUrl(def.defaultBaseUrl ?? "");
    const firstSpec = catalog?.specs.find((sp) => sp.provider === provider);
    setSelectedSpecId(firstSpec?.id ?? null);
  }

  function handleModelSelect(val: string) {
    if (val === "__custom__") {
      setCustomModel(true);
      setSelectedSpecId(null);
      setCustomModelInput("");
    } else if (val.startsWith("__fetched__")) {
      // Fetched model not in catalog → custom mode with pre-filled ID
      const modelId = val.slice("__fetched__".length);
      setCustomModel(true);
      setSelectedSpecId(null);
      setCustomModelInput(modelId);
    } else {
      setCustomModel(false);
      setSelectedSpecId(val);
    }
  }

  // ── Derived ────────────────────────────────────────────────────────────────

  const def = EMBEDDING_PROVIDERS.find((p) => p.value === selectedProvider) ?? null;
  const providerSpecs = catalog?.specs.filter((sp) => sp.provider === selectedProvider) ?? [];
  const selectedSpec = catalog?.specs.find((sp) => sp.id === selectedSpecId) ?? null;
  const job = status?.current_job ?? null;
  const jobBusy = job && (job.status === "pending" || job.status === "running");
  const isMaskedKey = apiKey.includes("•");
  const hasNewKey = apiKey.trim().length > 0 && !isMaskedKey;
  const activeSpecId = catalog?.active_spec_id ?? null;
  const effectiveSpecId = customModel
    ? `${selectedProvider}/${customModelInput.trim()}`
    : selectedSpecId;
  const willSwitch = !!effectiveSpecId && effectiveSpecId !== activeSpecId;
  const canSave =
    !!selectedProvider &&
    !jobBusy &&
    (customModel ? customModelInput.trim().length > 0 : !!selectedSpec) &&
    (hasNewKey || willSwitch || (selectedSpec?.api_key_configured ?? false) || baseUrl.trim().length > 0);

  // Fetched models not already in catalog for this provider
  const fetchedNotInCatalog = (fetchedModels ?? []).filter(
    (m) => !providerSpecs.some((sp) => sp.model_id === m || sp.model_id === m.split(":")[0])
  );

  // ── Actions ────────────────────────────────────────────────────────────────

  async function handleSave() {
    if (!selectedProvider || !effectiveSpecId) return;
    setSaving(true);
    setError("");
    try {
      const settingsToSave: Record<string, string> = {};
      if (hasNewKey) settingsToSave[`embedding_api_key__${selectedProvider}`] = apiKey.trim();
      settingsToSave["embedding_base_url"] = baseUrl.trim();
      await api("/api/settings", { method: "PUT", body: { settings: settingsToSave } });
      if (willSwitch) {
        const switchBody: Record<string, unknown> = { model_spec_id: effectiveSpecId };
        if (customModel) {
          switchBody.dimension = customDimension;
          switchBody.model_id = customModelInput.trim();
        }
        await api("/api/settings/embeddings/switch", {
          method: "POST",
          body: switchBody,
        });
      }
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api<{ success: boolean; message: string }>("/api/settings/test-embedding", {
        method: "POST",
      });
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: e instanceof Error ? e.message : "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  async function cancelJob() {
    if (!job) return;
    try {
      await api(`/api/settings/embeddings/jobs/${job.id}/cancel`, { method: "POST" });
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Cancel failed");
    }
  }

  // ── Render ─────────────────────────────────────────────────────────────────

  if (!catalog || !status) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
        <div className="flex items-center gap-3 px-6 pt-5 pb-4">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
            <span className="material-symbols-outlined text-primary text-base">data_array</span>
          </div>
          <div>
            <h3 className="text-base font-semibold text-foreground">Embedding Model</h3>
            <p className="text-xs text-muted-foreground">Loading catalog…</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 pt-5 pb-4">
        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
          <span className="material-symbols-outlined text-primary text-base">data_array</span>
        </div>
        <div>
          <h3 className="text-base font-semibold text-foreground">Embedding Model</h3>
          <p className="text-xs text-muted-foreground">
            Converts documents and queries into vectors for semantic search.
          </p>
        </div>
      </div>

      {/* Provider selector */}
      <div className="px-6 pb-4">
        <div className="grid grid-cols-3 sm:grid-cols-5 gap-2">
          {EMBEDDING_PROVIDERS.map((p) => {
            const active = selectedProvider === p.value;
            const hasSavedKey = !!maskedKeys[p.value];
            return (
              <button
                key={p.value}
                onClick={() => handleProviderSelect(p.value)}
                disabled={!!jobBusy}
                className={`relative flex flex-col items-center gap-1.5 py-3 px-2 rounded-lg border transition-all text-xs font-medium disabled:opacity-50 ${
                  active
                    ? "border-primary bg-primary/8 text-primary"
                    : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground"
                }`}
              >
                {hasSavedKey && (
                  <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-green-500" />
                )}
                <span className={`material-symbols-outlined text-xl ${active ? "text-primary" : ""}`}>
                  {p.icon}
                </span>
                {p.label}
                {active && (
                  <span className="text-[9px] uppercase tracking-wide bg-primary/15 text-primary px-1.5 py-0.5 rounded-full font-semibold">
                    Active
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>

      {/* Config fields */}
      {def && (
        <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-4">
          {/* Provider banners */}
          {selectedProvider === "anthropic" && (
            <div className="flex items-start gap-2 px-3 py-2 rounded-lg bg-amber-500/8 border border-amber-200/50 text-xs text-amber-700 dark:text-amber-400">
              <span className="material-symbols-outlined text-sm mt-0.5">warning</span>
              <span>Anthropic does not currently offer an official embedding API. Use only if you have a compatible proxy endpoint.</span>
            </div>
          )}

          {/* Migration job progress */}
          {jobBusy && (
            <div className="p-3 rounded-lg bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-800">
              <div className="flex items-center justify-between text-xs mb-1.5">
                <span>Migrating to <strong>{job.model_spec_id}</strong> — {job.done_pages}/{job.total_pages} pages</span>
                <button onClick={cancelJob} className="text-xs underline hover:no-underline">Cancel</button>
              </div>
              <div className="h-2 rounded bg-blue-100 dark:bg-blue-900 overflow-hidden">
                <div
                  className="h-full bg-blue-500 transition-all"
                  style={{ width: `${job.total_pages > 0 ? Math.round((job.done_pages / job.total_pages) * 100) : 0}%` }}
                />
              </div>
            </div>
          )}

          {job?.status === "failed" && (
            <div className="p-3 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800 text-xs">
              <strong>Migration failed:</strong> {job.error_message || "unknown"}
            </div>
          )}

          {/* 1. Base URL — first */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">
              Base URL
              <span className="ml-1.5 text-muted-foreground font-normal">
                {def.canFetch ? "(required)" : "(optional — leave blank for default)"}
              </span>
            </Label>
            <div className="flex gap-2">
              <Input
                value={baseUrl}
                onChange={(e) => { setBaseUrl(e.target.value); setFetchedModels(null); }}
                placeholder={def.defaultBaseUrl ?? "https://api.openai.com/v1"}
                className="bg-background flex-1"
                disabled={!!jobBusy}
              />
              {def.canFetch && (
                <button
                  disabled={!baseUrl || fetching || !!jobBusy}
                  onClick={handleFetchModels}
                  className="flex items-center gap-1.5 px-3 py-2 rounded-lg text-xs font-medium border border-border bg-background hover:bg-accent transition-colors disabled:opacity-50 whitespace-nowrap"
                >
                  <span className={`material-symbols-outlined text-sm ${fetching ? "animate-spin" : ""}`}>
                    {fetching ? "progress_activity" : "refresh"}
                  </span>
                  {fetching ? "Fetching…" : "Fetch Models"}
                </button>
              )}
            </div>
            {fetchError && (
              <p className="text-xs text-destructive flex items-center gap-1">
                <span className="material-symbols-outlined text-sm">error</span>
                {fetchError}
              </p>
            )}
            {fetchedModels !== null && (
              <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
                <span className="material-symbols-outlined text-sm">check_circle</span>
                {fetchedModels.length} models loaded — {providerSpecs.length} in catalog, {fetchedNotInCatalog.length} custom
              </p>
            )}
          </div>

          {/* 2. API Key */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">
              API Key
              {!def.needsKey && (
                <span className="ml-1.5 text-muted-foreground font-normal">(optional for Ollama)</span>
              )}
              {selectedSpec?.api_key_configured && (
                <span className="ml-2 text-green-600 dark:text-green-400">✓ saved</span>
              )}
            </Label>
            <Input
              type={isMaskedKey ? "text" : "password"}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              onFocus={() => { if (isMaskedKey) setApiKey(""); }}
              placeholder={def.keyPlaceholder ?? "API key"}
              className="bg-background"
            />
          </div>

          {/* 3. Model selection */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Model</Label>

            {/* Placeholder when fetch-capable provider hasn't fetched yet */}
            {def.canFetch && fetchedModels === null && !customModel ? (
              <div className="h-9 rounded-md border border-dashed border-border bg-background/50 px-3 flex items-center text-xs text-muted-foreground">
                {baseUrl ? "Click \"Fetch Models\" to load available models" : "Enter Base URL above first"}
              </div>
            ) : !customModel ? (
              <select
                value={selectedSpecId ?? ""}
                onChange={(e) => handleModelSelect(e.target.value)}
                disabled={!!jobBusy}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring disabled:opacity-50"
              >
                <option value="">Select a model…</option>
                {/* Catalog specs for this provider */}
                {providerSpecs.length > 0 && (
                  <optgroup label="Catalog models (recommended)">
                    {providerSpecs.map((sp) => (
                      <option key={sp.id} value={sp.id}>
                        {sp.model_id} — {sp.dimension}d
                        {sp.notes ? ` (${sp.notes})` : ""}
                        {sp.id === activeSpecId ? " ✓" : ""}
                      </option>
                    ))}
                  </optgroup>
                )}
                {/* Fetched models not in catalog */}
                {fetchedNotInCatalog.length > 0 && (
                  <optgroup label="From server (custom — select dimension)">
                    {fetchedNotInCatalog.map((m) => (
                      <option key={`__fetched__${m}`} value={`__fetched__${m}`}>{m}</option>
                    ))}
                  </optgroup>
                )}
                <option value="__custom__">Other (type manually)…</option>
              </select>
            ) : (
              <div className="flex flex-col gap-2">
                <div className="flex gap-2">
                  <Input
                    value={customModelInput}
                    onChange={(e) => setCustomModelInput(e.target.value)}
                    placeholder={def.modelPlaceholder ?? "model-id"}
                    className="bg-background flex-1"
                    autoFocus
                  />
                  <button
                    onClick={() => { setCustomModel(false); setCustomModelInput(""); setSelectedSpecId(providerSpecs[0]?.id ?? null); }}
                    className="text-xs text-muted-foreground hover:text-foreground px-2"
                  >
                    ← list
                  </button>
                </div>
                {/* Dimension selector for custom models */}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Output dimension:</span>
                  {([768, 1536, 3072] as const).map((d) => (
                    <button
                      key={d}
                      onClick={() => setCustomDimension(d)}
                      className={`px-2.5 py-1 rounded text-xs font-medium border transition-colors ${
                        customDimension === d
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border text-muted-foreground hover:border-primary/40"
                      }`}
                    >
                      {d}d
                    </button>
                  ))}
                  <span className="text-[10px] text-muted-foreground">(must match DB table)</span>
                </div>
              </div>
            )}

            {/* Spec details for catalog selection */}
            {selectedSpec && !customModel && (
              <div className="flex items-center gap-3 text-[11px] text-muted-foreground mt-0.5">
                <span>{selectedSpec.dimension}d vectors</span>
                {selectedSpec.cost_per_1m_tokens !== null && (
                  <span>${selectedSpec.cost_per_1m_tokens}/1M tokens</span>
                )}
                {selectedSpec.id === activeSpecId && (
                  <span className="text-green-600 dark:text-green-400 font-medium">✓ currently active</span>
                )}
              </div>
            )}
          </div>

          {/* Save + Test */}
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <button
                disabled={!canSave || saving}
                onClick={handleSave}
                className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {saving ? "Saving…" : willSwitch ? "Save & Switch Model" : "Save"}
              </button>
              <button
                disabled={testing}
                onClick={handleTest}
                className="flex items-center gap-1.5 px-4 py-2 rounded-lg text-sm font-medium border border-border bg-background hover:bg-accent transition-colors disabled:opacity-50"
              >
                <span className={`material-symbols-outlined text-base ${testing ? "animate-spin" : ""}`}>
                  {testing ? "progress_activity" : "network_check"}
                </span>
                {testing ? "Testing…" : "Test Connection"}
              </button>
              {saved && (
                <span className="text-sm text-green-600 dark:text-green-400 flex items-center gap-1">
                  <span className="material-symbols-outlined text-sm">check_circle</span>
                  Saved
                </span>
              )}
            </div>
            {testResult && (
              <div className={`flex items-start gap-2 px-3 py-2 rounded-lg text-xs ${
                testResult.success
                  ? "bg-green-500/10 text-green-700 dark:text-green-400"
                  : "bg-destructive/10 text-destructive"
              }`}>
                <span className="material-symbols-outlined text-sm mt-0.5">
                  {testResult.success ? "check_circle" : "error"}
                </span>
                {testResult.message}
              </div>
            )}
            {error && <p className="text-xs text-destructive">{error}</p>}
          </div>
        </div>
      )}
    </div>
  );
}
