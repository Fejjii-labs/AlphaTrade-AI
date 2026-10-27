import { ROUTE_REGISTRY, routesOfKind } from "@/lib/navigation/route-registry";

/**
 * Operational, audit, validation and engineering routes. They are listed in
 * Settings → Diagnostics and the command menu, generated from the route
 * registry. Primary navigation does not list them.
 */
export type AdvancedRoute = {
  href: string;
  label: string;
  group: string;
};

export const ADVANCED_ROUTES: readonly AdvancedRoute[] = routesOfKind("diagnostics").map((route) => ({
  href: route.path,
  label: route.label,
  group: route.group!,
}));

/** Static section and diagnostics pages, for page subtitles and the command menu. */
export const LABELED_ROUTES: readonly AdvancedRoute[] = ROUTE_REGISTRY.filter(
  (route) => (route.kind === "section" || route.kind === "diagnostics") && !route.path.includes("["),
).map((route) => ({ href: route.path, label: route.label, group: route.group ?? "Pages" }));

/** Prefixes that belong to Settings, including dynamic detail routes. */
export const SETTINGS_ROUTE_PREFIXES: readonly string[] = [
  "/settings",
  "/billing",
  "/usage",
  "/invitations",
  "/audit",
  "/exchange",
  "/portfolio",
  "/positions",
  "/alerts",
  "/watcher",
  "/market-watcher",
  "/market",
  "/watchlist",
  "/tradingview-signals",
  "/paper-signal-orchestration",
  "/paper-validation",
  "/validation-priority",
  "/research-validation",
  "/backtests",
  "/decision",
  "/workspace",
  "/proposals",
  "/approvals",
  "/pre-trade",
  "/manual-levels",
  "/analytics",
  "/strategy-quality",
];

export function pathMatchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function matchesAnyPrefix(pathname: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => pathMatchesPrefix(pathname, prefix));
}

function longestMatch(routes: readonly AdvancedRoute[], pathname: string): AdvancedRoute | null {
  const matches = routes.filter((route) => pathMatchesPrefix(pathname, route.href)).sort(
    (a, b) => b.href.length - a.href.length,
  );
  return matches[0] ?? null;
}

/** Longest diagnostics route for a pathname, including dynamic children. */
export function resolveAdvancedRoute(pathname: string): AdvancedRoute | null {
  return longestMatch(ADVANCED_ROUTES, pathname);
}

/** Longest labeled section or diagnostics route for a pathname. */
export function resolveLabeledRoute(pathname: string): AdvancedRoute | null {
  return longestMatch(LABELED_ROUTES, pathname);
}

export function isAdvancedPath(pathname: string): boolean {
  if (pathname === "/settings/diagnostics" || pathname.startsWith("/settings/diagnostics/")) return true;
  if (resolveAdvancedRoute(pathname)) return true;
  return matchesAnyPrefix(pathname, SETTINGS_ROUTE_PREFIXES.filter((prefix) => prefix !== "/settings"));
}
