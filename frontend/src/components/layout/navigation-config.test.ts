import { describe, expect, it } from "vitest";

import {
  ALL_DESTINATIONS,
  getDestinationId,
  getSecondaryItems,
  isNavLinkActive,
  isPrimaryDestinationActive,
  listReachableHrefs,
  MOBILE_BOTTOM_DESTINATION_IDS,
  MOBILE_MORE_DESTINATION_IDS,
  PRIMARY_DESTINATIONS,
  resolvePageIdentity,
  resolveSecondaryActiveHref,
  SETTINGS_DESTINATION,
} from "@/components/layout/navigation-config";
import {
  PHASE_B_CAPABILITY_PATHS,
  PHASE_B_REDIRECTS,
} from "@/lib/navigation/phase-b-redirects";

describe("Trader primary navigation", () => {
  it("exposes the five pages in sidebar order, with Settings apart", () => {
    expect(PRIMARY_DESTINATIONS.map((d) => [d.id, d.label, d.href])).toEqual([
      ["dashboard", "Dashboard", "/"],
      ["journal", "Trades", "/journal"],
      ["strategies", "Strategies", "/strategies"],
      ["autonomous", "Bot", "/autonomous"],
      ["agent", "Agent", "/agent"],
    ]);
    expect(SETTINGS_DESTINATION).toMatchObject({ id: "settings", label: "Settings", href: "/settings" });
    expect(ALL_DESTINATIONS.map((d) => d.id)).toEqual([
      ...PRIMARY_DESTINATIONS.map((d) => d.id),
      "settings",
    ]);
  });

  it("puts four destinations on the phone bar and Agent and Settings behind More", () => {
    expect(MOBILE_BOTTOM_DESTINATION_IDS).toEqual(["dashboard", "journal", "strategies", "autonomous"]);
    expect(MOBILE_MORE_DESTINATION_IDS).toEqual(["agent", "settings"]);
    expect(
      [...MOBILE_BOTTOM_DESTINATION_IDS, ...MOBILE_MORE_DESTINATION_IDS].sort(),
    ).toEqual(ALL_DESTINATIONS.map((d) => d.id).sort());
  });

  // Every primary route, sub-route, redirected (old) route and deep link lands
  // on exactly one destination.
  it.each([
    ["/", "dashboard"],
    ["/autonomous", "autonomous"],
    ["/risk", "autonomous"],
    ["/risk/autonomous", "autonomous"],
    ["/strategies", "strategies"],
    ["/strategies/detections", "strategies"],
    ["/strategies/setups/setup-1", "strategies"],
    ["/journal", "journal"],
    ["/journal/statistics", "journal"],
    ["/coaching", "journal"],
    ["/agent", "agent"],
    ["/agent/history", "agent"],
    ["/knowledge", "agent"],
    ["/knowledge/documents/doc-1", "agent"],
    ["/settings", "settings"],
    ["/settings/exchange", "settings"],
    ["/settings/alerts", "settings"],
    ["/settings/watcher", "settings"],
    ["/execution/manual-demo/exact-command", "settings"],
  ])("%s → %s", (path, id) => {
    expect(getDestinationId(path)).toBe(id);
    expect(
      ALL_DESTINATIONS.filter((d) => isPrimaryDestinationActive(path, d)).map((d) => d.id),
    ).toEqual([id]);
  });

  it("respects path boundaries", () => {
    expect(getDestinationId("/knowledge-base")).toBeNull();
    expect(getDestinationId("/autonomous-old")).toBeNull();
    expect(getDestinationId("/riskier")).toBeNull();
  });

  it("matches nav links exactly where a parent must not light up for children", () => {
    expect(isNavLinkActive("/", "/")).toBe(true);
    expect(isNavLinkActive("/agent", "/")).toBe(false);
    expect(isNavLinkActive("/journal", "/journal")).toBe(true);
    expect(isNavLinkActive("/journal/statistics", "/journal")).toBe(false);
    expect(isNavLinkActive("/autonomous", "/autonomous")).toBe(true);
    expect(isNavLinkActive("/settings", "/settings")).toBe(true);
    expect(isNavLinkActive("/settings/alerts", "/settings")).toBe(false);
  });

  it("keeps one Settings parent link for the manual demo detail page", () => {
    const settings = getSecondaryItems("settings");
    expect(settings.map((item) => item.href)).toEqual(["/settings"]);
    expect(getSecondaryItems("dashboard")).toEqual([]);
    expect(resolveSecondaryActiveHref("/settings", settings)).toBe("/settings");
  });

  it("resolves trader titles and subtitles", () => {
    for (const [path, title] of [
      ["/", "Dashboard"],
      ["/autonomous", "Bot"],
      ["/strategies", "Strategies"],
      ["/journal", "Trades"],
      ["/agent", "Agent"],
      ["/settings", "Settings"],
    ]) {
      expect(resolvePageIdentity(path!)).toMatchObject({ title, subtitle: null });
    }
    expect(resolvePageIdentity("/settings/alerts")).toMatchObject({ title: "Settings", subtitle: "Alerts" });
    expect(resolvePageIdentity("/settings/watcher")).toMatchObject({ title: "Settings", subtitle: "Markets" });
    expect(resolvePageIdentity("/knowledge")).toMatchObject({ title: "Agent", subtitle: "Knowledge" });
  });

  it("keeps required capability routes reachable", () => {
    const reachable = new Set(listReachableHrefs());
    for (const path of PHASE_B_CAPABILITY_PATHS) expect(reachable.has(path), path).toBe(true);
    for (const path of ["/agent", "/strategies", "/knowledge", "/autonomous", "/settings"])
      expect(reachable.has(path), path).toBe(true);
  });
});

