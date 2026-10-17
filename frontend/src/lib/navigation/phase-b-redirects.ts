/**
 * AT-040 Phase B redirects approved by AT-039 screen inventory.
 * Settings hub consolidations only. Advanced placements keep existing URLs.
 * Query strings are preserved by Next.js redirects by default.
 */
export type PhaseBRedirect = {
  source: string;
  destination: string;
  permanent: boolean;
};

export const PHASE_B_REDIRECTS: readonly PhaseBRedirect[] = [
  { source: "/billing", destination: "/settings/billing", permanent: false },
  // Billing & Usage share one canonical Settings route.
  { source: "/usage", destination: "/settings/billing#usage", permanent: false },
  { source: "/settings/usage", destination: "/settings/billing#usage", permanent: false },
  { source: "/invitations", destination: "/settings/team", permanent: false },
  { source: "/audit", destination: "/settings/audit", permanent: false },
  { source: "/exchange", destination: "/settings/exchange", permanent: false },
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
  "/risk",
  "/workspace",
  "/decision",
  "/proposals",
  "/approvals",
] as const;
