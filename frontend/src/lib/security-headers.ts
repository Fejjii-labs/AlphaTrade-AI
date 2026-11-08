/**
 * Security header definitions shared by next.config.ts and unit tests (AT-017).
 *
 * The CSP allows the configured API origin for fetch/XHR, plus exactly its
 * WebSocket counterpart (https -> wss, http -> ws, same host and port) for the
 * voice socket, and keeps inline
 * styles/scripts permitted because Next.js App Router emits inline runtime
 * scripts and Tailwind emits inline style attributes. Dev mode additionally
 * needs eval + websockets for HMR.
 *
 * Production narrows the inline allowance to <script> elements: React and Next
 * never emit inline event-handler attributes, so `script-src-attr 'none'`
 * blocks injected `onerror=`/`onclick=` handlers in CSP3 browsers (older ones
 * fall back to script-src). Nonce-based script-src needs per-request nonces in
 * middleware and dynamic rendering of every page; tracked separately (L15).
 */

export interface SecurityHeader {
  key: string;
  value: string;
}

export function resolveApiOrigin(rawUrl: string | undefined): string {
  try {
    return new URL(rawUrl ?? "http://localhost:8000").origin;
  } catch {
    return "http://localhost:8000";
  }
}

/**
 * The WebSocket origin the browser opens for an HTTP(S) API origin: https ->
 * wss, http -> ws, with host and any explicit port unchanged. Chromium does not
 * let an `https://host` connect-src source match `wss://host`, so the voice
 * socket needs this exact origin listed. Null for any other scheme or input.
 */
export function resolveWebSocketOrigin(apiOrigin: string): string | null {
  let url: URL;
  try {
    url = new URL(apiOrigin);
  } catch {
    return null;
  }
  if (url.protocol === "https:") url.protocol = "wss:";
  else if (url.protocol === "http:") url.protocol = "ws:";
  else return null;
  return `${url.protocol}//${url.host}`;
}

export function buildContentSecurityPolicy(options: {
  apiOrigin: string;
  dev: boolean;
}): string {
  const { apiOrigin, dev } = options;
  const socketOrigin = resolveWebSocketOrigin(apiOrigin);
  const connectSrc = [
    "'self'",
    apiOrigin,
    ...(socketOrigin ? [socketOrigin] : []),
    ...(dev ? ["ws:"] : []),
  ].join(" ");
  const scriptSrc = dev ? "'self' 'unsafe-inline' 'unsafe-eval'" : "'self' 'unsafe-inline'";
  return [
    "default-src 'self'",
    `connect-src ${connectSrc}`,
    `script-src ${scriptSrc}`,
    ...(dev ? [] : ["script-src-attr 'none'"]),
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "base-uri 'self'",
    "form-action 'self'",
  ].join("; ");
}

export function buildSecurityHeaders(options: {
  apiOrigin: string;
  dev: boolean;
}): SecurityHeader[] {
  const headers: SecurityHeader[] = [
    { key: "Content-Security-Policy", value: buildContentSecurityPolicy(options) },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(), microphone=(self), geolocation=()" },
    { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
  ];
  if (!options.dev) {
    headers.push({
      key: "Strict-Transport-Security",
      value: "max-age=63072000; includeSubDomains",
    });
  }
  return headers;
}
