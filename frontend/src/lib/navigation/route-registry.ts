import { PHASE_B_REDIRECTS } from "@/lib/navigation/phase-b-redirects";

/**
 * Every app route, classified once (UI overhaul B1, slimmed 2026-11-19).
 *
 * - primary: a sidebar destination.
 * - section: a page inside a primary destination (Settings tabs, Knowledge).
 * - deep-link: a detail page opened from a list, an alert or Telegram.
 * - public: signed-out pages.
 *
 * Removed routes have no page file; their config redirects live in
 * `phase-b-redirects.ts`.
 *
 * `path` uses the App Router folder form (`[id]` for dynamic segments, route
 * groups dropped). Tests check that this list matches the `page.tsx` files.
 */
export type RouteKind = "primary" | "section" | "deep-link" | "public";

export type RegisteredRoute = {
  path: string;
  kind: RouteKind;
  label: string;
};

export const ROUTE_REGISTRY: readonly RegisteredRoute[] = [
  { path: "/", kind: "primary", label: "Dashboard" },
  { path: "/agent", kind: "primary", label: "Agent" },
  { path: "/journal", kind: "primary", label: "Trades" },
  { path: "/strategies", kind: "primary", label: "Strategies" },
  { path: "/settings", kind: "primary", label: "Settings" },
  { path: "/autonomous", kind: "primary", label: "Bot" },

  // Knowledge belongs to the Agent page.
  { path: "/knowledge", kind: "section", label: "Knowledge" },
  { path: "/settings/exchange", kind: "section", label: "Exchange" },
  { path: "/settings/alerts", kind: "section", label: "Alerts" },
  { path: "/settings/watcher", kind: "section", label: "Markets" },

  { path: "/strategies/setups/[id]", kind: "deep-link", label: "Setup" },
  { path: "/execution/manual-demo/[commandId]", kind: "deep-link", label: "Manual demo order" },

  { path: "/login", kind: "public", label: "Sign in" },
  { path: "/register", kind: "public", label: "Register" },
  { path: "/forgot-password", kind: "public", label: "Forgot password" },
  { path: "/reset-password", kind: "public", label: "Reset password" },
  { path: "/verify-email", kind: "public", label: "Verify email" },
];

export type RouteRedirect = {
  source: string;
  destination: string;
  /** Redirect only when the path carries this query. */
  has?: { key: string; value?: string }[];
};

/** next.config redirects; they run before pages. */
export const ROUTE_REDIRECTS: readonly RouteRedirect[] = PHASE_B_REDIRECTS.map((rule) => ({
  source: rule.source,
  destination: rule.destination,
  ...(rule.has ? { has: rule.has.map(({ key, value }) => ({ key, value })) } : {}),
}));

export function pathMatchesPrefix(pathname: string, prefix: string): boolean {
  return pathname === prefix || pathname.startsWith(`${prefix}/`);
}

export function matchesAnyPrefix(pathname: string, prefixes: readonly string[]): boolean {
  return prefixes.some((prefix) => pathMatchesPrefix(pathname, prefix));
}

export type LabeledRoute = { href: string; label: string };

/** Static section pages, for page subtitles. */
export const LABELED_ROUTES: readonly LabeledRoute[] = routesOfKind("section").map((route) => ({
  href: route.path,
  label: route.label,
}));

/** Longest labeled section route for a pathname. */
export function resolveLabeledRoute(pathname: string): LabeledRoute | null {
  const matches = LABELED_ROUTES.filter((route) => pathMatchesPrefix(pathname, route.href)).sort(
    (a, b) => b.href.length - a.href.length,
  );
  return matches[0] ?? null;
}

function segmentsOf(path: string): string[] {
  return path.split(/[?#]/)[0]!.split("/").filter(Boolean);
}

function isDynamic(segment: string): boolean {
  return /^\[.+\]$/.test(segment) || segment.startsWith(":");
}

function isCatchAll(segment: string | undefined): boolean {
  return segment != null && segment.startsWith(":") && segment.endsWith("*");
}

function segmentsMatch(pattern: string[], target: string[]): boolean {
  if (isCatchAll(pattern.at(-1))) {
    const fixed = pattern.slice(0, -1);
    return target.length >= fixed.length && fixed.every((segment, index) => isDynamic(segment) || segment === target[index]);
  }
  return (
    pattern.length === target.length &&
    pattern.every((segment, index) => isDynamic(segment) || segment === target[index])
  );
}

/** The registered route a concrete path renders, ignoring query and hash. */
export function findRegisteredRoute(path: string): RegisteredRoute | null {
  const target = segmentsOf(path);
  const matches = ROUTE_REGISTRY.filter((route) => segmentsMatch(segmentsOf(route.path), target));
  // A static segment beats a dynamic one.
  matches.sort(
    (a, b) =>
      segmentsOf(a.path).filter(isDynamic).length - segmentsOf(b.path).filter(isDynamic).length,
  );
  return matches[0] ?? null;
}

function findRedirect(path: string): RouteRedirect | null {
  const target = segmentsOf(path);
  const query = new URLSearchParams(path.split("#")[0]!.split("?")[1] ?? "");
  return (
    ROUTE_REDIRECTS.find(
      (rule) =>
        segmentsMatch(segmentsOf(rule.source), target) &&
        (rule.has ?? []).every(({ key, value }) =>
          value === undefined ? query.has(key) : query.get(key) === value,
        ),
    ) ?? null
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
    return findRegisteredRoute(current);
  }
}

export function routesOfKind(kind: RouteKind): RegisteredRoute[] {
  return ROUTE_REGISTRY.filter((route) => route.kind === kind);
}