describe("AT-040 Phase B redirects", () => {
  it("defines redirects without loops", () => {
    expect(PHASE_B_REDIRECTS.length).toBeGreaterThan(0);
    const sources = new Set(PHASE_B_REDIRECTS.map((rule) => rule.source));
    for (const rule of PHASE_B_REDIRECTS) {
      expect(rule.source).not.toBe(rule.destination);
      expect(sources.has(rule.destination.split(/[?#]/)[0]!), rule.source).toBe(false);
    }
  });

  it.each([
    ["/billing", "/settings"],
    ["/usage", "/settings"],
    ["/settings/usage", "/settings"],
    ["/settings/billing", "/settings"],
    ["/invitations", "/settings"],
    ["/settings/team", "/settings"],
    ["/audit", "/settings"],
    ["/settings/audit", "/settings"],
    ["/settings/diagnostics", "/settings"],
    ["/settings/advanced", "/settings"],
    ["/settings/help", "/settings"],
    ["/settings/notifications", "/settings/alerts"],
    ["/exchange", "/settings/exchange"],
  ])("sends removed Settings route %s to %s", (source, destination) => {
    expect(PHASE_B_REDIRECTS.find((rule) => rule.source === source)?.destination).toBe(destination);
  });

  it("does not redirect dynamic capability IDs or paper-validation paths", () => {
    for (const rule of PHASE_B_REDIRECTS) {
      expect(rule.source.includes("[")).toBe(false);
      expect(rule.source.startsWith("/paper-validation")).toBe(false);
      expect(rule.source.startsWith("/backtests")).toBe(false);
      expect(rule.source === "/strategy-lab/:id").toBe(false);
    }
  });

  it("preserves query parameters by using path-only redirect sources", () => {
    for (const rule of PHASE_B_REDIRECTS) {
      expect(rule.source.includes("?")).toBe(false);
      if (!rule.source.startsWith("/strategy-lab/")) expect(rule.destination.includes("?")).toBe(false);
    }
  });
});

it("highlights Settings for exact manual demo detail and Journal for trade detail", () => {
  expect(getDestinationId("/execution/manual-demo/exact-command")).toBe(
    "settings",
  );
  expect(getDestinationId("/journal")).toBe("journal");
});
