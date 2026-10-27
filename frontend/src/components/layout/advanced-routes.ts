/**
 * Operational, audit, validation, billing, and engineering routes.
 * They stay reachable from Settings / Advanced and the command menu.
 * Primary navigation does not list them.
 */

export type AdvancedRoute = {
  href: string;
  label: string;
  group: string;
};

export const ADVANCED_ROUTE_GROUPS = [
  "Account",
  "Portfolio",
  "Market and alerts",
  "Decisions",
  "Validation",
  "Review",
  "Strategy tools",
  "Journal tools",
  "Knowledge tools",
] as const;

export const ADVANCED_ROUTES: readonly AdvancedRoute[] = [
  { href: "/settings/billing", label: "Billing & Usage", group: "Account" },
  { href: "/settings/team", label: "Team", group: "Account" },
  { href: "/settings/audit", label: "Audit", group: "Account" },
  { href: "/settings/exchange", label: "Exchange diagnostics", group: "Account" },

  { href: "/portfolio", label: "Portfolio", group: "Portfolio" },
  { href: "/positions", label: "Positions", group: "Portfolio" },
  { href: "/risk", label: "Risk settings", group: "Portfolio" },

  { href: "/tradingview-signals", label: "Signals inbox", group: "Market and alerts" },
  { href: "/alerts", label: "Alerts", group: "Market and alerts" },
  { href: "/alerts/review", label: "Setup Review", group: "Market and alerts" },
  { href: "/watcher", label: "Watcher", group: "Market and alerts" },
  { href: "/market-watcher", label: "Market Watcher", group: "Market and alerts" },
  { href: "/market", label: "Market Monitor", group: "Market and alerts" },
  { href: "/watchlist", label: "Watchlist", group: "Market and alerts" },
  {
    href: "/paper-signal-orchestration",
    label: "Signal Orchestration",
    group: "Market and alerts",
  },

  { href: "/decision", label: "Decision", group: "Decisions" },
  { href: "/decision/market", label: "Market quality", group: "Decisions" },
  { href: "/decision/candidates", label: "Candidates", group: "Decisions" },
  { href: "/decision/strategy", label: "Strategy performance", group: "Decisions" },
  { href: "/workspace", label: "AI assist", group: "Decisions" },
  { href: "/proposals", label: "Proposals", group: "Decisions" },
  { href: "/approvals", label: "Approvals", group: "Decisions" },
  { href: "/pre-trade", label: "Pre-Trade", group: "Decisions" },
  { href: "/manual-levels", label: "Manual Levels", group: "Decisions" },

  { href: "/paper-validation", label: "Validate hub", group: "Validation" },
  { href: "/paper-validation/drafts", label: "Drafts", group: "Validation" },
  { href: "/paper-validation/candidates", label: "Candidates", group: "Validation" },
  { href: "/paper-validation/run-plans", label: "Run Plans", group: "Validation" },
  { href: "/paper-validation/run-sessions", label: "Run Sessions", group: "Validation" },
  { href: "/validation-priority", label: "Validation Priority", group: "Validation" },
  { href: "/research-validation", label: "Research Validation", group: "Validation" },

  { href: "/analytics", label: "Analytics", group: "Review" },
  { href: "/journal/statistics", label: "Journal Statistics", group: "Review" },
  { href: "/journal/comparison", label: "Human vs System", group: "Review" },
  { href: "/learning-analytics", label: "Learning Analytics", group: "Review" },
  { href: "/coaching", label: "Coaching", group: "Review" },
  { href: "/strategy-quality", label: "Strategy Quality", group: "Review" },

  { href: "/strategy-lab", label: "Strategy Lab", group: "Strategy tools" },

  { href: "/journal/import", label: "Import", group: "Journal tools" },
  { href: "/lessons", label: "Lessons", group: "Knowledge tools" },
] as const;

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

/** Longest catalogued advanced route for a pathname, including dynamic children. */
export function resolveAdvancedRoute(pathname: string): AdvancedRoute | null {
  const matches = ADVANCED_ROUTES.filter((route) => pathMatchesPrefix(pathname, route.href)).sort(
    (a, b) => b.href.length - a.href.length,
  );
  return matches[0] ?? null;
}

export function isAdvancedPath(pathname: string): boolean {
  if (pathname === "/settings/advanced" || pathname.startsWith("/settings/advanced/")) return true;
  if (resolveAdvancedRoute(pathname)) return true;
  return matchesAnyPrefix(pathname, SETTINGS_ROUTE_PREFIXES.filter((prefix) => prefix !== "/settings"));
}
