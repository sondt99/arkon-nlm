"use client";

import { useEffect, useRef, useState } from "react";

import { api, fetchAuthedBlob } from "@/lib/api";

export type ImageResolverState = {
  resolved: Record<string, string>;
  denied: Set<string>;
  loading: boolean;
};

type ResolveResponse = {
  resolved: Record<string, string>;
  denied: string[];
};

const EMPTY: ImageResolverState = {
  resolved: {},
  denied: new Set(),
  loading: false,
};

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
  const [state, setState] = useState<ImageResolverState>(EMPTY);
  const reqId = useRef(0);
  const blobUrlsRef = useRef<string[]>([]);

  // Stable key for dependency comparison (sorted, deduped).
  const key = Array.from(new Set(ids)).sort().join(",");

  useEffect(() => {
    const revokePrevious = () => {
      for (const url of blobUrlsRef.current) URL.revokeObjectURL(url);
      blobUrlsRef.current = [];
    };

    if (!key) {
      revokePrevious();
      setState(EMPTY);
      return;
    }
    const myReq = ++reqId.current;
    setState((s) => ({ ...s, loading: true }));

    api<ResolveResponse>("/api/wiki/images/resolve", {
      method: "POST",
      body: { ids: key.split(",") },
    })
      .then(async (res) => {
        const entries = Object.entries(res.resolved || {});
        const blobs = await Promise.all(
          entries.map(async ([id, url]) => {
            try {
              const blob = await fetchAuthedBlob(url);
              return [id, URL.createObjectURL(blob)] as const;
            } catch {
              return [id, null] as const;
            }
          })
        );
        if (myReq !== reqId.current) {
          // A newer request superseded this one; discard these blob URLs.
          for (const [, url] of blobs) if (url) URL.revokeObjectURL(url);
          return;
        }

        revokePrevious();
        const resolved: Record<string, string> = {};
        for (const [id, url] of blobs) {
          if (url) {
            resolved[id] = url;
            blobUrlsRef.current.push(url);
          }
        }
        setState({
          resolved,
          denied: new Set(res.denied || []),
          loading: false,
        });
      })
      .catch(() => {
        if (myReq !== reqId.current) return;
        revokePrevious();
        setState({ resolved: {}, denied: new Set(), loading: false });
      });

    return () => {
      revokePrevious();
    };
  }, [key]);

  return state;
}
