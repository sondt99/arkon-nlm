/**
 * #91 — switching conversations could display the wrong conversation's messages.
 *
 * `loadMessages` wrote `setMessages(...)` unconditionally, with no abort and no check that
 * the conversation it was loading for was still the selected one. Click a slow conversation
 * A and then B and the responses arrive out of click order: B rendered, then A's late
 * response overwrote it. The sidebar highlighted B while the transcript was A's, and the next
 * message the user sent was posted to B's id but appended to the visible A transcript.
 *
 * The invariant these tests pin: whatever the sidebar says is selected is the *only*
 * conversation whose messages may reach the transcript.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import ChatPage from "./page";
import { api } from "@/lib/api";

vi.mock("@/lib/api", () => ({ api: vi.fn() }));

// The reveal animation reads matchMedia; jsdom does not implement it.
vi.stubGlobal(
  "matchMedia",
  vi.fn().mockReturnValue({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() })
);

const mockApi = vi.mocked(api);

const CONVERSATIONS = [
  { id: "conv-a", title: "Alpha thread", scope_type: "global", scope_id: null, created_at: "", updated_at: "" },
  { id: "conv-b", title: "Bravo thread", scope_type: "global", scope_id: null, created_at: "", updated_at: "" },
];

function message(id: string, content: string) {
  return { id, role: "assistant" as const, content, sources: null, created_at: "" };
}

function deferred<T>() {
  let release!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    release = res;
  });
  return { promise, release };
}

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  // jsdom has no layout, so scrollIntoView is missing on the autoscroll anchor.
  Element.prototype.scrollIntoView = vi.fn();
});

describe("conversation switching (#91)", () => {
  it("keeps the selected conversation's transcript when a slower earlier request lands last", async () => {
    const slowA = deferred<ReturnType<typeof message>[]>();

    mockApi.mockImplementation(((path: string) => {
      if (path === "/api/chat/conversations") return Promise.resolve(CONVERSATIONS);
      if (path === "/api/chat/conversations/conv-a/messages") return slowA.promise;
      if (path === "/api/chat/conversations/conv-b/messages") {
        return Promise.resolve([message("m-b", "BRAVO CONTENT")]);
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<ChatPage />);

    await user.click(await screen.findByText("Alpha thread"));
    await user.click(await screen.findByText("Bravo thread"));

    await waitFor(() => expect(screen.getByText("BRAVO CONTENT")).toBeInTheDocument());

    // A's request finally answers, long after the user moved on.
    slowA.release([message("m-a", "ALPHA CONTENT")]);
    await waitFor(() => expect(screen.getByText("BRAVO CONTENT")).toBeInTheDocument());
    expect(screen.queryByText("ALPHA CONTENT")).not.toBeInTheDocument();
  });

  it("never shows the previous conversation's messages under the newly selected one", async () => {
    const slowB = deferred<ReturnType<typeof message>[]>();

    mockApi.mockImplementation(((path: string) => {
      if (path === "/api/chat/conversations") return Promise.resolve(CONVERSATIONS);
      if (path === "/api/chat/conversations/conv-a/messages") {
        return Promise.resolve([message("m-a", "ALPHA CONTENT")]);
      }
      if (path === "/api/chat/conversations/conv-b/messages") return slowB.promise;
      return Promise.resolve([]);
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<ChatPage />);

    await user.click(await screen.findByText("Alpha thread"));
    await waitFor(() => expect(screen.getByText("ALPHA CONTENT")).toBeInTheDocument());

    // While B is in flight the transcript must not still be showing A's messages.
    await user.click(screen.getByText("Bravo thread"));
    await waitFor(() => expect(screen.queryByText("ALPHA CONTENT")).not.toBeInTheDocument());

    slowB.release([message("m-b", "BRAVO CONTENT")]);
    await waitFor(() => expect(screen.getByText("BRAVO CONTENT")).toBeInTheDocument());
  });

  it("aborts the outgoing conversation's request instead of paying for a response it discards", async () => {
    const signals: (AbortSignal | undefined)[] = [];

    mockApi.mockImplementation(((path: string, options?: { signal?: AbortSignal }) => {
      if (path === "/api/chat/conversations") return Promise.resolve(CONVERSATIONS);
      if (path.endsWith("/messages")) {
        signals.push(options?.signal);
        return new Promise(() => {}); // never settles
      }
      return Promise.resolve([]);
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<ChatPage />);

    await user.click(await screen.findByText("Alpha thread"));
    await waitFor(() => expect(signals).toHaveLength(1));
    await user.click(screen.getByText("Bravo thread"));

    await waitFor(() => expect(signals[0]?.aborted).toBe(true));
    expect(signals[1]?.aborted).toBe(false);
  });
});

describe("transcript load failure (#91)", () => {
  it("says the load failed instead of rendering the conversation as empty", async () => {
    // `.catch(() => setMessages([]))` rendered a failed fetch as "No messages yet. Say
    // something!" — the user's reflex is to retype a message they think was lost.
    mockApi.mockImplementation(((path: string) => {
      if (path === "/api/chat/conversations") return Promise.resolve(CONVERSATIONS);
      if (path.endsWith("/messages")) return Promise.reject(new Error("gateway timeout"));
      return Promise.resolve([]);
    }) as unknown as typeof api);

    const user = userEvent.setup();
    render(<ChatPage />);
    await user.click(await screen.findByText("Alpha thread"));

    await waitFor(() =>
      expect(screen.getByText(/couldn't load this conversation/i)).toBeInTheDocument()
    );
    expect(screen.queryByText(/no messages yet/i)).not.toBeInTheDocument();
  });
});
