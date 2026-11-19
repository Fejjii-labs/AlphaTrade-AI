import { pathMatchesPrefix } from "@/lib/navigation/route-registry";

export type SettingsSection = {
  href: string;
  label: string;
  /** Extra Settings routes that belong to this section. */
  includes?: readonly string[];
};

/** The four Settings tabs, in sub-nav order. */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { href: "/settings", label: "Account" },
  { href: "/settings/exchange", label: "Exchange" },
  { href: "/settings/alerts", label: "Alerts" },
  { href: "/settings/watcher", label: "Markets" },
];

/** The section a Settings path belongs to, or null outside Settings. */
export function resolveSettingsSection(pathname: string): SettingsSection | null {
  return (
    SETTINGS_SECTIONS.find(
      (section) =>
        (section.href === "/settings"
          ? pathname === "/settings"
          : pathMatchesPrefix(pathname, section.href)) ||
        (section.includes ?? []).some((path) => pathMatchesPrefix(pathname, path)),
    ) ?? null
  );
}

/** Old in-page anchors on `/settings`, forwarded to their own section. */
export const SETTINGS_HASH_FORWARDS: Readonly<Record<string, string>> = {
  notifications: "/settings/alerts",
  markets: "/settings/watcher",
};
