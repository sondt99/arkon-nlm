"use client";

import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";

// ── Provider definitions ──────────────────────────────────────────────────────

type ProviderDef = {
  value: string;
  label: string;
  icon: string;
  staticModels: string[];  // fallback list; empty = must fetch
  needsKey: boolean;
  canFetch: boolean;       // supports /v1/models endpoint
  defaultBaseUrl?: string;
  keyPlaceholder?: string;
  modelPlaceholder?: string;
};

const LLM_PROVIDERS: ProviderDef[] = [
  {
    value: "google",
    label: "Google",
    icon: "g_mobiledata",
    staticModels: ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"],
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "AIza...",
  },
  {
    value: "openai",
    label: "OpenAI",
    icon: "smart_toy",
    staticModels: ["gpt-4o", "gpt-4o-mini", "gpt-4.1-mini", "gpt-4.1-nano", "o3-mini"],
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "sk-...",
  },
  {
    value: "anthropic",
    label: "Anthropic",
    icon: "psychology",
    staticModels: ["claude-sonnet-4-20250514", "claude-haiku-4-20250514", "claude-opus-4-5"],
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "sk-ant-...",
  },
  {
    value: "ollama",
    label: "Ollama",
    icon: "computer",
    staticModels: ["qwen2.5:14b", "qwen2.5:7b", "llama3.1:8b", "mistral:7b"],
    needsKey: false,
    canFetch: true,
    defaultBaseUrl: "http://host.docker.internal:11434/v1",
    keyPlaceholder: "ollama",
    modelPlaceholder: "e.g. qwen2.5:14b",
  },
  {
    value: "ninerouter",
    label: "9Router",
    icon: "hub",
    staticModels: [],
    needsKey: true,
    canFetch: true,
    keyPlaceholder: "bearer-token",
    modelPlaceholder: "select or type model ID",
  },
];

const VISION_PROVIDERS: ProviderDef[] = [
  {
    value: "google",
    label: "Google",
    icon: "g_mobiledata",
    staticModels: ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"],
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "AIza...",
  },
  {
    value: "openai",
    label: "OpenAI",
    icon: "smart_toy",
    staticModels: ["gpt-4o", "gpt-4o-mini"],
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "sk-...",
  },
  {
    value: "anthropic",
    label: "Anthropic",
    icon: "psychology",
    staticModels: ["claude-sonnet-4-20250514", "claude-haiku-4-20250514"],
    needsKey: true,
    canFetch: false,
    keyPlaceholder: "sk-ant-...",
  },
  {
    value: "ollama",
    label: "Ollama",
    icon: "computer",
    staticModels: ["llava:7b", "llava:13b", "llava-llama3", "moondream"],
    needsKey: false,
    canFetch: true,
    defaultBaseUrl: "http://host.docker.internal:11434/v1",
    keyPlaceholder: "ollama",
    modelPlaceholder: "e.g. llava:7b",
  },
  {
    value: "ninerouter",
    label: "9Router",
    icon: "hub",
    staticModels: [],
    needsKey: true,
    canFetch: true,
    keyPlaceholder: "bearer-token",
    modelPlaceholder: "select or type model ID",
  },
];

const ALL_PROVIDERS = { llm: LLM_PROVIDERS, vision: VISION_PROVIDERS, chatbot: LLM_PROVIDERS };
const PROVIDER_NAMES = ["google", "openai", "anthropic", "ollama", "ninerouter"] as const;

// ── Props ─────────────────────────────────────────────────────────────────────

type Props = {
  title: string;
  description: string;
  icon: string;
  capability: "llm" | "vision" | "chatbot";
  testEndpoint: string;
  /** Show a badge explaining this provider is optional / has a fallback */
  fallbackNote?: string;
};

// ── Component ─────────────────────────────────────────────────────────────────

