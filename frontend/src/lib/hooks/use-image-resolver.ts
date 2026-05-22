"use client";

import { useEffect, useRef, useState } from "react";

import { api, getToken } from "@/lib/api";

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
 * Resolve `image://<uuid>` references inside wiki content_md to short-lived
 * presigned MinIO URLs the browser can fetch. Re-runs whenever the set of ids
 * changes.
 *
 * Returns:
 *   - `resolved[uuid]` = signed URL (renderable)
 *   - `denied` contains uuids the user is not authorised to view
 *   - uuids absent from both = unknown / missing
 */
export function useImageResolver(ids: string[]): ImageResolverState {
  const [state, setState] = useState<ImageResolverState>(EMPTY);
  const reqId = useRef(0);

  // Stable key for dependency comparison (sorted, deduped).
  const key = Array.from(new Set(ids)).sort().join(",");

  useEffect(() => {
    if (!key) {
      setState(EMPTY);
      return;
    }
    const myReq = ++reqId.current;
    setState((s) => ({ ...s, loading: true }));

    api<ResolveResponse>("/api/wiki/images/resolve", {
      method: "POST",
      body: { ids: key.split(",") },
    })
      .then((res) => {
        if (myReq !== reqId.current) return;
        const token = getToken();
        const withToken: Record<string, string> = {};
        for (const [id, url] of Object.entries(res.resolved || {})) {
          withToken[id] = token ? `${url}?token=${encodeURIComponent(token)}` : url;
        }
        setState({
          resolved: withToken,
          denied: new Set(res.denied || []),
          loading: false,
        });
      })
      .catch(() => {
        if (myReq !== reqId.current) return;
        setState({ resolved: {}, denied: new Set(), loading: false });
      });
  }, [key]);

  return state;
}
