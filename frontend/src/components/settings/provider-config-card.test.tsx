/**
 * Omniroute's /v1/models list is unauthenticated. The card used to block Fetch Models
 * whenever the key field was empty or a bullet mask, with "Enter the API key above to
 * list models."
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ProviderConfigCard } from "./provider-config-card";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

function install(overrides: Record<string, unknown> = {}) {
  mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
    const p = String(path);
    if (p === "/api/settings" && opts?.method !== "PUT") {
      return Promise.resolve({
        llm_provider: "omniroute",
        llm_model_id: "nosiaht",
        llm_base_url: "https://ai.nosiaht.com/v1",
        ...overrides,
      });
    }
    if (p === "/api/settings/fetch-models") {
      return Promise.resolve({ models: ["nosiaht", "glm/glm-5.3"] });
    }
    return Promise.resolve({});
  }) as unknown as typeof api);
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("Omniroute Fetch Models", () => {
  it("lists models without a plaintext API key", async () => {
    install();
    const user = userEvent.setup();
    render(<ProviderConfigCard capability="llm" testEndpoint="/api/settings/test-llm" />);

    await waitFor(() => expect(screen.getByDisplayValue("https://ai.nosiaht.com/v1")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Fetch Models/i }));

    await waitFor(() =>
      expect(mockApi.mock.calls.some(([path]) => path === "/api/settings/fetch-models")).toBe(true)
    );
    const fetchCall = mockApi.mock.calls.find(([path]) => path === "/api/settings/fetch-models");
    const body = (fetchCall?.[1] as { body: { api_key: string; base_url: string } }).body;
    expect(body.api_key).toBe("");
    expect(body.base_url).toBe("https://ai.nosiaht.com/v1");
    expect(screen.queryByText(/Enter the API key above to list models/i)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/2 models loaded/i)).toBeInTheDocument());
  });

  it("lists models when the saved key is only a bullet mask", async () => {
    install({ llm_api_key__omniroute: "••••••••491f" });
    const user = userEvent.setup();
    render(<ProviderConfigCard capability="llm" testEndpoint="/api/settings/test-llm" />);

    await waitFor(() => expect(screen.getByDisplayValue("••••••••491f")).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Fetch Models/i }));

    await waitFor(() =>
      expect(mockApi.mock.calls.some(([path]) => path === "/api/settings/fetch-models")).toBe(true)
    );
    expect(screen.queryByText(/Re-enter the API key to list models/i)).not.toBeInTheDocument();
  });
});
