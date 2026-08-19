import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const SECURITY_HEADERS: Record<string, string> = {
  "X-Frame-Options": "SAMEORIGIN",
  "X-Content-Type-Options": "nosniff",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
};

// Renamed from `middleware` to `proxy` for Next.js 16: the `middleware` file convention is
// deprecated and `next build` warns on every build. Same signature, same matcher — only the
// filename and the exported symbol change, per
// node_modules/next/dist/docs/01-app/03-api-reference/03-file-conventions/proxy.md
//
// Note these four headers are ALSO set by nginx (`nginx/nginx.conf`, with `always`), which is
// the sole ingress. Keeping both is deliberate defence-in-depth: if the frontend container is
// ever reached directly — a port-forward, a different ingress, local `next start` — nginx is
// not in the path and these are the only copies.
export function proxy(_request: NextRequest) {
  const response = NextResponse.next();
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) {
    response.headers.set(key, value);
  }
  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
