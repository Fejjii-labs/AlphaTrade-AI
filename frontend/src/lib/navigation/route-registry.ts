import { PHASE_B_REDIRECTS } from "@/lib/navigation/phase-b-redirects";

/**
 * Every app route, classified once (UI overhaul B1).
 *
 * - primary: a sidebar destination.
 * - section: a page inside a primary destination (Settings sections, Risk).
 * - deep-link: a detail page opened from a list, an alert or Telegram.
 * - diagnostics: kept working, listed only in the Settings diagnostics index.
 * - redirect: the route only forwards elsewhere (see ROUTE_REDIRECTS).
 * - public: signed-out pages.
 *
 * `path` uses the App Router folder form (`[id]` for dynamic segments, route
 * groups dropped). Tests check that this list matches the `page.tsx` files.
 */
export type RouteKind =
  | "primary"
  | "section"
  | "deep-link"
  | "diagnostics"
  | "redirect"
  | "public";

export type DiagnosticsGroup =
  | "Market & alerts"
  | "Decisions"
  | "Validation & research"
  | "Journal tools"
  | "Portfolio"
  | "System";

export type RegisteredRoute = {
  path: string;
  kind: RouteKind;
  label: string;
  group?: DiagnosticsGroup;
};

export const DIAGNOSTICS_GROUPS: readonly DiagnosticsGroup[] = [
  "Market & alerts",
  "Decisions",
  "Validation & research",
  "Journal tools",
  "Portfolio",
  "System",
];

export const ROUTE_REGISTRY: readonly RegisteredRoute[] = [
  { path: "/", kind: "primary", label: "Dashboard" },
  { path: "/agent", kind: "primary", label: "Agent" },
  { path: "/journal", kind: "primary", label: "Journal" },
  { path: "/knowledge", kind: "primary", label: "Knowledge" },
  { path: "/strategies", kind: "primary", label: "Strategies" },
  { path: "/settings", kind: "primary", label: "Settings" },

  { path: "/risk", kind: "section", label: "Risk settings" },
  { path: "/risk/autonomous", kind: "section", label: "Autonomous paper bot" },
  { path: "/strategies/detections", kind: "section", label: "Recent detections" },
  { path: "/watchlist", kind: "section", label: "Watchlist" },
  { path: "/settings/billing", kind: "section", label: "Billing & Usage" },
  { path: "/settings/team", kind: "section", label: "Team" },
  { path: "/settings/exchange", kind: "section", label: "Exchanges" },
  { path: "/settings/help", kind: "section", label: "Help" },
  { path: "/settings/notifications", kind: "section", label: "Notifications" },
  { path: "/settings/watcher", kind: "section", label: "Watcher" },
  { path: "/settings/diagnostics", kind: "section", label: "Diagnostics" },

  { path: "/strategies/setups/[id]", kind: "deep-link", label: "Setup" },
  { path: "/strategy-lab/[id]", kind: "deep-link", label: "Strategy" },
  { path: "/backtests/[id]", kind: "deep-link", label: "Backtest" },
  { path: "/execution/manual-demo/[commandId]", kind: "deep-link", label: "Manual demo order" },
  { path: "/decision/candidates/[candidateId]", kind: "deep-link", label: "Candidate" },
  { path: "/decision/approvals/[approvalId]", kind: "deep-link", label: "Approval" },
  { path: "/decision/executions/[executionId]", kind: "deep-link", label: "Execution" },
  { path: "/decision/plans/[planId]", kind: "deep-link", label: "Trade plan" },
  { path: "/decision/outcomes/[outcomeId]", kind: "deep-link", label: "Outcome" },
  { path: "/paper-validation/candidates/[candidateId]", kind: "deep-link", label: "Validation candidate" },
  { path: "/paper-validation/drafts/[draftId]", kind: "deep-link", label: "Validation draft" },
  { path: "/paper-validation/run-plans/[planId]", kind: "deep-link", label: "Run plan" },
  { path: "/paper-validation/run-sessions/[sessionId]", kind: "deep-link", label: "Run session" },

  { path: "/watcher", kind: "diagnostics", label: "Scanner", group: "Market & alerts" },
  { path: "/market-watcher", kind: "diagnostics", label: "Market watcher", group: "Market & alerts" },
  { path: "/market", kind: "diagnostics", label: "Market monitor", group: "Market & alerts" },
  { path: "/alerts", kind: "diagnostics", label: "Alerts", group: "Market & alerts" },
  { path: "/alerts/review", kind: "diagnostics", label: "Setup review", group: "Market & alerts" },
  { path: "/tradingview-signals", kind: "diagnostics", label: "Signals inbox", group: "Market & alerts" },
  { path: "/manual-levels", kind: "diagnostics", label: "Manual levels", group: "Market & alerts" },
  { path: "/paper-signal-orchestration", kind: "diagnostics", label: "Signal orchestration", group: "Market & alerts" },

  { path: "/decision", kind: "diagnostics", label: "Decision", group: "Decisions" },
  { path: "/decision/candidates", kind: "diagnostics", label: "Candidates", group: "Decisions" },
  { path: "/decision/market", kind: "diagnostics", label: "Market quality", group: "Decisions" },
  { path: "/decision/strategy", kind: "diagnostics", label: "Strategy performance", group: "Decisions" },
  { path: "/approvals", kind: "diagnostics", label: "Approvals", group: "Decisions" },
  { path: "/proposals", kind: "diagnostics", label: "Proposals", group: "Decisions" },
  { path: "/pre-trade", kind: "diagnostics", label: "Pre-trade", group: "Decisions" },

  { path: "/paper-validation", kind: "diagnostics", label: "Validation hub", group: "Validation & research" },
  { path: "/paper-validation/drafts", kind: "diagnostics", label: "Validation drafts", group: "Validation & research" },
  { path: "/paper-validation/candidates", kind: "diagnostics", label: "Validation candidates", group: "Validation & research" },
  { path: "/paper-validation/run-plans", kind: "diagnostics", label: "Run plans", group: "Validation & research" },
  { path: "/paper-validation/run-sessions", kind: "diagnostics", label: "Run sessions", group: "Validation & research" },
  { path: "/research-validation", kind: "diagnostics", label: "Research validation", group: "Validation & research" },
  { path: "/validation-priority", kind: "diagnostics", label: "Validation priority", group: "Validation & research" },
  { path: "/strategy-quality", kind: "diagnostics", label: "Strategy quality", group: "Validation & research" },
  { path: "/strategy-lab", kind: "diagnostics", label: "Strategy lab", group: "Validation & research" },

  { path: "/journal/import", kind: "diagnostics", label: "Journal import", group: "Journal tools" },
  { path: "/journal/comparison", kind: "diagnostics", label: "Human vs system", group: "Journal tools" },
  { path: "/analytics", kind: "diagnostics", label: "Analytics", group: "Journal tools" },
  { path: "/learning-analytics", kind: "diagnostics", label: "Learning analytics", group: "Journal tools" },
  { path: "/lessons", kind: "diagnostics", label: "Lessons", group: "Journal tools" },

  { path: "/portfolio", kind: "diagnostics", label: "Portfolio", group: "Portfolio" },
  { path: "/positions", kind: "diagnostics", label: "Positions", group: "Portfolio" },
  { path: "/workspace", kind: "diagnostics", label: "AI assist", group: "Portfolio" },

  { path: "/settings/audit", kind: "diagnostics", label: "Audit", group: "System" },

  { path: "/dashboard", kind: "redirect", label: "Dashboard (old link)" },
  { path: "/coaching", kind: "redirect", label: "Coaching (old link)" },
  { path: "/journal/statistics", kind: "redirect", label: "Journal statistics (old link)" },
  { path: "/audit", kind: "redirect", label: "Audit (old link)" },
  { path: "/exchange", kind: "redirect", label: "Exchange (old link)" },
  { path: "/invitations", kind: "redirect", label: "Invitations (old link)" },
  { path: "/settings/advanced", kind: "redirect", label: "Advanced (old link)" },

  { path: "/login", kind: "public", label: "Sign in" },
  { path: "/register", kind: "public", label: "Register" },
  { path: "/forgot-password", kind: "public", label: "Forgot password" },
  { path: "/reset-password", kind: "public", label: "Reset password" },
  { path: "/verify-email", kind: "public", label: "Verify email" },
];

