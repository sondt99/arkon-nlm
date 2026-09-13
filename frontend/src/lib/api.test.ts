/**
 * `lib/api.ts` is the single choke point every request in the app passes through, and it
 * was completely uncovered — which is how the 401 blindness in #68 survived. These tests
 * pin the two behaviours that are easy to regress silently: what happens on an expired
 * session, and whether a "cancelled" request is actually cancelled.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  __resetUnauthorizedGuard,
  api,
  clearToken,
  fetchAuthedBlob,
  setToken,
} from "./api";

/** jsdom refuses real navigation, so window.location is replaced wholesale. */
function stubLocation(pathname = "/skills", search = "") {
  const assign = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname, search, assign, href: `http://localhost${pathname}${search}` },
  });
  return assign;
}

/** Factory, not a value: a Response body is single-read, so every call needs a fresh one. */
function jsonResponse(status: number, body: unknown) {
  return () =>
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
}

/** `vi.fn().mockResolvedValue(res)` reuses one Response across calls; this does not. */
function respondWith(factory: () => Response) {
  return vi.fn().mockImplementation(() => Promise.resolve(factory()));
}

beforeEach(() => {
  __resetUnauthorizedGuard();
  localStorage.clear();
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.useRealTimers();
});

// --------------------------------------------------------------------------- //
// Happy path
// --------------------------------------------------------------------------- //

describe("api()", () => {
  it("sends the bearer token when one is stored", async () => {
    setToken("tok-123");
    const fetchMock = respondWith(jsonResponse(200, { ok: true }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api("/api/thing")).resolves.toEqual({ ok: true });

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers.Authorization).toBe("Bearer tok-123");
  });

  it("omits the Authorization header entirely when no token is stored", async () => {
    const fetchMock = respondWith(jsonResponse(200, {}));
    vi.stubGlobal("fetch", fetchMock);

    await api("/api/thing");

    const headers = fetchMock.mock.calls[0][1].headers as Record<string, string>;
    expect(headers).not.toHaveProperty("Authorization");
  });

  it("returns {} for an empty body rather than throwing on JSON.parse", async () => {
    vi.stubGlobal("fetch", respondWith(() => new Response(null, { status: 204 })));
    await expect(api("/api/thing", { method: "DELETE" })).resolves.toEqual({});
  });

  it("surfaces the backend `detail` as the error message", async () => {
    vi.stubGlobal(
      "fetch",
      respondWith(jsonResponse(422, { detail: "slug already exists" }))
    );

    await expect(api("/api/wiki/pages", { method: "POST", body: {} })).rejects.toMatchObject({
      status: 422,
      message: "slug already exists",
    });
  });

  it("falls back to a status message when the error body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      respondWith(() => new Response("<html>502</html>", { status: 502 }))
    );

    await expect(api("/api/thing")).rejects.toMatchObject({
      status: 502,
      message: "API Error 502",
    });
  });
});

// --------------------------------------------------------------------------- //
// #68 — session expiry
// --------------------------------------------------------------------------- //

