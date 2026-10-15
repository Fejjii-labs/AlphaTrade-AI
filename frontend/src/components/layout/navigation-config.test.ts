import { describe, expect, it } from "vitest";

import {
  getDestinationId,
  getSecondaryItems,
  isNavLinkActive,
  isPrimaryDestinationActive,
  listReachableHrefs,
  MOBILE_BOTTOM_DESTINATION_IDS,
  PRIMARY_DESTINATIONS,
  resolvePageIdentity,
  resolveSecondaryActiveHref,
} from "@/components/layout/navigation-config";
import {
  PHASE_B_CAPABILITY_PATHS,
  PHASE_B_REDIRECTS,
} from "@/lib/navigation/phase-b-redirects";

describe("Trader primary navigation", () => {
  it("exposes exactly five primary destinations", () => {
    expect(PRIMARY_DESTINATIONS).toHaveLength(5);
    expect(PRIMARY_DESTINATIONS.map((destination) => destination.id)).toEqual([
      "dashboard",
      "agent",
      "journal",
      "strategies",
      "settings",
    ]);
    expect(
      PRIMARY_DESTINATIONS.map((destination) => destination.label),
    ).toEqual([
      "Dashboard",
      "Agent",
      "Journal & Knowledge",
      "Strategies",
      "Settings",
    ]);
  });

  it("puts every primary destination on the mobile tab bar", () => {
    expect(MOBILE_BOTTOM_DESTINATION_IDS).toEqual([
      "dashboard",
      "agent",
      "journal",
      "strategies",
      "settings",
    ]);
  });

  it("keeps primary paths unique and specialized routes outside the primary list", () => {
    const paths = PRIMARY_DESTINATIONS.map((destination) => destination.href);
    expect(paths).toEqual([
      "/",
      "/agent",
      "/journal",
      "/strategies",
      "/settings",
    ]);
    expect(new Set(paths).size).toBe(5);
    for (const path of [
      "/watcher",
      "/risk",
      "/strategy-lab",
      "/lessons",
      "/market",
    ]) {
      expect(paths).not.toContain(path);
      expect(listReachableHrefs()).toContain(path);
    }
  });

  it("selects exactly one destination for nested links and respects path boundaries", () => {
    for (const [path, label] of [
      ["/knowledge/documents/doc-1", "Journal & Knowledge"],
      ["/lessons/lesson-42", "Journal & Knowledge"],
      ["/journal/statistics", "Journal & Knowledge"],
      ["/strategy-lab/strategy-7", "Strategies"],
      ["/agent/history", "Agent"],
      ["/settings/advanced", "Settings"],
    ]) {
      expect(
        PRIMARY_DESTINATIONS.filter((item) =>
          isPrimaryDestinationActive(path!, item),
        ).map((item) => item.label),
      ).toEqual([label]);
    }
    expect(getDestinationId("/knowledge-base")).toBeNull();
    expect(getDestinationId("/lessons-learned")).toBeNull();
  });

  it("maps trader hubs and retained routes onto the five destinations", () => {
    expect(getDestinationId("/")).toBe("dashboard");
    expect(getDestinationId("/agent")).toBe("agent");
    expect(getDestinationId("/strategies")).toBe("strategies");
    expect(getDestinationId("/strategy-lab")).toBe("strategies");
    expect(getDestinationId("/strategy-lab/new")).toBe("strategies");
    expect(getDestinationId("/knowledge")).toBe("journal");
    expect(getDestinationId("/journal")).toBe("journal");
    expect(getDestinationId("/journal/comparison")).toBe("journal");
    expect(getDestinationId("/journal/import")).toBe("journal");
    expect(getDestinationId("/lessons")).toBe("journal");
    expect(getDestinationId("/coaching")).toBe("journal");
    expect(getDestinationId("/learning-analytics")).toBe("journal");
    expect(getDestinationId("/settings")).toBe("settings");
    expect(getDestinationId("/settings/advanced")).toBe("settings");
    expect(getDestinationId("/settings/billing")).toBe("settings");
    expect(getDestinationId("/tradingview-signals")).toBe("settings");
    expect(getDestinationId("/paper-validation")).toBe("settings");
    expect(getDestinationId("/paper-validation/candidates/cand-1")).toBe(
      "settings",
    );
    expect(getDestinationId("/paper-signal-orchestration")).toBe("settings");
    expect(getDestinationId("/backtests/bt-123")).toBe("settings");
    expect(getDestinationId("/portfolio")).toBe("settings");
    expect(getDestinationId("/risk")).toBe("strategies");
    expect(getDestinationId("/workspace")).toBe("settings");
    expect(getDestinationId("/decision")).toBe("settings");
    expect(getDestinationId("/decision/candidates")).toBe("settings");
    expect(getDestinationId("/analytics")).toBe("settings");
  });

  it("does not treat nested routes as the dashboard", () => {
    expect(isNavLinkActive("/", "/")).toBe(true);
    expect(isNavLinkActive("/workspace", "/")).toBe(false);
    expect(isNavLinkActive("/journal", "/journal")).toBe(true);
    expect(isNavLinkActive("/journal/import", "/journal")).toBe(false);
    const dashboard = PRIMARY_DESTINATIONS[0];
    expect(isPrimaryDestinationActive("/", dashboard)).toBe(true);
    expect(isPrimaryDestinationActive("/portfolio", dashboard)).toBe(false);
    expect(isPrimaryDestinationActive("/agent", PRIMARY_DESTINATIONS[1]!)).toBe(
      true,
    );
  });

  it("keeps one Settings parent link for retained operational routes", () => {
    const settings = getSecondaryItems("settings");
    expect(settings.map((item) => item.href)).toEqual(["/settings"]);
    expect(settings.map((item) => item.label)).toEqual(["Back to Settings"]);
    expect(getSecondaryItems("dashboard")).toEqual([]);
    expect(getSecondaryItems("agent")).toEqual([]);
    expect(resolveSecondaryActiveHref("/settings", settings)).toBe("/settings");
    expect(
      resolveSecondaryActiveHref("/settings/billing", settings),
    ).toBeNull();
    expect(
      resolveSecondaryActiveHref("/tradingview-signals", settings),
    ).toBeNull();
  });

  it("resolves trader titles and advanced subtitles", () => {
    expect(resolvePageIdentity("/")).toMatchObject({
      title: "Dashboard",
      subtitle: null,
    });
    expect(resolvePageIdentity("/agent")).toMatchObject({
      title: "Agent",
      subtitle: null,
    });
    expect(resolvePageIdentity("/strategies")).toMatchObject({
      title: "Strategies",
      subtitle: null,
    });
    expect(resolvePageIdentity("/journal")).toMatchObject({
      title: "Journal & Knowledge",
      subtitle: null,
    });
    expect(resolvePageIdentity("/settings")).toMatchObject({
      title: "Settings",
      subtitle: null,
    });
    expect(resolvePageIdentity("/settings/advanced")).toMatchObject({
      title: "Settings",
      subtitle: "Advanced",
    });
    expect(resolvePageIdentity("/knowledge")).toMatchObject({
      title: "Journal & Knowledge",
      subtitle: null,
    });
    expect(resolvePageIdentity("/journal/import")).toMatchObject({
      title: "Journal & Knowledge",
      subtitle: "Import",
    });
    expect(resolvePageIdentity("/alerts/review")).toMatchObject({
      title: "Settings",
      subtitle: "Setup Review",
    });
    expect(
      resolvePageIdentity("/paper-validation/candidates/example"),
    ).toMatchObject({
      title: "Settings",
      subtitle: "Candidates",
    });
    expect(resolvePageIdentity("/workspace")).toMatchObject({
      title: "Settings",
      subtitle: "AI assist",
    });
    expect(resolvePageIdentity("/risk")).toMatchObject({
      title: "Strategies",
      subtitle: "Risk settings",
    });
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
    expect(reachable.has("/settings/advanced")).toBe(true);
    expect(reachable.has("/agent")).toBe(true);
    expect(reachable.has("/strategies")).toBe(true);
    expect(reachable.has("/strategy-lab")).toBe(true);
    expect(getDestinationId("/knowledge")).toBe("journal");
    expect(reachable.has("/lessons")).toBe(true);
    expect(reachable.has("/watcher")).toBe(true);
    expect(reachable.has("/market")).toBe(true);
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
      expect(rule.destination.startsWith(rule.source.startsWith("/strategy-lab/") ? "/agent?" : "/settings/")).toBe(true);
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
