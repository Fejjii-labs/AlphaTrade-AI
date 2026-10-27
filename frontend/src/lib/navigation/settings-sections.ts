import { pathMatchesPrefix } from "@/components/layout/advanced-routes";

export type SettingsSection = {
  href: string;
  label: string;
  /** Extra Settings routes that belong to this section. */
  includes?: readonly string[];
};

/** The six Settings sections, in sub-nav order (UI overhaul B2a). */
export const SETTINGS_SECTIONS: readonly SettingsSection[] = [
  { href: "/settings", label: "Account", includes: ["/settings/team", "/settings/billing"] },
  { href: "/settings/exchange", label: "Exchanges" },
  { href: "/settings/notifications", label: "Notifications" },
  { href: "/settings/watcher", label: "Watcher" },
  { href: "/settings/advanced", label: "Diagnostics", includes: ["/settings/audit"] },
  { href: "/settings/help", label: "Help" },
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
  notifications: "/settings/notifications",
  markets: "/settings/watcher",
};
