import { existsSync, readdirSync } from "node:fs";
import path from "node:path";

import { describe, expect, it, vi } from "vitest";

import { ADVANCED_ROUTES, SETTINGS_ROUTE_PREFIXES } from "@/components/layout/advanced-routes";
import {
  getDestinationId,
  PRIMARY_DESTINATIONS,
  SECONDARY_NAV,
} from "@/components/layout/navigation-config";
import nextConfig from "../../../next.config";
import {
  findRegisteredRoute,
  PAGE_REDIRECTS,
  resolveRoute,
  ROUTE_REDIRECTS,
  ROUTE_REGISTRY,
  routesOfKind,
} from "./route-registry";

const redirectMock = vi.hoisted(() => vi.fn());
vi.mock("next/navigation", () => ({ redirect: redirectMock }));

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

function pageFile(route: string): string {
  for (const group of ["(app)", "(public)"]) {
    const file = path.join(APP_DIR, group, route, "page.tsx");
    if (existsSync(file)) return file;
  }
  throw new Error(`No page.tsx for ${route}`);
}

describe("route registry", () => {
  it("lists every page.tsx exactly once, and nothing else", () => {
    const pages = pageRoutes().sort();
    const registered = ROUTE_REGISTRY.map((route) => route.path).sort();
    expect(registered).toEqual(pages);
    expect(new Set(registered).size).toBe(registered.length);
  });

  it("gives every diagnostics route a group and no other route one", () => {
    for (const route of ROUTE_REGISTRY) {
      if (route.kind === "diagnostics") expect(route.group, route.path).toBeTruthy();
      else expect(route.group, route.path).toBeUndefined();
    }
  });

  it("keeps the public pages and deep links the plan names", () => {
    expect(routesOfKind("public").map((route) => route.path)).toEqual([
      "/login",
      "/register",
      "/forgot-password",
      "/reset-password",
      "/verify-email",
    ]);
    for (const deepLink of [
      "/strategies/setups/abc",
      "/decision/candidates/abc",
      "/execution/manual-demo/abc",
      "/decision/approvals/abc",
      "/decision/executions/abc",
      "/decision/plans/abc",
      "/decision/outcomes/abc",
      "/backtests/abc",
      "/strategy-lab/abc",
    ]) {
      expect(findRegisteredRoute(deepLink)?.kind, deepLink).toBe("deep-link");
    }
    expect(findRegisteredRoute("/decision/candidates")?.kind).toBe("diagnostics");
  });

  it("marks every redirecting page as kind redirect", () => {
    const redirectPages = new Set(
      routesOfKind("redirect").map((route) => route.path),
    );
    for (const rule of ROUTE_REDIRECTS) {
      const page = ROUTE_REGISTRY.find((route) => route.path === rule.source);
      // A query-only redirect (`/journal?tab=knowledge`) leaves its page live.
      if (page && !rule.has) expect(redirectPages.has(page.path), rule.source).toBe(true);
    }
    for (const page of redirectPages) {
      expect(ROUTE_REDIRECTS.some((rule) => rule.source === page), page).toBe(true);
    }
  });
});

describe("redirects", () => {
  it.each(ROUTE_REDIRECTS.map((rule) => [rule.source, rule]))(
    "%s lands on a real page",
    (_source, rule) => {
      const query = (rule.has ?? []).map(({ key, value }) => `${key}=${value ?? "1"}`).join("&");
      const sample = rule.source.replace(":id", "sample-id") + (query ? `?${query}` : "");
      const landing = resolveRoute(sample);
      expect(landing, `${rule.source} → ${rule.destination}`).not.toBeNull();
      expect(landing!.kind).not.toBe("redirect");
    },
  );

  it.each(
    ROUTE_REDIRECTS.filter((rule) => rule.via === "config").map((rule) => [rule.source, rule]),
  )("%s is served by next.config", async (_source, rule) => {
    const rules = await nextConfig.redirects!();
    expect(rules).toContainEqual(
      expect.objectContaining({ source: rule.source, destination: rule.destination, permanent: false }),
    );
  });

  it.each(PAGE_REDIRECTS.map((rule) => [rule.source, rule]))(
    "%s page calls redirect() with the registered target",
    async (_source, rule) => {
      redirectMock.mockClear();
      const mod = (await import(/* @vite-ignore */ pageFile(rule.source))) as {
        default: () => unknown;
      };
      mod.default();
      expect(redirectMock).toHaveBeenCalledExactlyOnceWith(rule.destination);
    },
  );
});

describe("navigation links resolve to real pages (no 404)", () => {
  const links = [...new Set([
    ...PRIMARY_DESTINATIONS.map((item) => item.href),
    ...SECONDARY_NAV.flatMap((group) => group.items.map((item) => item.href)),
    ...ADVANCED_ROUTES.map((route) => route.href),
  ])];

  it.each(links.map((href) => [href]))("%s", (href) => {
    expect(resolveRoute(href), href).not.toBeNull();
  });
});

describe("/risk belongs to one destination", () => {
  it("is not also a Settings prefix", () => {
    expect(SETTINGS_ROUTE_PREFIXES).not.toContain("/risk");
    expect(getDestinationId("/risk")).toBe("strategies");
    expect(getDestinationId("/risk/autonomous")).toBe("strategies");
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
