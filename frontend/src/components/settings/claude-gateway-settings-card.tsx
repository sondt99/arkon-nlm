"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { Input } from "@/components/ui/input";

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

export function ClaudeGatewaySettingsCard() {
  const [enabled, setEnabled] = useState(true);
  const [loading, setLoading] = useState(true);
  const [savingToggle, setSavingToggle] = useState(false);
  const [savedToggle, setSavedToggle] = useState(false);

  const [temperature, setTemperature] = useState(0.2);
  const [topP, setTopP] = useState(1);
  const [maxTokens, setMaxTokens] = useState("");
  const [savingParams, setSavingParams] = useState(false);
  const [savedParams, setSavedParams] = useState(false);
  const [paramsError, setParamsError] = useState("");
  const [loadError, setLoadError] = useState("");

  const origin = typeof window !== "undefined" ? window.location.origin : "<this server's URL>";

  /** Bumped by Retry. The load lives in the effect rather than in a `load()` the effect
   *  calls, so the effect owns cancellation too: a card unmounted mid-request no longer
   *  writes into a torn-down tree. */
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const data = await api<Record<string, unknown>>("/api/settings");
        if (cancelled) return;
        const str = (k: string) => (typeof data[k] === "string" ? (data[k] as string) : "");

        setLoadError("");
        const val = data["claude_gateway_enabled"];
        setEnabled(val === undefined || val === null || val === "" || String(val).toLowerCase() !== "false");

        const temp = str("claude_gateway_temperature");
        if (temp) setTemperature(parseFloat(temp));
        const tp = str("claude_gateway_top_p");
        if (tp) setTopP(parseFloat(tp));
        setMaxTokens(str("claude_gateway_max_tokens"));
      } catch (err) {
        // Previously a try/finally with no catch: a failed read stopped the spinner and left
        // `enabled` asserting its `useState(true)` default, so the next toggle wrote the
        // opposite of what the server held.
        if (cancelled) return;
        setLoadError(err instanceof Error ? err.message : "Failed to load settings");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => { cancelled = true; };
  }, [reloadToken]);

  const retryLoad = () => {
    setLoading(true);
    setReloadToken((n) => n + 1);
  };

  async function toggleEnabled() {
    const next = !enabled;
    setEnabled(next);
    setSavingToggle(true);
    setSavedToggle(false);
    try {
      await api("/api/settings", {
        method: "PUT",
        body: { settings: { claude_gateway_enabled: next ? "true" : "false" } },
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
    setParamsError("");
    try {
      await api("/api/settings", {
        method: "PUT",
        body: {
          settings: {
            claude_gateway_temperature: String(temperature),
            claude_gateway_top_p: String(topP),
            claude_gateway_max_tokens: maxTokens.trim(),
          },
        },
      });
      setSavedParams(true);
      setTimeout(() => setSavedParams(false), 2000);
    } catch (err) {
      // A bare try/finally reported a rejected save as "spinner stopped": no error,
      // no "Saved", and the only trace was an unhandled rejection in the console.
      setParamsError(err instanceof Error ? err.message : "Failed to save parameters");
    } finally {
      setSavingParams(false);
    }
  }

  return (
    <div className="border-t border-border/60">
      {/* Enable/disable */}
      <div className="px-6 pb-5 pt-5 flex flex-col gap-4">
        <div className="flex items-center justify-between gap-4">
          <div className="flex flex-col gap-0.5">
            <span className="text-sm font-medium text-foreground">Enable Claude Code Gateway</span>
            <span className="text-xs text-muted-foreground">
              {enabled
                ? "Claude Code (and other Anthropic API clients) can point ANTHROPIC_BASE_URL here"
                : "Gateway is off — /api/claude-gateway/v1/* requests are rejected with an error"}
            </span>
          </div>

          <button
            onClick={toggleEnabled}
            disabled={loading || savingToggle || !!loadError}
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

        {loadError && (
          <p className="text-xs text-destructive flex items-center gap-1">
            <span className="material-symbols-outlined text-sm">error</span>
            Couldn&apos;t read the current setting ({loadError}) — the toggle is disabled so it
            cannot write the wrong value.
            <button onClick={retryLoad} className="underline underline-offset-2 hover:no-underline">
              Retry
            </button>
          </p>
        )}
      </div>

      {/* Setup instructions */}
      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-2">
        <span className="text-sm font-medium text-foreground">Client setup</span>
        <p className="text-xs text-muted-foreground leading-relaxed">
          Point Claude Code at this server instead of api.anthropic.com. In <code className="bg-muted px-1 py-0.5 rounded">~/.claude/settings.json</code>:
        </p>
        <pre className="bg-[#3a302a] text-[#faf5ee] rounded-lg p-3 text-xs font-mono overflow-x-auto">
{`{
  "env": {
    "ANTHROPIC_BASE_URL": "${origin}/api/claude-gateway",
    "ANTHROPIC_AUTH_TOKEN": "<your API key>"
  }
}`}
        </pre>
        <p className="text-xs text-muted-foreground">
          Uses the same API key as <strong>Export API</strong> below — generate or copy it there.
        </p>
      </div>

      {/* Generation parameters */}
      <div className="px-6 pb-5 border-t border-border/60 pt-4 flex flex-col gap-4">
        <span className="text-sm font-medium text-foreground">Generation Parameters</span>
        <p className="text-xs text-muted-foreground -mt-2">
          Optional overrides — applied only when set here, otherwise Claude Code&apos;s own request values are used as-is.
        </p>

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
              whatever Claude Code itself requested.
            </InfoTip>
          </div>
          <Input
            type="number"
            min={1}
            value={maxTokens}
            onChange={(e) => setMaxTokens(e.target.value)}
            placeholder="Use Claude Code's request value"
            className="bg-background max-w-[220px]"
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
          {paramsError && (
            <span className="text-xs text-destructive flex items-center gap-1">
              <span className="material-symbols-outlined text-sm">error</span>
              {paramsError}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
