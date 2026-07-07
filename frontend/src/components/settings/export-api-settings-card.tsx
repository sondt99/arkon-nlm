"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";

const PROVIDER_LABELS: Record<string, string> = {
  google: "Google",
  openai: "OpenAI",
  anthropic: "Anthropic",
  ollama: "Ollama",
  ninerouter: "9Router",
};

function InfoTip({ children }: { children: React.ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={<span className="inline-flex items-center cursor-help text-muted-foreground/60 hover:text-foreground transition-colors" />}
      >
        <span className="material-symbols-outlined" style={{ fontSize: 14 }}>info</span>
      </TooltipTrigger>
      <TooltipContent side="top" className="max-w-[260px] text-left">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

export function ExportApiSettingsCard() {
  // Enable/disable toggle
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [savingToggle, setSavingToggle] = useState(false);
  const [savedToggle, setSavedToggle] = useState(false);

  // Model summary (read-only — configured via Chatbot/LLM Provider cards above)
  const [modelProvider, setModelProvider] = useState("");
  const [modelId, setModelId] = useState("");
  const [isFallbackModel, setIsFallbackModel] = useState(false);

  // Generation parameters
  const [temperature, setTemperature] = useState(0.5);
  const [topP, setTopP] = useState(1);
  const [maxTokens, setMaxTokens] = useState("");
  const [savingParams, setSavingParams] = useState(false);
  const [savedParams, setSavedParams] = useState(false);

  // API key (shares Employee.mcp_token with MCP Desktop)
  const [token, setToken] = useState<string | null>(null);
  const [hasToken, setHasToken] = useState(false);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  async function load() {
    try {
      const data = await api<Record<string, unknown>>("/api/settings");
      const str = (k: string) => (typeof data[k] === "string" ? (data[k] as string) : "");

      const val = data["export_api_enabled"];
      setEnabled(val === undefined || val === null || val === "" || String(val).toLowerCase() !== "false");

      const chatbotProvider = str("chatbot_provider");
      if (chatbotProvider) {
        setModelProvider(chatbotProvider);
        setModelId(str("chatbot_model_id"));
        setIsFallbackModel(false);
      } else {
        setModelProvider(str("llm_provider"));
        setModelId(str("llm_model_id"));
        setIsFallbackModel(true);
      }

      const temp = str("export_api_temperature");
      if (temp) setTemperature(parseFloat(temp));
      const tp = str("export_api_top_p");
      if (tp) setTopP(parseFloat(tp));
      setMaxTokens(str("export_api_max_tokens"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    api<{ has_token: boolean }>("/api/my/mcp-token/status")
      .then((data) => setHasToken(data.has_token))
      .catch(() => {});
  }, []);

  async function toggleEnabled() {
    const next = !enabled;
    setEnabled(next);
    setSavingToggle(true);
    setSavedToggle(false);
    try {
      await api("/api/settings", {
        method: "PUT",
        body: { settings: { export_api_enabled: next ? "true" : "false" } },
      });
      setSavedToggle(true);
      setTimeout(() => setSavedToggle(false), 2000);
    } catch {
      setEnabled(!next);
    } finally {
      setSavingToggle(false);
    }
  }

  async function saveParams() {
    setSavingParams(true);
    setSavedParams(false);
    try {
      await api("/api/settings", {
        method: "PUT",
        body: {
          settings: {
            export_api_temperature: String(temperature),
            export_api_top_p: String(topP),
            export_api_max_tokens: maxTokens.trim(),
          },
        },
      });
      setSavedParams(true);
      setTimeout(() => setSavedParams(false), 2000);
    } finally {
      setSavingParams(false);
    }
  }

  async function handleGenerateToken() {
    setTokenLoading(true);
    try {
      const data = await api<{ token: string }>("/api/my/mcp-token", { method: "POST" });
      setToken(data.token);
      setHasToken(true);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed");
    } finally {
      setTokenLoading(false);
    }
  }

  async function handleRevokeToken() {
    if (!confirm("Revoke this token? Claude Desktop (MCP) will also disconnect — it uses the same token.")) return;
    try {
      await api("/api/my/mcp-token", { method: "DELETE" });
      setToken(null);
      setHasToken(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Failed");
    }
  }

  function handleCopyToken() {
    if (!token) return;
    navigator.clipboard.writeText(token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  return (
    <div className="bg-card rounded-xl border border-border shadow-sahara overflow-hidden">
      <div className="flex items-center gap-3 px-6 pt-5 pb-4">
        <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
          <span className="material-symbols-outlined text-primary text-base">webhook</span>
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-semibold text-foreground">Export API</h3>
          <p className="text-xs text-muted-foreground">REST access for external tools (n8n, Zapier, scripts)</p>
        </div>
      </div>

      {/* Enable/disable */}
      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium text-foreground">Enable Export API</span>
            <span className="text-xs text-muted-foreground">
              {enabled
                ? "External tools can chat with Victor/Ashley and search the wiki via /api/export/v1"
                : "Export API is off — /api/export/v1/* requests are rejected with 503"}
            </span>
          </div>

          <button
            onClick={toggleEnabled}
            disabled={loading || savingToggle}
            aria-pressed={enabled}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
              enabled ? "bg-primary" : "bg-muted"
            }`}
          >
            <span
              className={`pointer-events-none block h-4 w-4 rounded-full bg-white shadow-sm ring-0 transition-transform duration-200 ${
                enabled ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>

        {savedToggle && (
          <p className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
            <span className="material-symbols-outlined text-sm">check_circle</span>
            Saved
          </p>
        )}
      </div>

      {/* Model summary */}
      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex items-center justify-between gap-4">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-foreground">Model</span>
          <span className="text-xs text-muted-foreground">
            {isFallbackModel ? "Falling back to LLM Provider — configure a dedicated Chatbot Provider above to change." : "Uses the Chatbot Provider configured above."}
          </span>
        </div>
        <span className="shrink-0 inline-flex items-center gap-1.5 text-xs font-medium bg-muted px-2.5 py-1 rounded-full border border-border">
          {modelProvider ? (
            <>
              <span className="text-foreground">{PROVIDER_LABELS[modelProvider] ?? modelProvider}</span>
              {modelId && <span className="text-muted-foreground">· {modelId}</span>}
            </>
          ) : (
            <span className="text-muted-foreground">Not configured</span>
          )}
        </span>
      </div>

      {/* Generation parameters */}
      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-4">
        <span className="text-sm font-medium text-foreground">Generation Parameters</span>

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <InfoTip>
              Controls randomness of the reply. Lower values (near 0) make answers more focused and
              deterministic; higher values (near 1) make them more varied and creative.
            </InfoTip>
            <span className="text-xs font-mono text-muted-foreground tabular-nums">{temperature.toFixed(2)}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-foreground w-24 shrink-0">Temperature</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={temperature}
              onChange={(e) => setTemperature(parseFloat(e.target.value))}
              className="flex-1 accent-primary"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <InfoTip>
              Nucleus sampling — restricts word choices to the smallest set covering this cumulative
              probability. 1.0 considers all options; lower values narrow the choices to more likely words.
            </InfoTip>
            <span className="text-xs font-mono text-muted-foreground tabular-nums">{topP.toFixed(2)}</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-sm text-foreground w-24 shrink-0">Top-p</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={topP}
              onChange={(e) => setTopP(parseFloat(e.target.value))}
              className="flex-1 accent-primary"
            />
          </div>
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-1.5">
            <span className="text-sm text-foreground">Max tokens</span>
            <InfoTip>
              Upper limit on how long a reply can be, in tokens (~¾ of a word each). Leave empty to use
              the provider&apos;s default limit.
            </InfoTip>
          </div>
          <Input
            type="number"
            min={1}
            value={maxTokens}
            onChange={(e) => setMaxTokens(e.target.value)}
            placeholder="Provider default"
            className="bg-background max-w-[180px]"
          />
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={saveParams}
            disabled={savingParams}
            className="bg-primary text-primary-foreground px-4 py-1.5 rounded-lg text-xs font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {savingParams ? "Saving…" : "Save Parameters"}
          </button>
          {savedParams && (
            <span className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
              <span className="material-symbols-outlined text-sm">check_circle</span>
              Saved
            </span>
          )}
        </div>
      </div>

      {/* API key */}
      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-3">
        <span className="text-sm font-medium text-foreground">API Key</span>

        <div className="flex items-start gap-2 rounded-lg bg-amber-500/10 border border-amber-500/20 px-3 py-2.5 text-xs text-amber-700 dark:text-amber-400">
          <span className="material-symbols-outlined text-sm mt-0.5">warning</span>
          <span>This is your personal MCP token — the same one used to connect Claude Desktop. Regenerating it disconnects Desktop too.</span>
        </div>

        {token ? (
          <div className="flex flex-col gap-2">
            <div className="bg-[#3a302a] rounded-lg p-3 font-mono text-xs text-[#faf5ee] break-all">
              {token}
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleCopyToken}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-background hover:bg-accent transition-colors"
              >
                <span className="material-symbols-outlined text-sm">{copied ? "check" : "content_copy"}</span>
                {copied ? "Copied!" : "Copy Token"}
              </button>
              <button
                onClick={handleRevokeToken}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
              >
                <span className="material-symbols-outlined text-sm">vpn_key_off</span>
                Revoke
              </button>
            </div>
          </div>
        ) : hasToken ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-2 text-xs text-green-700 dark:text-green-400 bg-green-500/10 px-3 py-2 rounded-lg border border-green-500/20">
              <span className="material-symbols-outlined text-sm">check_circle</span>
              <span>Token is active. It was shown once at generation — it can&apos;t be retrieved again.</span>
            </div>
            <div className="flex gap-2">
              <button
                onClick={handleGenerateToken}
                disabled={tokenLoading}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border border-border bg-background hover:bg-accent transition-colors disabled:opacity-50"
              >
                <span className="material-symbols-outlined text-sm">refresh</span>
                {tokenLoading ? "Regenerating…" : "Regenerate Token"}
              </button>
              <button
                onClick={handleRevokeToken}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-destructive hover:bg-destructive/10 transition-colors"
              >
                <span className="material-symbols-outlined text-sm">vpn_key_off</span>
                Revoke
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={handleGenerateToken}
            disabled={tokenLoading}
            className="self-start bg-primary text-primary-foreground px-4 py-2 rounded-lg text-sm font-medium hover:bg-primary/90 transition-colors disabled:opacity-50"
          >
            {tokenLoading ? "Generating…" : "Generate API Key"}
          </button>
        )}
      </div>
    </div>
  );
}
