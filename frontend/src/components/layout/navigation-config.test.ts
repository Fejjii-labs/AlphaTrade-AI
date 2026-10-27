import { describe, expect, it } from "vitest";

import {
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
} from "@/components/layout/navigation-config";
import {
  PHASE_B_CAPABILITY_PATHS,
  PHASE_B_REDIRECTS,
} from "@/lib/navigation/phase-b-redirects";

describe("Trader primary navigation", () => {
  it("exposes the seven destinations in sidebar order", () => {
    expect(PRIMARY_DESTINATIONS.map((d) => [d.id, d.label, d.href])).toEqual([
      ["dashboard", "Dashboard", "/"],
      ["autonomous", "Autonomous", "/autonomous"],
      ["strategies", "Strategies", "/strategies"],
      ["journal", "Trades", "/journal"],
      ["agent", "Agent", "/agent"],
      ["knowledge", "Knowledge", "/knowledge"],
      ["settings", "Settings", "/settings"],
    ]);
    expect(new Set(PRIMARY_DESTINATIONS.map((d) => d.href)).size).toBe(7);
  });

  it("puts four destinations on the phone bar and the rest behind More", () => {
    expect(MOBILE_BOTTOM_DESTINATION_IDS).toEqual(["dashboard", "journal", "strategies", "agent"]);
    expect(MOBILE_MORE_DESTINATION_IDS).toEqual(["autonomous", "knowledge", "settings"]);
    expect(
      [...MOBILE_BOTTOM_DESTINATION_IDS, ...MOBILE_MORE_DESTINATION_IDS].sort(),
    ).toEqual(PRIMARY_DESTINATIONS.map((d) => d.id).sort());
  });

  it("keeps specialized routes reachable but out of the primary list", () => {
    const paths = PRIMARY_DESTINATIONS.map((destination) => destination.href);
    for (const path of ["/watcher", "/strategy-lab", "/lessons", "/market"]) {
      expect(paths).not.toContain(path);
      expect(listReachableHrefs()).toContain(path);
    }
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
    ["/strategy-lab", "strategies"],
    ["/strategy-lab/strategy-7", "strategies"],
    ["/journal", "journal"],
    ["/journal/statistics", "journal"],
    ["/journal/import", "journal"],
    ["/journal/comparison", "journal"],
    ["/learning-analytics", "journal"],
    ["/coaching", "journal"],
    ["/agent", "agent"],
    ["/agent/history", "agent"],
    ["/knowledge", "knowledge"],
    ["/knowledge/documents/doc-1", "knowledge"],
    ["/lessons", "knowledge"],
    ["/lessons/lesson-42", "knowledge"],
    ["/settings", "settings"],
    ["/settings/diagnostics", "settings"],
    ["/settings/advanced", "settings"],
    ["/settings/watcher", "settings"],
    ["/settings/billing", "settings"],
    ["/watchlist", "settings"],
    ["/audit", "settings"],
    ["/exchange", "settings"],
    ["/tradingview-signals", "settings"],
    ["/paper-validation/candidates/cand-1", "settings"],
    ["/backtests/bt-123", "settings"],
    ["/decision/candidates/cand-1", "settings"],
    ["/execution/manual-demo/exact-command", "settings"],
    ["/portfolio", "settings"],
    ["/workspace", "settings"],
  ])("%s → %s", (path, id) => {
    expect(getDestinationId(path)).toBe(id);
    expect(
      PRIMARY_DESTINATIONS.filter((d) => isPrimaryDestinationActive(path, d)).map((d) => d.id),
    ).toEqual([id]);
  });

  it("respects path boundaries", () => {
    expect(getDestinationId("/knowledge-base")).toBeNull();
    expect(getDestinationId("/lessons-learned")).toBeNull();
    expect(getDestinationId("/autonomous-old")).toBeNull();
    expect(getDestinationId("/riskier")).toBeNull();
  });

  it("matches nav links exactly where a parent must not light up for children", () => {
    expect(isNavLinkActive("/", "/")).toBe(true);
    expect(isNavLinkActive("/workspace", "/")).toBe(false);
    expect(isNavLinkActive("/journal", "/journal")).toBe(true);
    expect(isNavLinkActive("/journal/import", "/journal")).toBe(false);
    expect(isNavLinkActive("/autonomous", "/autonomous")).toBe(true);
    expect(isNavLinkActive("/knowledge", "/knowledge")).toBe(true);
    expect(isNavLinkActive("/settings", "/settings")).toBe(true);
    expect(isNavLinkActive("/settings/billing", "/settings")).toBe(false);
    expect(isNavLinkActive("/risk/autonomous", "/risk")).toBe(true);
  });

  it("keeps one Settings parent link for retained operational routes", () => {
    const settings = getSecondaryItems("settings");
    expect(settings.map((item) => item.href)).toEqual(["/settings"]);
    expect(getSecondaryItems("dashboard")).toEqual([]);
    expect(resolveSecondaryActiveHref("/settings", settings)).toBe("/settings");
    expect(resolveSecondaryActiveHref("/tradingview-signals", settings)).toBeNull();
  });

  it("resolves trader titles and subtitles", () => {
    for (const [path, title] of [
      ["/", "Dashboard"],
      ["/autonomous", "Autonomous"],
      ["/strategies", "Strategies"],
      ["/journal", "Trades"],
      ["/agent", "Agent"],
      ["/knowledge", "Knowledge"],
      ["/settings", "Settings"],
    ]) {
      expect(resolvePageIdentity(path!)).toMatchObject({ title, subtitle: null });
    }
    expect(resolvePageIdentity("/settings/diagnostics")).toMatchObject({ title: "Settings", subtitle: "Diagnostics" });
    expect(resolvePageIdentity("/journal/import")).toMatchObject({ title: "Trades", subtitle: "Journal import" });
    expect(resolvePageIdentity("/lessons")).toMatchObject({ title: "Knowledge", subtitle: "Lessons" });
    expect(resolvePageIdentity("/workspace")).toMatchObject({ title: "Settings", subtitle: "AI assist" });
  });

  it("keeps required capability routes reachable", () => {
    const reachable = new Set(listReachableHrefs());
    for (const path of PHASE_B_CAPABILITY_PATHS) {
      if (path.startsWith("/backtests/")) {
        expect(getDestinationId(path)).toBe("settings");
        continue;
      }
      expect(reachable.has(path), path).toBe(true);
    }
    for (const path of ["/settings/diagnostics", "/agent", "/strategies", "/strategy-lab", "/knowledge", "/autonomous", "/lessons", "/watcher", "/market"])
      expect(reachable.has(path), path).toBe(true);
  });
});

