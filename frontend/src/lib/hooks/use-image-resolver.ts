"use client";

import { useEffect, useRef, useState } from "react";

import { api, fetchAuthedBlob } from "@/lib/api";

export type ImageResolverState = {
  resolved: Record<string, string>;
  denied: Set<string>;
  loading: boolean;
  /** The resolve call itself failed. Distinct from "this id is not in `resolved`",
   *  which the caller renders as a missing image — a transport failure is not the
   *  same fact as the document referencing an image that does not exist. */
  failed: boolean;
};

type ResolveResponse = {
  resolved: Record<string, string>;
  denied: string[];
};

/**
 * The outcome of one resolve, tagged with the id-set it answers for.
 *
 * `loading` is derived from `key !== resolution.key` rather than stored, so it cannot be
 * left stuck: a stored flag has to be cleared on every exit path, and the failure path
 * and the superseded-request path are exactly the ones that get missed.
 */
type Resolution = {
  key: string;
  resolved: Record<string, string>;
  denied: Set<string>;
  failed: boolean;
};

const EMPTY: Resolution = { key: "", resolved: {}, denied: new Set(), failed: false };

/**
 * Resolve `image://<uuid>` references inside wiki content_md to authenticated
 * object-URLs the browser can render. Re-runs whenever the set of ids changes.
 *
 * Images are fetched with the auth token in the `Authorization` header (not
 * the URL) and converted to `blob:` object URLs, so the bearer token never
 * ends up in browser history, proxy logs, or a Referer header.
 *
 * Returns:
 *   - `resolved[uuid]` = blob object URL (renderable)
 *   - `denied` contains uuids the user is not authorised to view
 *   - uuids absent from both = unknown / missing
 */
export function useImageResolver(ids: string[]): ImageResolverState {
  const [resolution, setResolution] = useState<Resolution>(EMPTY);
  /** The object URLs currently handed out via `resolution`. Only these may be revoked. */
  const liveUrlsRef = useRef<string[]>([]);

  // Stable key for dependency comparison (sorted, deduped).
  const key = Array.from(new Set(ids)).sort().join(",");

  useEffect(() => {
    if (!key) return;

    const controller = new AbortController();

    api<ResolveResponse>("/api/wiki/images/resolve", {
      method: "POST",
      body: { ids: key.split(",") },
      signal: controller.signal,
    })
      .then(async (res) => {
        const entries = Object.entries(res.resolved || {});
        const created = await Promise.all(
          entries.map(async ([id, url]) => {
            try {
              const blob = await fetchAuthedBlob(url, undefined, controller.signal);
              return [id, URL.createObjectURL(blob)] as const;
            } catch {
              return [id, null] as const;
            }
          })
        );

        // `createObjectURL` is what allocates, so the liveness check has to come after it.
        // Checked before, an unmount landing in this window took the success branch and
        // created one blob: URL per image with nothing left alive to revoke it — pinning
        // every full-resolution Blob for the lifetime of the document.
        if (controller.signal.aborted) {
          for (const [, url] of created) if (url) URL.revokeObjectURL(url);
          return;
        }

        const resolved: Record<string, string> = {};
        const nextUrls: string[] = [];
        for (const [id, url] of created) {
          if (url) {
            resolved[id] = url;
            nextUrls.push(url);
          }
        }

        // Revoke the previous batch only once the replacement is in hand. Revoking in the
        // effect cleanup instead killed URLs the rendered output was still pointing at, so
        // every image turned into a broken-image icon for the duration of the next fetch.
        const superseded = liveUrlsRef.current;
        liveUrlsRef.current = nextUrls;
        for (const url of superseded) URL.revokeObjectURL(url);

        setResolution({ key, resolved, denied: new Set(res.denied || []), failed: false });
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        setResolution({ key, resolved: {}, denied: new Set(), failed: true });
      });

    return () => controller.abort();
  }, [key]);

  // Unmount-only. Declared after the resolve effect so its cleanup runs second: the abort
  // above has already fired, so any in-flight run will revoke whatever it allocated itself.
  useEffect(
    () => () => {
      for (const url of liveUrlsRef.current) URL.revokeObjectURL(url);
      liveUrlsRef.current = [];
    },
    []
  );

  return {
    resolved: resolution.resolved,
    denied: resolution.denied,
    loading: key !== "" && resolution.key !== key,
    failed: resolution.key === key && resolution.failed,
  };
}
