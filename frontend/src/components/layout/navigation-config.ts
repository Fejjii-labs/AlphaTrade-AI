import type { LucideIcon } from "lucide-react";
import { BookOpen, Bot, Layers, LayoutDashboard, Settings } from "lucide-react";

import {
  ADVANCED_ROUTES,
  isAdvancedPath,
  matchesAnyPrefix,
  resolveAdvancedRoute,
  SETTINGS_ROUTE_PREFIXES,
} from "@/components/layout/advanced-routes";

export type DestinationId =
  "dashboard" | "agent" | "journal" | "strategies" | "settings";

export type NavLink = {
  href: string;
  label: string;
  icon: LucideIcon;
  advanced?: boolean;
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

/** Five trader destinations. Specialized routes stay contextual or under Settings / Advanced. */
export const PRIMARY_DESTINATIONS: readonly PrimaryDestination[] = [
  {
    id: "dashboard",
    label: "Dashboard",
    href: "/",
    icon: LayoutDashboard,
    ariaLabel: "Dashboard",
  },
  {
    id: "agent",
    label: "Agent",
    href: "/agent",
    icon: Bot,
    ariaLabel: "Agent",
  },
  {
    id: "journal",
    label: "Journal & Knowledge",
    href: "/journal",
    icon: BookOpen,
    ariaLabel: "Journal & Knowledge",
  },
  {
    id: "strategies",
    label: "Strategies",
    href: "/strategies",
    icon: Layers,
    ariaLabel: "Strategies",
  },
  {
    id: "settings",
    label: "Settings",
    href: "/settings",
    icon: Settings,
    ariaLabel: "Settings",
  },
] as const;

/** Phone tab bar shows every primary destination. Advanced work stays inside Settings. */
export const MOBILE_BOTTOM_DESTINATION_IDS: readonly DestinationId[] = [
  "dashboard",
  "agent",
  "journal",
  "strategies",
  "settings",
] as const;

const JOURNAL_PREFIXES = [
  "/journal",
  "/learning-analytics",
  "/coaching",
  "/knowledge",
  "/lessons",
] as const;
const STRATEGY_PREFIXES = ["/strategies", "/strategy-lab", "/risk"] as const;
const AGENT_PREFIXES = ["/agent"] as const;

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
    id: "strategies",
    match: (pathname) => matchesAnyPrefix(pathname, STRATEGY_PREFIXES),
  },
  {
    id: "agent",
    match: (pathname) => matchesAnyPrefix(pathname, AGENT_PREFIXES),
  },
  {
    id: "settings",
    match: (pathname) =>
      pathname === "/settings/advanced" ||
      matchesAnyPrefix(pathname, SETTINGS_ROUTE_PREFIXES) ||
      pathname.startsWith("/execution/manual-demo/") ||
      isAdvancedPath(pathname),
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
  const found = PRIMARY_DESTINATIONS.find(
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
  if (href === "/settings/advanced") {
    return (
      pathname === href ||
      (pathname !== "/settings" && isAdvancedPath(pathname))
    );
  }
  if (href === "/risk")
    return pathname === "/risk" || pathname.startsWith("/risk/");
  if (href === "/alerts")
    return pathname === "/alerts" || pathname.startsWith("/alerts/");
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
  const labeled = resolveAdvancedRoute(pathname);
  if (labeled && labeled.href !== primary.href) {
    return {
      primaryLabel: primary.label,
      secondaryLabel: labeled.label,
      title: primary.label,
      subtitle: labeled.label,
    };
  }

  if (pathname === "/settings/advanced") {
    return {
      primaryLabel: primary.label,
      secondaryLabel: "Advanced",
      title: primary.label,
      subtitle: "Advanced",
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
  const hrefs = new Set<string>(["/settings/advanced", "/knowledge"]);
  for (const destination of PRIMARY_DESTINATIONS) {
    hrefs.add(destination.href);
  }
  for (const group of SECONDARY_NAV) {
    for (const item of group.items) {
      hrefs.add(item.href);
    }
  }
  for (const route of ADVANCED_ROUTES) {
    hrefs.add(route.href);
  }
  return [...hrefs].sort();
}