export function ProviderConfigCard({ title, description, icon, capability, testEndpoint, fallbackNote }: Props) {
  const providers = ALL_PROVIDERS[capability];

  const [provider, setProvider] = useState("");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKeys, setApiKeys] = useState<Record<string, string>>({});
  const [customModel, setCustomModel] = useState(false);

  // Fetch state
  const [fetchedModels, setFetchedModels] = useState<string[] | null>(null);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState("");

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);

  useEffect(() => { void load(); }, []);

  async function load() {
    try {
      const data = await api<Record<string, unknown>>("/api/settings");
      const str = (k: string) => (typeof data[k] === "string" ? (data[k] as string) : "");

      setProvider(str(`${capability}_provider`));
      setModel(str(`${capability}_model_id`));
      setBaseUrl(str(`${capability}_base_url`));

      const keys: Record<string, string> = {};
      for (const p of PROVIDER_NAMES) {
        const v = str(`${capability}_api_key__${p}`);
        if (v) keys[p] = v;
      }
      setApiKeys(keys);
    } catch {
      // use defaults
    } finally {
      setLoading(false);
    }
  }

  async function handleFetchModels() {
    if (!baseUrl) return;
    setFetching(true);
    setFetchError("");
    setFetchedModels(null);
    try {
      const apiKey = apiKeys[provider] ?? "";
      const res = await api<{ models: string[] }>("/api/settings/fetch-models", {
        method: "POST",
        body: { base_url: baseUrl, api_key: apiKey.startsWith("••••") ? "" : apiKey },
      });
      setFetchedModels(res.models);
      setModel("");
      setCustomModel(false);
    } catch (e) {
      setFetchError(e instanceof Error ? e.message : "Failed to fetch models");
    } finally {
      setFetching(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    setSaveError("");
    try {
      const settings: Record<string, string> = {
        [`${capability}_provider`]: provider,
        [`${capability}_model_id`]: model,
        [`${capability}_base_url`]: baseUrl,
      };
      for (const p of PROVIDER_NAMES) {
        const v = apiKeys[p] ?? "";
        if (v && !v.startsWith("••••")) settings[`${capability}_api_key__${p}`] = v;
        else if (!v) settings[`${capability}_api_key__${p}`] = "";
      }
      await api("/api/settings", { method: "PUT", body: { settings } });
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
      await load();
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    try {
      const res = await api<{ success: boolean; message: string }>(testEndpoint, { method: "POST" });
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: e instanceof Error ? e.message : "Test failed" });
    } finally {
      setTesting(false);
    }
  }

  if (loading) {
    return (
      <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
        <div className="flex items-center gap-3 px-6 pt-5 pb-4">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
            <span className="material-symbols-outlined text-primary text-base">{icon}</span>
          </div>
          <div>
            <h3 className="text-base font-semibold text-foreground">{title}</h3>
            <p className="text-xs text-muted-foreground">Loading…</p>
          </div>
        </div>
      </div>
    );
  }

  const def = providers.find((p) => p.value === provider) ?? null;
  const apiKey = apiKeys[provider] ?? "";
  const isMasked = apiKey.includes("•");
  const modelList = fetchedModels ?? def?.staticModels ?? [];
  const isModelInList = modelList.includes(model);
  const showCustomInput = customModel || (model.length > 0 && !isModelInList);

  function handleProviderSelect(val: string) {
    const next = providers.find((p) => p.value === val)!;
    setProvider(val);
    setBaseUrl(next.defaultBaseUrl ?? "");
    setCustomModel(false);
    setModel("");
    setFetchedModels(null);
    setFetchError("");
    setTestResult(null);
  }

  function handleModelSelect(val: string) {
    if (val === "__custom__") {
      setCustomModel(true);
      setModel("");
    } else {
      setCustomModel(false);
      setModel(val);
    }
  }

  return (
    <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-6 pt-5 pb-4">
        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
          <span className="material-symbols-outlined text-primary text-base">{icon}</span>
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h3 className="text-base font-semibold text-foreground">{title}</h3>
            {fallbackNote && (
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-muted text-muted-foreground border border-border">
                {fallbackNote}
              </span>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>

      {/* Provider selector */}
      <div className="px-6 pb-4">
        <div className={`grid gap-2 ${capability === "chatbot" ? "grid-cols-3 sm:grid-cols-6" : "grid-cols-3 sm:grid-cols-5"}`}>
          {/* Chatbot: "None" option = fall back to LLM provider */}
          {capability === "chatbot" && (
            <button
              onClick={() => { setProvider(""); setModel(""); setBaseUrl(""); setFetchedModels(null); setFetchError(""); setTestResult(null); }}
              className={`relative flex flex-col items-center gap-1.5 py-3 px-2 rounded-lg border transition-all text-xs font-medium ${
                provider === ""
                  ? "border-primary bg-primary/8 text-primary"
                  : "border-border bg-background text-muted-foreground hover:border-primary/40 hover:text-foreground"
              }`}
            >
              <span className="material-symbols-outlined text-xl">
                device_hub
              </span>
              LLM fallback
              {provider === "" && (
                <span className="text-[9px] uppercase tracking-wide bg-primary/15 text-primary px-1.5 py-0.5 rounded-full font-semibold">
                  Active
                </span>
              )}
            </button>
          )}
          {providers.map((p) => {
            const active = provider === p.value;
            const hasSavedKey = !!apiKeys[p.value];
            return (
              <button
                key={p.value}
                onClick={() => handleProviderSelect(p.value)}
                className={`relative flex flex-col items-center gap-1.5 py-3 px-2 rounded-lg border transition-all text-xs font-medium ${
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

      {/* Chatbot fallback notice */}
      {capability === "chatbot" && !provider && (
        <div className="px-6 pb-5 border-t border-border/60 pt-4">
          <div className="flex items-start gap-3 rounded-lg bg-muted/50 border border-border px-4 py-3">
            <span className="material-symbols-outlined text-muted-foreground text-base mt-0.5">info</span>
            <p className="text-xs text-muted-foreground leading-relaxed">
              Chatbot will use the <strong>LLM Provider</strong> configured above.
              Select a dedicated provider here if you want to use a different model for chat (e.g. a faster or cheaper one).
            </p>
          </div>
        </div>
      )}

      {/* Config fields */}
      {def && (
        <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-4">

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
              />
              {def.canFetch && (
                <button
                  disabled={!baseUrl || fetching}
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
                {fetchedModels.length} models loaded
              </p>
            )}
          </div>

          {/* 2. API Key */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">
              API Key
              {!def.needsKey && (
                <span className="ml-1.5 text-muted-foreground font-normal">(optional)</span>
              )}
              {!!apiKeys[provider] && (
                <span className="ml-2 text-green-600 dark:text-green-400 font-normal">✓ saved</span>
              )}
            </Label>
            <Input
              type={isMasked ? "text" : "password"}
              value={apiKey}
              onChange={(e) => setApiKeys((prev) => ({ ...prev, [provider]: e.target.value }))}
              onFocus={() => { if (isMasked) setApiKeys((prev) => ({ ...prev, [provider]: "" })); }}
              placeholder={def.keyPlaceholder ?? "API key"}
              className="bg-background"
            />
          </div>

          {/* 3. Model selection */}
          <div className="flex flex-col gap-1.5">
            <Label className="text-xs">Model</Label>
            {def.canFetch && fetchedModels === null ? (
              <div className="h-9 rounded-md border border-dashed border-border bg-background/50 px-3 flex items-center text-xs text-muted-foreground">
                {baseUrl ? "Click \"Fetch Models\" to load available models" : "Enter Base URL above first"}
              </div>
            ) : !showCustomInput ? (
              <select
                value={model}
                onChange={(e) => handleModelSelect(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-3 text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value="">Select a model…</option>
                {modelList.map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
                <option value="__custom__">Other (type manually)…</option>
              </select>
            ) : (
              <div className="flex gap-2">
                <Input
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                  placeholder={def.modelPlaceholder ?? "model-id"}
                  className="bg-background flex-1"
                  autoFocus
                />
                <button
                  onClick={() => { setCustomModel(false); setModel(""); }}
                  className="text-xs text-muted-foreground hover:text-foreground px-2"
                >
                  ← list
                </button>
              </div>
            )}
          </div>

          {/* Save + Test */}
          <div className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <button
                onClick={handleSave}
                disabled={saving}
                className="bg-primary text-primary-foreground px-5 py-2 rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
              >
                {saving ? "Saving…" : "Save"}
              </button>
              <button
                disabled={testing || !provider}
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
            {saveError && (
              <div className="text-xs text-destructive bg-destructive/10 px-3 py-2 rounded-lg flex items-center gap-2">
                <span className="material-symbols-outlined text-sm">error</span>
                {saveError}
              </div>
            )}
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
          </div>
        </div>
      )}
    </div>
  );
}
