/**
 * The pager moved `idx` and reset nothing else.
 *
 * Every handler reads `drafts[idx]` at call time, so arming Reject on draft A, typing the
 * reason, paging to B, and confirming sent the rejection to **B** carrying A's note. That
 * destroys another contributor's submission under a note written about someone else's
 * work, and records it as a legitimate review.
 *
 * These pin the reset, and the two properties the reset must not break.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

import { WikiDraftBanner } from "./wiki-draft-banner";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api", () => ({ api: apiMock }));

// WikiContent renders markdown through the full pipeline; irrelevant here and slow.
vi.mock("./wiki-content", () => ({
  WikiContent: ({ markdown }: { markdown?: string }) => <div>{markdown}</div>,
}));

function draft(id: string, author: string) {
  return {
    id,
    page_slug: "concept/leave-policy",
    content_md: `body of ${id}`,
    author_name: author,
    created_at: "2026-09-01T00:00:00Z",
  } as never;
}

const DRAFTS = [draft("draft-a", "Ana"), draft("draft-b", "Bo")];

function renderBanner() {
  const onApproved = vi.fn();
  const onRejected = vi.fn();
  render(
    <WikiDraftBanner
      drafts={DRAFTS}
      onApproved={onApproved}
      onRejected={onRejected}
    />,
  );
  return { onApproved, onRejected };
}

// Buttons carry a material-symbols glyph whose text joins the accessible name
// ("cancel Reject", "chevron_right"), so match on substrings rather than exact labels.
const noteBox = () => screen.queryByPlaceholderText(/Tell the contributor why/i);
const rejectButton = () =>
  screen.getByRole("button", {
    name: (n) => /reject/i.test(n) && !/confirm/i.test(n),
  });
const confirmReject = () =>
  screen.getByRole("button", { name: /confirm reject/i });
const nextPage = () => screen.getByRole("button", { name: /chevron_right/i });

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({});
});

describe("WikiDraftBanner paging", () => {
  it("discards an armed rejection when the reviewer pages to another draft", async () => {
    const { onRejected } = renderBanner();

    fireEvent.click(rejectButton());
    fireEvent.change(noteBox()!, { target: { value: "Wrong figures in the table" } });
    expect(noteBox()).toHaveValue("Wrong figures in the table");

    fireEvent.click(nextPage());

    // The armed state belonged to draft-a and must not follow the reviewer to draft-b.
    expect(noteBox()).toBeNull();
    expect(screen.getByText("body of draft-b")).toBeInTheDocument();

    // Nothing was submitted on the way.
    expect(apiMock).not.toHaveBeenCalled();
    expect(onRejected).not.toHaveBeenCalled();
  });

  it("rejects the draft actually on screen, with its own note", async () => {
    const { onRejected } = renderBanner();

    fireEvent.click(rejectButton());
    fireEvent.change(noteBox()!, { target: { value: "note for A" } });
    fireEvent.click(nextPage());

    fireEvent.click(rejectButton());
    fireEvent.change(noteBox()!, { target: { value: "note for B" } });
    fireEvent.click(confirmReject());

    await waitFor(() => expect(apiMock).toHaveBeenCalled());
    const [url, init] = apiMock.mock.calls[0];
    expect(url).toBe("/api/wiki/drafts/draft-b/reject");
    expect(init.body.reviewer_note).toBe("note for B");
    await waitFor(() => expect(onRejected).toHaveBeenCalledWith("draft-b"));
  });

  it("keeps the reject flow usable on a single draft", async () => {
    // The reset must not make arming impossible when there is no pager at all.
    render(
      <WikiDraftBanner
        drafts={[draft("solo", "Cy")]}
        onApproved={vi.fn()}
        onRejected={vi.fn()}
      />,
    );

    fireEvent.click(rejectButton());
    fireEvent.change(noteBox()!, { target: { value: "not ready" } });
    expect(noteBox()).toHaveValue("not ready");
  });
});
