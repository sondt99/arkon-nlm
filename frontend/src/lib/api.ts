/**
 * API Client — Fetch wrapper with JWT auth.
 * All API calls go through this module.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "";
const REQUEST_TIMEOUT_MS = 30_000;

type RequestOptions = {
  method?: string;
  body?: unknown;
  headers?: Record<string, string>;
  /** Caller-owned cancellation. Without this, an effect that "aborted" could only discard
   *  a response it had already paid for — the socket stayed open and the server kept
   *  working. Progressive loaders and preview panes rely on it to actually stop. */
  signal?: AbortSignal;
  timeoutMs?: number;
};

class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, message: string, data?: unknown) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem("arkon_token");
}

export function setToken(token: string) {
  localStorage.setItem("arkon_token", token);
}

export function clearToken() {
  localStorage.removeItem("arkon_token");
}

/**
 * Session-expiry handling.
 *
 * De-duplicated on purpose: a page that fires several requests in parallel gets several
 * 401s, and without the guard each one would trigger its own redirect — cancelling the
 * others mid-navigation and, in the worst case, looping.
 */
let unauthorizedHandled = false;

export function onUnauthorized() {
  if (unauthorizedHandled) return;
  if (typeof window === "undefined") return;

  unauthorizedHandled = true;
  clearToken();

  // Let anything interested (AuthProvider, banners) react before we navigate.
  window.dispatchEvent(new CustomEvent("arkon:unauthorized"));

  // Preserve where the user was so login can send them back.
  const next = encodeURIComponent(
    window.location.pathname + window.location.search
  );
  const target = `/login?next=${next}`;
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign(target);
  }
}

/** Test hook — lets a suite assert the de-dupe without reloading the page. */
export function __resetUnauthorizedGuard() {
  unauthorizedHandled = false;
}

async function request(path: string, options: RequestOptions = {}): Promise<Response> {
  const {
    method = "GET",
    body,
    headers = {},
    timeoutMs = REQUEST_TIMEOUT_MS,
    signal,
  } = options;
  const token = getToken();

  const controller = new AbortController();
  let timedOut = false;
  const timerId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  // A caller's signal and the timeout share one controller, because fetch takes only one.
  // `timedOut` keeps them distinguishable: a timeout must keep its ApiError(0, "Request
  // timed out") contract, while a caller-initiated abort has to surface a real AbortError
  // so `if (err.name === "AbortError") return` guards in effect cleanups still work —
  // reporting a user-cancelled request as a timeout would render a spurious error panel.
  const forwardAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", forwardAbort, { once: true });
  }

  const config: RequestInit = {
    method,
    signal: controller.signal,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
  };

  if (body && method !== "GET") {
    config.body = JSON.stringify(body);
  }

  let res: Response;
  const fullUrl = `${API_BASE}${path}`;
  try {
    res = await fetch(fullUrl, config);
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError" && timedOut) {
      throw new ApiError(0, "Request timed out");
    }
    throw err;
  } finally {
    clearTimeout(timerId);
    signal?.removeEventListener("abort", forwardAbort);
  }

  if (!res.ok) {
    let data;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    const message =
      (data as { detail?: string })?.detail || `API Error ${res.status}`;

    if (res.status === 401) {
      // Every non-ok response used to become a generic ApiError, and the only 401 reaction
      // in the app ran once at mount. So when a 24-hour JWT expired overnight the app kept
      // rendering as logged in: lists showed empty, several call sites swallowed the error
      // entirely, and the skill editor's autosave failed on every keystroke with only a
      // 1.5px dot turning red — the user kept typing and lost the work.
      onUnauthorized();
    }

    throw new ApiError(res.status, message, data);
  }

  return res;
}

export async function api<T = unknown>(
  path: string,
  options: RequestOptions = {}
): Promise<T> {
  const res = await request(path, options);
  // Handle empty responses (204, etc.)
  const text = await res.text();
  if (!text) return {} as T;
  return JSON.parse(text);
}

/** Like `api()`, but also exposes response headers — for endpoints that carry
 * metadata (e.g. `X-Total-Count`) outside the JSON body to stay backward
 * compatible with other consumers of that body shape. */
export async function apiWithMeta<T = unknown>(
  path: string,
  options: RequestOptions = {}
): Promise<{ data: T; headers: Headers }> {
  const res = await request(path, options);
  const text = await res.text();
  const data = (text ? JSON.parse(text) : {}) as T;
  return { data, headers: res.headers };
}

/** Upload a file via multipart/form-data. Uses the same base URL as `api()`. */
export async function apiUpload<T = unknown>(
  path: string,
  formData: FormData,
  timeoutMs = 120_000
): Promise<T> {
  const uploadBase = typeof window !== "undefined" ? API_BASE : "http://localhost:5055";

  const token = getToken();
  const controller = new AbortController();
  const timerId = setTimeout(() => controller.abort(), timeoutMs);

  let res: Response;
  try {
    res = await fetch(`${uploadBase}${path}`, {
      method: "POST",
      signal: controller.signal,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: formData,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "Upload timed out");
    }
    throw err;
  } finally {
    clearTimeout(timerId);
  }

  if (!res.ok) {
    let data;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    const message =
      (data as { detail?: string })?.detail || `Upload Error ${res.status}`;
    throw new ApiError(res.status, message, data);
  }

  return res.json();
}

/** Fetch a binary resource with the auth header (not the URL) carrying the token. */
export async function fetchAuthedBlob(
  path: string,
  timeoutMs = 30_000,
  signal?: AbortSignal
): Promise<Blob> {
  const token = getToken();
  const controller = new AbortController();
  let timedOut = false;
  const timerId = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);

  // See request(): one controller for both, `timedOut` keeps the two causes apart.
  const forwardAbort = () => controller.abort();
  if (signal) {
    if (signal.aborted) controller.abort();
    else signal.addEventListener("abort", forwardAbort, { once: true });
  }

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      signal: controller.signal,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError" && timedOut) {
      throw new ApiError(0, "Request timed out");
    }
    throw err;
  } finally {
    clearTimeout(timerId);
    signal?.removeEventListener("abort", forwardAbort);
  }

  if (!res.ok) {
    throw new ApiError(res.status, `API Error ${res.status}`);
  }

  return res.blob();
}

export { ApiError };
