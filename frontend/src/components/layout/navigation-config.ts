import type { LucideIcon } from "lucide-react";
import { BookOpen, Bot, Cpu, Layers, LayoutDashboard, Settings } from "lucide-react";

import {
  LABELED_ROUTES,
  matchesAnyPrefix,
  resolveLabeledRoute,
} from "@/lib/navigation/route-registry";

export type DestinationId =
  | "dashboard"
  | "journal"
  | "strategies"
  | "autonomous"
  | "agent"
  | "settings";

export type NavLink = {
  href: string;
  label: string;
  icon: LucideIcon;
};

export type PrimaryDestination = {
  id: DestinationId;
  label: string;
  href: string;
  icon: LucideIcon;
  /** Accessible name for landmark links */
  ariaLabel: string;
};

export type SecondaryNavGroup = {
  destinationId: DestinationId;
  items: readonly NavLink[];
};

/** The five trader pages, in sidebar order. Settings sits apart as a gear. */
export const PRIMARY_DESTINATIONS: readonly PrimaryDestination[] = [
  { id: "dashboard", label: "Dashboard", href: "/", icon: LayoutDashboard, ariaLabel: "Dashboard" },
  { id: "journal", label: "Trades", href: "/journal", icon: BookOpen, ariaLabel: "Trades" },
  { id: "strategies", label: "Strategies", href: "/strategies", icon: Layers, ariaLabel: "Strategies" },
  { id: "autonomous", label: "Bot", href: "/autonomous", icon: Cpu, ariaLabel: "Bot" },
  { id: "agent", label: "Agent", href: "/agent", icon: Bot, ariaLabel: "Agent" },
] as const;

/** Settings, shown as a gear at the bottom of the sidebar. */
export const SETTINGS_DESTINATION: PrimaryDestination = {
  id: "settings",
  label: "Settings",
  href: "/settings",
  icon: Settings,
  ariaLabel: "Settings",
};

/** Every destination: the five pages, then Settings. */
export const ALL_DESTINATIONS: readonly PrimaryDestination[] = [
  ...PRIMARY_DESTINATIONS,
  SETTINGS_DESTINATION,
];

/** Phone tab bar: four destinations, then More. */
export const MOBILE_BOTTOM_DESTINATION_IDS: readonly DestinationId[] = [
  "dashboard",
  "journal",
  "strategies",
  "autonomous",
] as const;

/** Destinations behind the phone More sheet. */
export const MOBILE_MORE_DESTINATION_IDS: readonly DestinationId[] = [
  "agent",
  "settings",
] as const;

const JOURNAL_PREFIXES = ["/journal"] as const;
const AUTONOMOUS_PREFIXES = ["/autonomous"] as const;
const STRATEGY_PREFIXES = ["/strategies"] as const;
// Knowledge is part of the Agent page.
const AGENT_PREFIXES = ["/agent", "/knowledge"] as const;
const SETTINGS_PREFIXES = ["/settings", "/execution/manual-demo"] as const;

export const SECONDARY_NAV: readonly SecondaryNavGroup[] = [
  {
    destinationId: "settings",
    items: [{ href: "/settings", label: "Back to Settings", icon: Settings }],
  },
] as const;

/** Ordered prefix rules; first match wins. Dashboard `/` is exact-only. */
const DESTINATION_MATCHERS: readonly {
  id: DestinationId;
  match: (pathname: string) => boolean;
}[] = [
  {
    id: "journal",
    match: (pathname) => matchesAnyPrefix(pathname, JOURNAL_PREFIXES),
  },
  {
    id: "autonomous",
    match: (pathname) => matchesAnyPrefix(pathname, AUTONOMOUS_PREFIXES),
  },
  {
    id: "strategies",
    match: (pathname) => matchesAnyPrefix(pathname, STRATEGY_PREFIXES),
  },
  {
    id: "agent",
    match: (pathname) => matchesAnyPrefix(pathname, AGENT_PREFIXES),
  },
  {
    id: "settings",
    match: (pathname) => matchesAnyPrefix(pathname, SETTINGS_PREFIXES),
  },
  { id: "dashboard", match: (pathname) => pathname === "/" },
];

export function getDestinationId(pathname: string): DestinationId | null {
  for (const rule of DESTINATION_MATCHERS) {
    if (rule.match(pathname)) return rule.id;
  }
  return null;
}

export function getPrimaryDestination(id: DestinationId): PrimaryDestination {
  const found = ALL_DESTINATIONS.find(
    (destination) => destination.id === id,
  );
  if (!found) {
    throw new Error(`Unknown destination: ${id}`);
  }
  return found;
}

export function getSecondaryItems(
  destinationId: DestinationId,
): readonly NavLink[] {
  return (
    SECONDARY_NAV.find((group) => group.destinationId === destinationId)
      ?.items ?? []
  );
}

export function isNavLinkActive(pathname: string, href: string): boolean {
  if (href === "/") return pathname === "/";
  if (href === "/settings")
    return (
      pathname === "/settings" || pathname.startsWith("/execution/manual-demo/")
    );
  if (href === "/journal") return pathname === "/journal";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Exactly one secondary link may be current: prefer the longest matching href.
 */
export function resolveSecondaryActiveHref(
  pathname: string,
  items: readonly NavLink[],
): string | null {
  const matches = items
    .filter((item) => isNavLinkActive(pathname, item.href))
    .sort((a, b) => b.href.length - a.href.length);
  return matches[0]?.href ?? null;
}

export function isPrimaryDestinationActive(
  pathname: string,
  destination: PrimaryDestination,
): boolean {
  return getDestinationId(pathname) === destination.id;
}

export type PageIdentity = {
  primaryLabel: string;
  secondaryLabel: string | null;
  title: string;
  subtitle: string | null;
};

/**
 * Route-aware page identity. Trader hubs use the primary label.
 * Retained operational routes keep their own subtitle under that hub.
 */
export function resolvePageIdentity(pathname: string): PageIdentity {
  const destinationId = getDestinationId(pathname);
  if (!destinationId) {
    return {
      primaryLabel: "AlphaTrade",
      secondaryLabel: null,
      title: "AlphaTrade",
      subtitle: null,
    };
  }

  const primary = getPrimaryDestination(destinationId);
  const labeled = resolveLabeledRoute(pathname);
  if (labeled && labeled.href !== primary.href) {
    return {
      primaryLabel: primary.label,
      secondaryLabel: labeled.label,
      title: primary.label,
      subtitle: labeled.label,
    };
  }

  const secondaryItems = getSecondaryItems(destinationId);
  const activeHref = resolveSecondaryActiveHref(pathname, secondaryItems);
  const secondary =
    secondaryItems.find((item) => item.href === activeHref) ?? null;
  if (
    !secondary ||
    (secondary.href === primary.href && pathname === primary.href)
  ) {
    return {
      primaryLabel: primary.label,
      secondaryLabel: null,
      title: primary.label,
      subtitle: null,
    };
  }

  return {
    primaryLabel: primary.label,
    secondaryLabel: secondary.label,
    title: primary.label,
    subtitle: secondary.label,
  };
}

/** Flat reachability map used by tests — every retained capability path. */
export function listReachableHrefs(): string[] {
  const hrefs = new Set<string>();
  for (const destination of ALL_DESTINATIONS) {
    hrefs.add(destination.href);
  }
  for (const group of SECONDARY_NAV) {
    for (const item of group.items) {
      hrefs.add(item.href);
    }
  }
  for (const route of LABELED_ROUTES) {
    hrefs.add(route.href);
  }
  return [...hrefs].sort();
}
