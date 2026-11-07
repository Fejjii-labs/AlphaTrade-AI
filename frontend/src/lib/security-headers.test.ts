import { describe, expect, it } from "vitest";

import {
  buildContentSecurityPolicy,
  buildSecurityHeaders,
  resolveApiOrigin,
} from "./security-headers";

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

describe("buildContentSecurityPolicy", () => {
  it("allows only self and the API origin for connections in production", () => {
    const csp = buildContentSecurityPolicy({
      apiOrigin: "https://api.example.com",
      dev: false,
    });
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("connect-src 'self' https://api.example.com");
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