describe("AT-040 Phase B redirects", () => {
  it("defines Settings hub redirects without loops", () => {
    expect(PHASE_B_REDIRECTS.length).toBeGreaterThan(0);
    const sources = new Set(PHASE_B_REDIRECTS.map((rule) => rule.source));
    const destinations = new Set(
      PHASE_B_REDIRECTS.map((rule) => rule.destination),
    );
    for (const rule of PHASE_B_REDIRECTS) {
      expect(rule.source).not.toBe(rule.destination);
      const home = rule.source.startsWith("/strategy-lab/") ? "/agent?" : rule.source.startsWith("/risk") ? "/autonomous#" : rule.source.startsWith("/strategies/") ? "/strategies" : rule.source === "/journal" ? "/knowledge" : "/settings/";
      expect(rule.destination.startsWith(home), rule.source).toBe(true);
      expect(destinations.has(rule.source)).toBe(false);
    }
    expect(sources.has("/billing")).toBe(true);
    expect(sources.has("/usage")).toBe(true);
    expect(sources.has("/settings/usage")).toBe(true);
    expect(sources.has("/invitations")).toBe(true);
    expect(sources.has("/audit")).toBe(true);
    expect(sources.has("/exchange")).toBe(true);
    expect(
      PHASE_B_REDIRECTS.find((rule) => rule.source === "/billing")?.destination,
    ).toBe("/settings/billing");
    expect(
      PHASE_B_REDIRECTS.find((rule) => rule.source === "/usage")?.destination,
    ).toBe("/settings/billing#usage");
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