describe("401 handling (#68)", () => {
  it("clears the token and redirects to /login, preserving where the user was", async () => {
    setToken("expired");
    const assign = stubLocation("/wiki/engineering/runbook", "?tab=history");
    vi.stubGlobal(
      "fetch",
      respondWith(jsonResponse(401, { detail: "Not authenticated" }))
    );

    await expect(api("/api/wiki/pages")).rejects.toBeInstanceOf(ApiError);

    expect(localStorage.getItem("arkon_token")).toBeNull();
    expect(assign).toHaveBeenCalledTimes(1);
    expect(assign.mock.calls[0][0]).toBe(
      "/login?next=" + encodeURIComponent("/wiki/engineering/runbook?tab=history")
    );
  });

  it("redirects ONCE when a page fires several requests that all 401", async () => {
    // The regression this guards: without the de-dupe guard, a dashboard issuing five
    // parallel fetches produced five window.location.assign calls, each cancelling the
    // previous navigation.
    setToken("expired");
    const assign = stubLocation();
    vi.stubGlobal("fetch", respondWith(jsonResponse(401, {})));

    const results = await Promise.allSettled(
      Array.from({ length: 5 }, () => api("/api/thing"))
    );

    expect(results.every((r) => r.status === "rejected")).toBe(true);
    expect(assign).toHaveBeenCalledTimes(1);
  });

  it("does not redirect when the 401 happens on /login itself", async () => {
    // Otherwise a wrong password on the login page redirects the login page to itself,
    // discarding the typed credentials and the error message.
    const assign = stubLocation("/login");
    vi.stubGlobal(
      "fetch",
      respondWith(jsonResponse(401, { detail: "Incorrect email or password" }))
    );

    await expect(
      api("/api/auth/login", { method: "POST", body: { email: "a", password: "b" } })
    ).rejects.toMatchObject({ message: "Incorrect email or password" });

    expect(assign).not.toHaveBeenCalled();
  });

  it("still rejects, so call sites can render their own message", async () => {
    stubLocation();
    vi.stubGlobal("fetch", respondWith(jsonResponse(401, {})));
    await expect(api("/api/thing")).rejects.toBeInstanceOf(ApiError);
  });

  it("leaves a 403 alone — an authorization failure is not an expired session", async () => {
    setToken("valid");
    const assign = stubLocation();
    vi.stubGlobal(
      "fetch",
      respondWith(jsonResponse(403, { detail: "Permission denied" }))
    );

    await expect(api("/api/admin/employees")).rejects.toMatchObject({ status: 403 });

    expect(localStorage.getItem("arkon_token")).toBe("valid");
    expect(assign).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------------------------- //
// #75 / #77 — real cancellation
// --------------------------------------------------------------------------- //

describe("cancellation", () => {
  it("forwards an in-flight caller abort to the signal fetch received", async () => {
    // Asserted while the request is still open, which is the only moment it matters.
    // Aborting after it settles is deliberately a no-op — request() unhooks the listener
    // in its finally block so a page-lifetime controller does not accumulate one listener
    // per request.
    let seen: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            seen = init.signal ?? undefined;
            init.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError"))
            );
          })
      )
    );

    const controller = new AbortController();
    const pending = api("/api/wiki/graph", { signal: controller.signal });
    const settled = pending.catch((e) => e);

    expect(seen).toBeInstanceOf(AbortSignal);
    expect(seen!.aborted).toBe(false);

    controller.abort();
    expect(seen!.aborted).toBe(true);
    await expect(settled).resolves.toMatchObject({ name: "AbortError" });
  });

  it("aborts before dispatch when the signal is already aborted", async () => {
    const fetchMock = vi.fn().mockImplementation((_url, init: RequestInit) => {
      if (init.signal?.aborted) {
        return Promise.reject(new DOMException("Aborted", "AbortError"));
      }
      return Promise.resolve(jsonResponse(200, {}));
    });
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      api("/api/thing", { signal: AbortSignal.abort() })
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  it("reports a caller abort as AbortError, NOT as a timeout", async () => {
    // These two share one controller. Mislabelling a user-initiated cancellation as
    // "Request timed out" makes effect cleanups render a spurious error panel — exactly
    // what the #73/#74 error states would then display on a normal navigation.
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new DOMException("Aborted", "AbortError"))
    );

    const err: unknown = await api("/api/thing", {
      signal: new AbortController().signal,
    }).catch((e) => e);

    expect(err).toBeInstanceOf(DOMException);
    expect(err).toMatchObject({ name: "AbortError" });
    expect(err).not.toBeInstanceOf(ApiError);
  });

  it("reports a genuine timeout as ApiError(0, 'Request timed out')", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            init.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError"))
            );
          })
      )
    );

    const pending = api("/api/slow", { timeoutMs: 1_000 });
    const assertion = expect(pending).rejects.toMatchObject({
      status: 0,
      message: "Request timed out",
    });
    await vi.advanceTimersByTimeAsync(1_100);
    await assertion;
  });

  it("does not leak an abort listener onto a long-lived caller signal", async () => {
    // A single page-lifetime controller shared by a polling loop would otherwise
    // accumulate one listener per request.
    const controller = new AbortController();
    const add = vi.spyOn(controller.signal, "addEventListener");
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    vi.stubGlobal("fetch", respondWith(jsonResponse(200, {})));

    await api("/api/thing", { signal: controller.signal });
    await api("/api/thing", { signal: controller.signal });

    expect(add).toHaveBeenCalledTimes(2);
    expect(remove).toHaveBeenCalledTimes(2);
  });

  it("fetchAuthedBlob forwards an in-flight caller abort too", async () => {
    let seen: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(
        (_url, init: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            seen = init.signal ?? undefined;
            init.signal?.addEventListener("abort", () =>
              reject(new DOMException("Aborted", "AbortError"))
            );
          })
      )
    );

    const controller = new AbortController();
    const settled = fetchAuthedBlob(
      "/api/artifacts/1/download",
      5_000,
      controller.signal
    ).catch((e) => e);

    expect(seen!.aborted).toBe(false);
    controller.abort();
    expect(seen!.aborted).toBe(true);
    await expect(settled).resolves.toMatchObject({ name: "AbortError" });
  });

  it("fetchAuthedBlob returns the blob on success", async () => {
    vi.stubGlobal(
      "fetch",
      respondWith(() => new Response("pdf-bytes", { status: 200 }))
    );
    const blob = await fetchAuthedBlob("/api/artifacts/1/download");
    await expect(blob.text()).resolves.toBe("pdf-bytes");
  });
});

