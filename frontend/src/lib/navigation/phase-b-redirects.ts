/**
 * AT-040 Phase B redirects approved by AT-039 screen inventory.
 * Settings hub consolidations only. Advanced placements keep existing URLs.
 * Query strings are preserved by Next.js redirects by default.
 */
export type RedirectQueryMatch = { type: "query"; key: string; value?: string };

export type PhaseBRedirect = {
  source: string;
  destination: string;
  permanent: boolean;
  /** Only redirect when the request has this query (Next.js `has`). */
  has?: RedirectQueryMatch[];
};

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
] as const;

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
