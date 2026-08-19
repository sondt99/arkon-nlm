/**
 * #91 — wiki image `blob:` URLs leaked, and the ones still on screen were revoked too early.
 *
 * The hook resolved `image://<uuid>` refs to authenticated Blobs and handed out object URLs.
 * Two defects, both invisible until a page was left mid-load:
 *
 *   1. `URL.createObjectURL` ran *before* the liveness check, and the request id was never
 *      bumped on unmount — so the post-unmount path took the success branch and minted one
 *      object URL per image that nothing was left alive to revoke. Every full-resolution
 *      Blob stayed pinned in memory for the lifetime of the document.
 *   2. The effect cleanup revoked the previous batch on every `key` change, including the
 *      URLs the rendered markdown was still pointing at, so navigating between two pages
 *      turned the visible images into broken-image icons until the new batch arrived.
 *
 * These tests count `createObjectURL` against `revokeObjectURL` — the only observable that
 * distinguishes "cleaned up" from "leaked" — and pin the ordering that keeps live URLs live.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { useImageResolver } from "./use-image-resolver";
import { api, fetchAuthedBlob } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  api: vi.fn(),
  fetchAuthedBlob: vi.fn(),
}));

const mockApi = vi.mocked(api);
const mockBlob = vi.mocked(fetchAuthedBlob);

let created: string[] = [];
let revoked: string[] = [];
let seq = 0;

/** Resolves only when `release()` is called, so a request can be parked mid-flight. */
function deferred<T>() {
  let release!: (value: T) => void;
  let fail!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    release = res;
    fail = rej;
  });
  return { promise, release, fail };
}

beforeEach(() => {
  created = [];
  revoked = [];
  seq = 0;
  vi.clearAllMocks();
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:mock/${++seq}`;
    created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url: string) => {
    revoked.push(url);
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

/** Every object URL that was minted and never handed back. */
const leaked = () => created.filter((url) => !revoked.includes(url));

function resolveResponse(ids: string[]) {
  return {
    resolved: Object.fromEntries(ids.map((id) => [id, `/api/wiki/images/${id}/raw`])),
    denied: [] as string[],
  };
}

describe("blob URL lifecycle (#91)", () => {
  it("revokes every URL it minted when the page unmounts after the images arrive", async () => {
    mockApi.mockResolvedValue(resolveResponse(["a", "b"]));
    mockBlob.mockResolvedValue(new Blob(["x"]));

    const { result, unmount } = renderHook(() => useImageResolver(["a", "b"]));
    await waitFor(() => expect(Object.keys(result.current.resolved)).toHaveLength(2));
    expect(created).toHaveLength(2);

    unmount();
    expect(leaked()).toEqual([]);
  });

  it("leaves nothing behind when the page unmounts while the images are still downloading", async () => {
    // The exact window the old code got wrong: the resolve call has returned, so the success
    // branch is committed to running, but the Blob fetches have not finished yet.
    const gate = deferred<Blob>();
    mockApi.mockResolvedValue(resolveResponse(["a", "b", "c"]));
    mockBlob.mockReturnValue(gate.promise);

    const { unmount } = renderHook(() => useImageResolver(["a", "b", "c"]));
    await waitFor(() => expect(mockBlob).toHaveBeenCalledTimes(3));

    unmount();
    await act(async () => {
      gate.release(new Blob(["x"]));
      await Promise.resolve();
    });

    // Either no URL was minted, or every minted URL was revoked. Both are clean; a URL
    // created after unmount and left alive is not.
    expect(leaked()).toEqual([]);
  });

  it("keeps the URLs the caller is still rendering alive until the replacements land", async () => {
    mockApi.mockResolvedValue(resolveResponse(["a"]));
    mockBlob.mockResolvedValue(new Blob(["x"]));

    const { result, rerender } = renderHook(({ ids }) => useImageResolver(ids), {
      initialProps: { ids: ["a"] },
    });
    await waitFor(() => expect(result.current.resolved.a).toBeTruthy());
    const firstUrl = result.current.resolved.a;

    // Park the second page's resolve so the "loading the next set" state is observable.
    const gate = deferred<{ resolved: Record<string, string>; denied: string[] }>();
    mockApi.mockReturnValue(gate.promise);
    rerender({ ids: ["b"] });

    await waitFor(() => expect(result.current.loading).toBe(true));
    expect(result.current.resolved.a).toBe(firstUrl);
    expect(revoked).not.toContain(firstUrl);

    await act(async () => {
      gate.release(resolveResponse(["b"]));
      await Promise.resolve();
    });
    await waitFor(() => expect(result.current.resolved.b).toBeTruthy());

    // Superseded only once the replacement was in hand.
    expect(revoked).toContain(firstUrl);
  });
});

describe("derived loading and failure state (#91)", () => {
  it("reports loading for a non-empty id set until that exact set has resolved", async () => {
    mockApi.mockResolvedValue(resolveResponse(["a"]));
    mockBlob.mockResolvedValue(new Blob(["x"]));

    const { result } = renderHook(() => useImageResolver(["a"]));
    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
  });

  it("is never loading when there are no images to resolve", () => {
    const { result } = renderHook(() => useImageResolver([]));
    expect(result.current.loading).toBe(false);
    expect(mockApi).not.toHaveBeenCalled();
  });

  it("clears loading and reports the failure when the resolve call rejects", async () => {
    // `loading` used to be stored and cleared by hand, and this is the path that forgot to:
    // a rejected resolve left every image spinning on "Đang tải hình ảnh…" forever.
    mockApi.mockRejectedValue(new Error("network down"));

    const { result } = renderHook(() => useImageResolver(["a"]));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.failed).toBe(true);
    expect(result.current.resolved).toEqual({});
  });

  it("does not report a stale failure once a different id set is requested", async () => {
    mockApi.mockRejectedValue(new Error("network down"));
    const { result, rerender } = renderHook(({ ids }) => useImageResolver(ids), {
      initialProps: { ids: ["a"] },
    });
    await waitFor(() => expect(result.current.failed).toBe(true));

    mockApi.mockResolvedValue(resolveResponse(["b"]));
    mockBlob.mockResolvedValue(new Blob(["x"]));
    rerender({ ids: ["b"] });
    expect(result.current.failed).toBe(false);
    await waitFor(() => expect(result.current.resolved.b).toBeTruthy());
  });
});