describe("token helpers", () => {
  it("round-trips and clears", () => {
    expect(localStorage.getItem("arkon_token")).toBeNull();
    setToken("abc");
    expect(localStorage.getItem("arkon_token")).toBe("abc");
    clearToken();
    expect(localStorage.getItem("arkon_token")).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// The de-dupe guard must not survive a page the redirect never leaves
// --------------------------------------------------------------------------- //

describe("onUnauthorized de-dupe guard", () => {
  it("does not latch on /login, where no navigation clears it", async () => {
    // A 401 on the login route is an ordinary failed sign-in, not an expired session.
    // The guard used to be set before the redirect check, and only a page load clears it
    // — so on /login it latched with nothing to unlatch it.
    const assign = stubLocation("/login");
    setToken("tok-123");
    global.fetch = respondWith(jsonResponse(401, { detail: "Bad credentials" }));

    await expect(api("/api/auth/login", { method: "POST" })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(assign).not.toHaveBeenCalled();

    // Now the user signs in successfully — a client-side transition, so module state
    // survives — and their session later expires on a real page.
    const assignAfter = stubLocation("/wiki");
    setToken("tok-456");
    global.fetch = respondWith(jsonResponse(401, { detail: "Token expired" }));

    await expect(api("/api/wiki/pages")).rejects.toBeInstanceOf(ApiError);

    expect(assignAfter).toHaveBeenCalledTimes(1);
    expect(assignAfter.mock.calls[0][0]).toContain("/login?next=");
    expect(localStorage.getItem("arkon_token")).toBeNull();
  });

  it("still collapses parallel 401s into a single redirect", async () => {
    // The reason the guard exists: several in-flight requests each get a 401, and without
    // it each one starts its own navigation, cancelling the others mid-flight.
    const assign = stubLocation("/wiki");
    setToken("tok-123");
    global.fetch = respondWith(jsonResponse(401, { detail: "Token expired" }));

    await Promise.allSettled([
      api("/api/wiki/pages"),
      api("/api/wiki/stats"),
      api("/api/wiki/tree"),
    ]);

    expect(assign).toHaveBeenCalledTimes(1);
  });

  it("preserves where the user was so login can send them back", async () => {
    const assign = stubLocation("/wiki/graph", "?focus=abc");
    setToken("tok-123");
    global.fetch = respondWith(jsonResponse(401, { detail: "Token expired" }));

    await expect(api("/api/wiki/graph")).rejects.toBeInstanceOf(ApiError);

    expect(assign).toHaveBeenCalledWith(
      `/login?next=${encodeURIComponent("/wiki/graph?focus=abc")}`,
    );
  });
});
