"use client";

import { usePathname } from "next/navigation";

import { SubNav } from "@/components/ui/sub-nav";
import { resolveSettingsSection, SETTINGS_SECTIONS } from "@/lib/navigation/settings-sections";

export default function SettingsLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const active = resolveSettingsSection(pathname);
  return (
    <div className="grid gap-4 lg:grid-cols-[11rem_minmax(0,1fr)] lg:gap-8">
      <SubNav
        aria-label="Settings sections"
        items={SETTINGS_SECTIONS}
        activeHref={active?.href ?? null}
        className="lg:sticky lg:top-4 lg:self-start"
      />
      <div className="min-w-0">{children}</div>
    </div>
  );
}
