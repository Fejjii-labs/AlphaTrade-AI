/**
 * Config redirects for every removed route (next.config `redirects()`).
 * Each old URL lands on its nearest kept page. Query strings are preserved by
 * Next.js redirects by default; `:path*` also matches the bare prefix.
 */
export type RedirectQueryMatch = { type: "query"; key: string; value?: string };

export type PhaseBRedirect = {
  source: string;
  destination: string;
  permanent: boolean;
  /** Only redirect when the request has this query (Next.js `has`). */
  has?: RedirectQueryMatch[];
};

function redirectAll(destination: string, sources: readonly string[]): PhaseBRedirect[] {
  return sources.map((source) => ({ source, destination, permanent: false }));
}

export const PHASE_B_REDIRECTS: readonly PhaseBRedirect[] = [
  // Settings has four tabs: Account, Exchange, Alerts, Markets. Removed tabs land on Account.
  { source: "/billing", destination: "/settings", permanent: false },
  { source: "/usage", destination: "/settings", permanent: false },
  { source: "/settings/usage", destination: "/settings", permanent: false },
  { source: "/settings/billing", destination: "/settings", permanent: false },
  { source: "/invitations", destination: "/settings", permanent: false },
  { source: "/settings/team", destination: "/settings", permanent: false },
  { source: "/audit", destination: "/settings", permanent: false },
  { source: "/settings/audit", destination: "/settings", permanent: false },
  { source: "/settings/diagnostics", destination: "/settings", permanent: false },
  { source: "/settings/advanced", destination: "/settings", permanent: false },
  { source: "/settings/help", destination: "/settings", permanent: false },
  { source: "/settings/notifications", destination: "/settings/alerts", permanent: false },
  { source: "/exchange", destination: "/settings/exchange", permanent: false },
  // Account and bot limits share one form on Autonomous Trading; the kill switch moved there from /watchlist.
  { source: "/risk", destination: "/autonomous#limits", permanent: false },
  { source: "/risk/autonomous", destination: "/autonomous#limits", permanent: false },
  { source: "/watchlist", destination: "/settings/watcher", permanent: false },
  // Each family card holds its own 7-day history now.
  { source: "/strategies/detections", destination: "/strategies", permanent: false },
  // Knowledge is its own page; the old Journal tab follows it (query kept).
  {
    source: "/journal",
    has: [{ type: "query", key: "tab", value: "knowledge" }],
    destination: "/knowledge",
    permanent: false,
  },
  { source: "/strategy-lab/new", destination: "/agent?intent=strategy", permanent: false },
  { source: "/strategy-lab/:id/edit", destination: "/agent?strategy_id=:id", permanent: false },
  { source: "/strategy-lab/:id", destination: "/agent?strategy_id=:id", permanent: false },
  // Removed redirect pages.
  { source: "/dashboard", destination: "/", permanent: false },
  { source: "/coaching", destination: "/agent", permanent: false },
  { source: "/journal/statistics", destination: "/journal?tab=stats", permanent: false },
  // Removed diagnostics pages (UI slim, 2026-11-19).
  ...redirectAll("/settings/watcher", ["/watcher", "/market-watcher", "/market", "/manual-levels"]),
  ...redirectAll("/settings/alerts", [
    "/alerts",
    "/alerts/:path*",
    "/tradingview-signals",
    "/paper-signal-orchestration",
  ]),
  ...redirectAll("/autonomous", [
    "/decision",
    "/decision/:path*",
    "/approvals",
    "/proposals",
    "/pre-trade",
  ]),
  ...redirectAll("/strategies", [
    "/strategy-lab",
    "/paper-validation",
    "/paper-validation/:path*",
    "/research-validation",
    "/validation-priority",
    "/strategy-quality",
    "/backtests/:path*",
  ]),
  ...redirectAll("/journal", [
    "/journal/import",
    "/journal/comparison",
    "/analytics",
    "/analytics/:path*",
    "/learning-analytics",
    "/lessons",
    "/lessons/:path*",
  ]),
  ...redirectAll("/", ["/portfolio", "/positions"]),
  ...redirectAll("/agent", ["/workspace"]),
];

/** Pages that must stay reachable from navigation (for tests). */
export const PHASE_B_CAPABILITY_PATHS: readonly string[] = [
  "/",
  "/journal",
  "/strategies",
  "/autonomous",
  "/agent",
  "/knowledge",
  "/settings",
  "/settings/exchange",
  "/settings/alerts",
  "/settings/watcher",
] as const;