export type RouteRedirect = {
  source: string;
  destination: string;
  /** `config`: next.config redirects (run before pages). `page`: the page calls `redirect()`. */
  via: "config" | "page";
};

/** Page-level redirects: the route's own `page.tsx` calls `redirect()`. */
export const PAGE_REDIRECTS: readonly RouteRedirect[] = [
  { source: "/dashboard", destination: "/", via: "page" },
  { source: "/coaching", destination: "/agent", via: "page" },
  { source: "/journal/statistics", destination: "/", via: "page" },
];

export const ROUTE_REDIRECTS: readonly RouteRedirect[] = [
  ...PHASE_B_REDIRECTS.map((rule) => ({
    source: rule.source,
    destination: rule.destination,
    via: "config" as const,
  })),
  ...PAGE_REDIRECTS,
];

function segmentsOf(path: string): string[] {
  return path.split(/[?#]/)[0]!.split("/").filter(Boolean);
}

function isDynamic(segment: string): boolean {
  return /^\[.+\]$/.test(segment) || segment.startsWith(":");
}

/** The registered route a concrete path renders, ignoring query and hash. */
export function findRegisteredRoute(path: string): RegisteredRoute | null {
  const target = segmentsOf(path);
  const matches = ROUTE_REGISTRY.filter((route) => {
    const pattern = segmentsOf(route.path);
    return (
      pattern.length === target.length &&
      pattern.every((segment, index) => isDynamic(segment) || segment === target[index])
    );
  });
  // A static segment beats a dynamic one (`/decision/candidates` vs `[candidateId]`).
  matches.sort(
    (a, b) =>
      segmentsOf(a.path).filter(isDynamic).length - segmentsOf(b.path).filter(isDynamic).length,
  );
  return matches[0] ?? null;
}

function findRedirect(path: string): RouteRedirect | null {
  const target = segmentsOf(path);
  return (
    ROUTE_REDIRECTS.find((rule) => {
      const pattern = segmentsOf(rule.source);
      return (
        pattern.length === target.length &&
        pattern.every((segment, index) => isDynamic(segment) || segment === target[index])
      );
    }) ?? null
  );
}

/**
 * Follow redirects the way a browser would and return the page that renders,
 * or null for a 404 or a redirect loop. Config redirects win over pages.
 */
export function resolveRoute(path: string): RegisteredRoute | null {
  const seen = new Set<string>();
  let current = path;
  for (;;) {
    const key = segmentsOf(current).join("/");
    if (seen.has(key)) return null;
    seen.add(key);
    const redirect = findRedirect(current);
    if (redirect) {
      current = redirect.destination;
      continue;
    }
    const route = findRegisteredRoute(current);
    if (!route || route.kind === "redirect") return null;
    return route;
  }
}

export function routesOfKind(kind: RouteKind): RegisteredRoute[] {
  return ROUTE_REGISTRY.filter((route) => route.kind === kind);
}
