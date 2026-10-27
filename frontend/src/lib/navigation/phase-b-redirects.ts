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
  { source: "/billing", destination: "/settings/billing", permanent: false },
  // Billing & Usage share one canonical Settings route.
  { source: "/usage", destination: "/settings/billing#usage", permanent: false },
  { source: "/settings/usage", destination: "/settings/billing#usage", permanent: false },
  { source: "/invitations", destination: "/settings/team", permanent: false },
  { source: "/audit", destination: "/settings/audit", permanent: false },
  { source: "/exchange", destination: "/settings/exchange", permanent: false },
  { source: "/settings/advanced", destination: "/settings/diagnostics", permanent: false },
  // Risk and bot settings live on Autonomous; the kill switch moved there from /watchlist.
  { source: "/risk", destination: "/autonomous#account", permanent: false },
  { source: "/risk/autonomous", destination: "/autonomous#bot", permanent: false },
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

/** Capability paths that must remain reachable after Phase B (for tests). */
export const PHASE_B_CAPABILITY_PATHS: readonly string[] = [
  "/",
  "/tradingview-signals",
  "/paper-validation",
  "/paper-validation/candidates",
  "/paper-validation/drafts",
  "/paper-validation/run-plans",
  "/paper-validation/run-sessions",
  "/paper-signal-orchestration",
  "/journal",
  "/journal/comparison",
  "/backtests/example-id",
  "/portfolio",
  "/settings",
  "/settings/billing",
  "/autonomous",
  "/workspace",
  "/decision",
  "/proposals",
  "/approvals",
] as const;
