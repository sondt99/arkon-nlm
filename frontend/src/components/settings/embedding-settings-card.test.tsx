/**
 * #91 — saving a non-Ollama embedding provider wiped the shared `embedding_base_url`, and the
 * two masked-secret guards disagreed with each other.
 *
 * `embedding_base_url` is a single global key (`app/services/config_service.py`), read once for
 * whichever provider is active. The card treated it as per-provider: `handleProviderSelect`
 * reset the field to the provider's packaged default (or blank), and `handleSave` wrote it
 * unconditionally. Clicking over to OpenAI to compare models, clicking back, and saving
 * replaced a hand-configured Ollama host with the packaged default — and the key is not
 * sensitive, so an empty string really does erase it.
 *
 * The mask guards were `apiKey.includes("•")` in one place and `apiKey.startsWith("••••")` in
 * another. The backend masks a value of eight characters or fewer as `"•" * len`, so a short
 * secret masks to `"•••"` — which `startsWith("••••")` does not recognise, and that mask was
 * forwarded to `/api/settings/fetch-models` as if it were the real credential.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EmbeddingSettingsCard } from "./embedding-settings-card";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const OLLAMA_HOST = "http://10.0.0.5:11434/v1";

const CATALOG = {
  active_spec_id: "ollama/nomic-embed-text",
  specs: [
    {
      id: "ollama/nomic-embed-text",
      provider: "ollama",
      model_id: "nomic-embed-text",
      dimension: 768,
      label: "Nomic",
      cost_per_1m_tokens: null,
      notes: null,
      api_key_configured: false,
    },
    {
      id: "openai/text-embedding-3-small",
      provider: "openai",
      model_id: "text-embedding-3-small",
      dimension: 1536,
      label: "OpenAI small",
      cost_per_1m_tokens: 2,
      notes: null,
      api_key_configured: true,
    },
  ],
};

const STATUS = {
  active_spec_id: "ollama/nomic-embed-text",
  total_pages: 10,
  embedded_pages: 10,
  current_job: null,
};

/** The `settings` payload of every PUT /api/settings the card has issued. */
function savedSettings(): Record<string, string>[] {
  return mockApi.mock.calls
    .filter(([path, opts]) => path === "/api/settings" && (opts as { method?: string })?.method === "PUT")
    .map(([, opts]) => (opts as { body: { settings: Record<string, string> } }).body.settings);
}

function install(overrides: Record<string, unknown> = {}) {
  mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
    const p = String(path);
    if (p === "/api/settings/embeddings/catalog") return Promise.resolve(CATALOG);
    if (p === "/api/settings/embeddings/status") return Promise.resolve(STATUS);
    if (p === "/api/settings" && opts?.method !== "PUT") {
      return Promise.resolve({ embedding_base_url: OLLAMA_HOST, ...overrides });
    }
    return Promise.resolve({});
  }) as unknown as typeof api);
}

beforeEach(() => {
  vi.clearAllMocks();
});

/** Every `embedding_base_url` value the card has tried to persist. */
const persistedBaseUrls = () =>
  savedSettings()
    .filter((payload) => "embedding_base_url" in payload)
    .map((payload) => payload.embedding_base_url);

describe("shared embedding_base_url (#91)", () => {
  it("restores the stored endpoint when the user returns to the active provider", async () => {
    install();
    const user = userEvent.setup();
    render(<EmbeddingSettingsCard />);

    await waitFor(() => expect(screen.getByDisplayValue(OLLAMA_HOST)).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /OpenAI/i }));
    await user.click(screen.getByRole("button", { name: /Ollama/i }));

    // Not `http://host.docker.internal:11434/v1` — re-applying the packaged default is how a
    // hand-configured host got staged for overwrite in the first place.
    await waitFor(() => expect(screen.getByDisplayValue(OLLAMA_HOST)).toBeInTheDocument());
  });

  it("persists nothing for the shared endpoint when the user only browsed providers", async () => {
    install();
    const user = userEvent.setup();
    render(<EmbeddingSettingsCard />);

    await waitFor(() => expect(screen.getByDisplayValue(OLLAMA_HOST)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /OpenAI/i }));
    await user.click(screen.getByRole("button", { name: /Ollama/i }));
    await user.click(screen.getByRole("button", { name: "Save" }));

    // A round trip through another provider changed nothing, so nothing about the endpoint
    // may be written — and certainly not a blank or a default.
    await waitFor(() => expect(screen.getByDisplayValue(OLLAMA_HOST)).toBeInTheDocument());
    expect(persistedBaseUrls()).toEqual([]);
  });

  it("writes the endpoint the user actually typed", async () => {
    install();
    const user = userEvent.setup();
    render(<EmbeddingSettingsCard />);

    const field = await screen.findByDisplayValue(OLLAMA_HOST);
    await user.clear(field);
    await user.type(field, "http://ollama.internal:11434/v1");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(persistedBaseUrls()).toEqual(["http://ollama.internal:11434/v1"])
    );
  });

  it("writes a blank endpoint when the save genuinely switches to a provider that has none", async () => {
    install();
    const user = userEvent.setup();
    render(<EmbeddingSettingsCard />);

    await waitFor(() => expect(screen.getByDisplayValue(OLLAMA_HOST)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /OpenAI/i }));
    await user.click(screen.getByRole("button", { name: "Save & Switch Model" }));

    // The guard must not be so broad that a real switch cannot clear the endpoint.
    await waitFor(() => expect(persistedBaseUrls()).toEqual([""]));
  });
});

describe("masked secret guard (#91)", () => {
  it("never forwards a short bullet mask to fetch-models as a real credential", async () => {
    // A stored secret of three characters comes back as "•••" — shorter than the four bullets
    // the old `startsWith` guard looked for.
    install({ "embedding_api_key__ollama": "•••" });
    const user = userEvent.setup();
    render(<EmbeddingSettingsCard />);

    await waitFor(() => expect(screen.getByDisplayValue("•••")).toBeInTheDocument());

    await user.click(screen.getByRole("button", { name: /Fetch Models/i }));
    await waitFor(() =>
      expect(
        mockApi.mock.calls.some(([path]) => path === "/api/settings/fetch-models")
      ).toBe(true)
    );

    const fetchCall = mockApi.mock.calls.find(([path]) => path === "/api/settings/fetch-models");
    const body = (fetchCall?.[1] as { body: { api_key: string } }).body;
    expect(body.api_key).toBe("");
  });

  it("never submits a bullet mask back as the stored key", async () => {
    install({ "embedding_api_key__ollama": "•••" });
    const user = userEvent.setup();
    render(<EmbeddingSettingsCard />);

    await waitFor(() => expect(screen.getByDisplayValue("•••")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Save" }));

    // Round-tripping the mask would have the backend store "•••" as the key: its own
    // skip-if-masked check also only recognises four or more bullets.
    await waitFor(() => expect(screen.getByDisplayValue("•••")).toBeInTheDocument());
    for (const payload of savedSettings()) {
      expect(payload).not.toHaveProperty("embedding_api_key__ollama");
    }
  });
});
