/**
 * #91 — the Export API and Claude Gateway cards had `try`/`finally` with no `catch`, in both
 * `saveParams()` and `load()`.
 *
 * A rejected save just stopped the spinner: no error, no "Saved", and the only trace was an
 * unhandled rejection in the console. A rejected load was worse — it left `enabled` asserting
 * its `useState(true)` default while the server may have had the feature switched off, so the
 * next toggle click wrote the *opposite* of what was actually stored.
 *
 * Both cards are the same shape, so both are driven through the same table of cases.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ClaudeGatewaySettingsCard } from "./claude-gateway-settings-card";
import { ExportApiSettingsCard } from "./export-api-settings-card";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

const mockApi = vi.mocked(api);

const CARDS = [
  { name: "Claude Gateway", Card: ClaudeGatewaySettingsCard, flag: "claude_gateway_enabled" },
  { name: "Export API", Card: ExportApiSettingsCard, flag: "export_api_enabled" },
] as const;

/** Every PUT /api/settings payload the card issued. */
const puts = () =>
  mockApi.mock.calls
    .filter(([path, opts]) => path === "/api/settings" && (opts as { method?: string })?.method === "PUT")
    .map(([, opts]) => (opts as { body: { settings: Record<string, string> } }).body.settings);

beforeEach(() => {
  vi.clearAllMocks();
});

describe.each(CARDS)("$name card — failure surfacing (#91)", ({ Card, flag }) => {
  it("shows an error when saving the generation parameters fails", async () => {
    mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
      if (path === "/api/settings" && opts?.method === "PUT") {
        return Promise.reject(new Error("upstream refused"));
      }
      if (path === "/api/settings") return Promise.resolve({ [flag]: "true" });
      return Promise.resolve({});
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<Card />);

    await user.click(await screen.findByRole("button", { name: /Save Parameters/i }));

    await waitFor(() => expect(screen.getByText("upstream refused")).toBeInTheDocument());
    // And definitely not a success claim.
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("refuses to write the toggle when the stored value could not be read", async () => {
    // The load fails, so `enabled` is still its `useState(true)` default. The server may hold
    // "false"; writing the negation of an unknown value is the actual defect.
    mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
      if (path === "/api/settings" && opts?.method !== "PUT") {
        return Promise.reject(new Error("settings unavailable"));
      }
      return Promise.resolve({});
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<Card />);

    await waitFor(() =>
      expect(screen.getByText(/couldn't read the current setting/i)).toBeInTheDocument()
    );

    const toggle = screen.getByRole("button", { pressed: true });
    expect(toggle).toBeDisabled();
    await user.click(toggle);
    expect(puts()).toEqual([]);
  });

  it("re-reads the setting when Retry is pressed", async () => {
    let attempt = 0;
    mockApi.mockImplementation(((path: string, opts?: { method?: string }) => {
      if (path === "/api/settings" && opts?.method !== "PUT") {
        attempt += 1;
        if (attempt === 1) return Promise.reject(new Error("settings unavailable"));
        return Promise.resolve({ [flag]: "false" });
      }
      return Promise.resolve({});
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<Card />);

    await waitFor(() =>
      expect(screen.getByText(/couldn't read the current setting/i)).toBeInTheDocument()
    );
    await user.click(screen.getByRole("button", { name: "Retry" }));

    await waitFor(() =>
      expect(screen.queryByText(/couldn't read the current setting/i)).not.toBeInTheDocument()
    );
    // The stored value was "false", which the failed first attempt had been misreporting.
    expect(screen.getByRole("button", { pressed: false })).toBeEnabled();
  });

  it("confirms the save when it succeeds", async () => {
    mockApi.mockImplementation(((path: string) => {
      if (path === "/api/settings") return Promise.resolve({ [flag]: "true" });
      return Promise.resolve({});
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<Card />);
    await user.click(await screen.findByRole("button", { name: /Save Parameters/i }));

    await waitFor(() => expect(screen.getByText("Saved")).toBeInTheDocument());
  });
});
