import { readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it } from "vitest";

import {
  ALL_DESTINATIONS,
  SECONDARY_NAV,
} from "@/components/layout/navigation-config";
import { SETTINGS_SECTIONS } from "@/lib/navigation/settings-sections";
import nextConfig from "../../../next.config";
import {
  findRegisteredRoute,
  resolveRoute,
  ROUTE_REDIRECTS,
  ROUTE_REGISTRY,
  routesOfKind,
} from "./route-registry";

const APP_DIR = path.resolve(__dirname, "../../app");

/** `page.tsx` files as route paths: route groups dropped, `[id]` kept. */
function pageRoutes(dir = APP_DIR, prefix: string[] = []): string[] {
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    if (entry.isFile() && entry.name === "page.tsx") found.push(`/${prefix.join("/")}`);
    if (!entry.isDirectory()) continue;
    const segment = /^\(.+\)$/.test(entry.name) ? [] : [entry.name];
    found.push(...pageRoutes(path.join(dir, entry.name), [...prefix, ...segment]));
  }
  return found;
}

describe("route registry", () => {
  it("lists every page.tsx exactly once, and nothing else", () => {
    const pages = pageRoutes().sort();
    const registered = ROUTE_REGISTRY.map((route) => route.path).sort();
    expect(registered).toEqual(pages);
    expect(new Set(registered).size).toBe(registered.length);
  });

  it("keeps the five pages, four Settings tabs, Knowledge and two deep links", () => {
    expect(routesOfKind("primary").map((route) => route.path).sort()).toEqual(
      ["/", "/agent", "/autonomous", "/journal", "/settings", "/strategies"],
    );
    expect(routesOfKind("section").map((route) => route.path).sort()).toEqual(
      ["/knowledge", "/settings/alerts", "/settings/exchange", "/settings/watcher"],
    );
    expect(routesOfKind("public").map((route) => route.path)).toEqual([
      "/login",
      "/register",
      "/forgot-password",
      "/reset-password",
      "/verify-email",
    ]);
    for (const deepLink of ["/strategies/setups/abc", "/execution/manual-demo/abc"]) {
      expect(findRegisteredRoute(deepLink)?.kind, deepLink).toBe("deep-link");
    }
  });

  it("leaves no page behind a redirect, except query-only ones", () => {
    for (const rule of ROUTE_REDIRECTS) {
      if (rule.has || rule.source.includes(":")) continue;
      expect(findRegisteredRoute(rule.source), rule.source).toBeNull();
    }
  });
});

describe("redirects", () => {
  it.each(ROUTE_REDIRECTS.map((rule) => [rule.source, rule]))(
    "%s lands on a real page",
    (_source, rule) => {
      const query = (rule.has ?? []).map(({ key, value }) => `${key}=${value ?? "1"}`).join("&");
      const sample =
        rule.source.replace(":id", "sample-id").replace(":path*", "sample/child") + (query ? `?${query}` : "");
      const landing = resolveRoute(sample);
      expect(landing, `${rule.source} → ${rule.destination}`).not.toBeNull();
    },
  );

  it.each(ROUTE_REDIRECTS.map((rule) => [rule.source, rule]))("%s is served by next.config", async (_source, rule) => {
    const rules = await nextConfig.redirects!();
    expect(rules).toContainEqual(
      expect.objectContaining({ source: rule.source, destination: rule.destination, permanent: false }),
    );
  });
});
describe("navigation links resolve to real pages (no 404)", () => {
  const links = [...new Set([
    ...ALL_DESTINATIONS.map((item) => item.href),
    ...SECONDARY_NAV.flatMap((group) => group.items.map((item) => item.href)),
    ...SETTINGS_SECTIONS.map((section) => section.href),
  ])];

  it.each(links.map((href) => [href]))("%s", (href) => {
    expect(resolveRoute(href), href).not.toBeNull();
  });
});

describe("removed routes land on their nearest kept page", () => {
  it.each([
    ["/risk", "/autonomous"],
    ["/risk/autonomous", "/autonomous"],
    ["/dashboard", "/"],
    ["/coaching", "/agent"],
    ["/settings/team", "/settings"],
    ["/settings/billing", "/settings"],
    ["/settings/audit", "/settings"],
    ["/settings/diagnostics", "/settings"],
    ["/settings/help", "/settings"],
    ["/settings/notifications", "/settings/alerts"],
    ["/watcher", "/settings/watcher"],
    ["/market-watcher", "/settings/watcher"],
    ["/alerts", "/settings/alerts"],
    ["/alerts/review", "/settings/alerts"],
    ["/decision/candidates/cand-1", "/autonomous"],
    ["/approvals", "/autonomous"],
    ["/paper-validation/run-sessions/s-1", "/strategies"],
    ["/strategy-lab", "/strategies"],
    ["/strategy-lab/strategy-7", "/agent"],
    ["/backtests/bt-1", "/strategies"],
    ["/journal/import", "/journal"],
    ["/analytics", "/journal"],
    ["/lessons/lesson-1", "/journal"],
    ["/portfolio", "/"],
    ["/positions", "/"],
    ["/workspace", "/agent"],
  ])("%s → %s", (source, page) => {
    expect(resolveRoute(source)?.path).toBe(page);
  });
});

describe("query-matched redirects", () => {
  it("only redirects /journal when it carries tab=knowledge", () => {
    expect(resolveRoute("/journal")?.path).toBe("/journal");
    expect(resolveRoute("/journal?tab=stats")?.path).toBe("/journal");
    expect(resolveRoute("/journal?tab=knowledge&category=rules")?.path).toBe("/knowledge");
  });

  it("sends the old statistics page to the Stats tab", () => {
    expect(resolveRoute("/journal/statistics")?.path).toBe("/journal");
  });
});
