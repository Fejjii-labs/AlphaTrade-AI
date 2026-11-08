import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  buildSecurityHeaders,
  resolveApiOrigin,
  resolveWebSocketOrigin,
} from "./security-headers";

/** Source list of one CSP directive, split into tokens. */
function directive(csp: string, name: string): string[] {
  const part = csp.split(";").map((item) => item.trim()).find((item) => item.startsWith(`${name} `));
  return part ? part.split(/\s+/).slice(1) : [];
}

function productionCsp(apiOrigin: string): string {
  return buildSecurityHeaders({ apiOrigin, dev: false }).find(
    (header) => header.key === "Content-Security-Policy",
  )!.value;
}

describe("resolveApiOrigin", () => {
  it("extracts the origin from the configured API URL", () => {
    expect(resolveApiOrigin("https://alphatrade-api-staging.onrender.com")).toBe(
      "https://alphatrade-api-staging.onrender.com",
    );
    expect(resolveApiOrigin("http://localhost:8000/base")).toBe("http://localhost:8000");
  });

  it("falls back to the local API origin on missing or invalid input", () => {
    expect(resolveApiOrigin(undefined)).toBe("http://localhost:8000");
    expect(resolveApiOrigin("not-a-url")).toBe("http://localhost:8000");
  });
});

describe("resolveWebSocketOrigin", () => {
  it("maps https to wss and http to ws on the same host", () => {
    expect(resolveWebSocketOrigin("https://alphatrade-api-staging.onrender.com")).toBe(
      "wss://alphatrade-api-staging.onrender.com",
    );
    expect(resolveWebSocketOrigin("http://api.example.com")).toBe("ws://api.example.com");
  });

  it("preserves explicit non-default ports and drops default ones", () => {
    expect(resolveWebSocketOrigin("http://localhost:8000")).toBe("ws://localhost:8000");
    expect(resolveWebSocketOrigin("https://api.example.com:8443")).toBe("wss://api.example.com:8443");
    expect(resolveWebSocketOrigin("http://127.0.0.1:3001")).toBe("ws://127.0.0.1:3001");
    expect(resolveWebSocketOrigin("https://api.example.com:443")).toBe("wss://api.example.com");
    expect(resolveWebSocketOrigin("http://api.example.com:80")).toBe("ws://api.example.com");
  });

  it("normalizes to an origin: no path, query, credentials or trailing slash", () => {
    expect(resolveWebSocketOrigin("https://API.Example.com/base/?x=1#y")).toBe("wss://api.example.com");
    expect(resolveWebSocketOrigin("https://user:pass@api.example.com")).toBe("wss://api.example.com");
  });

  it("returns null for non-HTTP schemes and invalid input", () => {
    expect(resolveWebSocketOrigin("ftp://api.example.com")).toBeNull();
    expect(resolveWebSocketOrigin("wss://api.example.com")).toBeNull();
    expect(resolveWebSocketOrigin("null")).toBeNull();
    expect(resolveWebSocketOrigin("not-a-url")).toBeNull();
    expect(resolveWebSocketOrigin("")).toBeNull();
  });
});

describe("buildContentSecurityPolicy", () => {
  it("allows exactly the API origin and its WebSocket counterpart in production", () => {
    const csp = productionCsp(resolveApiOrigin("https://alphatrade-api-staging.onrender.com"));
    expect(directive(csp, "connect-src")).toEqual([
      "'self'",
      "https://alphatrade-api-staging.onrender.com",
      "wss://alphatrade-api-staging.onrender.com",
    ]);
  });

  it("keeps the API port on both the HTTP and WebSocket sources", () => {
    expect(directive(productionCsp("https://api.example.com:8443"), "connect-src")).toEqual([
      "'self'",
      "https://api.example.com:8443",
      "wss://api.example.com:8443",
    ]);
    expect(directive(productionCsp("http://127.0.0.1:8000"), "connect-src")).toEqual([
      "'self'",
      "http://127.0.0.1:8000",
      "ws://127.0.0.1:8000",
    ]);
  });

  it("never adds wildcard, scheme-only or unrelated socket sources in production", () => {
    const sources = directive(productionCsp("https://api.example.com"), "connect-src");
    for (const forbidden of ["ws:", "wss:", "*", "https:", "http:", "ws://api.example.com"]) {
      expect(sources).not.toContain(forbidden);
    }
    expect(sources.every((source) => !source.includes("*"))).toBe(true);
    expect(sources.filter((source) => /^wss?:/.test(source))).toEqual(["wss://api.example.com"]);
  });

  it("leaves every other production directive unchanged by the socket source", () => {
    const csp = productionCsp("https://api.example.com");
    expect(csp.split("; ").filter((part) => !part.startsWith("connect-src "))).toEqual([
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline'",
      "script-src-attr 'none'",
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      "object-src 'none'",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ]);
  });

  it("allows only self and the API origin for connections in production", () => {
    const csp = buildContentSecurityPolicy({
      apiOrigin: "https://api.example.com",
      dev: false,
    });
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("connect-src 'self' https://api.example.com wss://api.example.com");
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("form-action 'self'");
    // Inline event-handler attributes are never needed by React/Next.
    expect(csp).toContain("script-src-attr 'none'");
  });

  it("permits eval and websockets only in dev (HMR)", () => {
    const csp = buildContentSecurityPolicy({ apiOrigin: "http://localhost:8000", dev: true });
    expect(csp).toContain("'unsafe-eval'");
    expect(csp).toContain("ws:");
    expect(csp).not.toContain("script-src-attr");
  });
});

describe("buildSecurityHeaders", () => {
  it("includes the full hardening set in production", () => {
    const headers = buildSecurityHeaders({ apiOrigin: "https://api.example.com", dev: false });
    const keys = headers.map((h) => h.key);
    expect(keys).toContain("Content-Security-Policy");
    expect(keys).toContain("X-Content-Type-Options");
    expect(keys).toContain("X-Frame-Options");
    expect(keys).toContain("Referrer-Policy");
    expect(keys).toContain("Permissions-Policy");
    expect(keys).toContain("Cross-Origin-Opener-Policy");
    expect(keys).toContain("Strict-Transport-Security");
    expect(headers.find((h) => h.key === "X-Frame-Options")?.value).toBe("DENY");
    expect(headers.find((h) => h.key === "X-Content-Type-Options")?.value).toBe("nosniff");
    expect(headers.find((h) => h.key === "Permissions-Policy")?.value).toBe(
      "camera=(), microphone=(self), geolocation=()",
    );
  });

  it("omits HSTS in dev", () => {
    const headers = buildSecurityHeaders({ apiOrigin: "http://localhost:8000", dev: true });
    expect(headers.map((h) => h.key)).not.toContain("Strict-Transport-Security");
  });
});
