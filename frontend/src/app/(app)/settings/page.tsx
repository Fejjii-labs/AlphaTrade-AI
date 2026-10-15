"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { EmailVerificationNotice } from "@/components/account/EmailVerificationNotice";
import { WatcherWatchlistSection } from "@/components/WatcherWatchlistSection";
import { WatcherAssignmentsSection } from "@/components/WatcherAssignmentsSection";
import { NotificationSettingsPanel } from "@/components/NotificationSettingsPanel";
import { PaperAccountSetup } from "@/components/settings/PaperAccountSetup";
import { SettingsReadout } from "@/components/settings/SettingsReadout";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";
import { useAppContext } from "@/contexts/AppContext";
import { useWatcherMonitoring } from "@/hooks/useWatcherMonitoring";
import { formatDateTime } from "@/lib/format";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";

export default function SettingsPage() {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  if (!user || !organization) return null;
  return (
    <SettingsWorkspace key={JSON.stringify([organization.id, user.id, session])} />
  );
}

function SettingsWorkspace() {
  const { user, organization } = useAuth();
  const { health, providers, refreshStatus, loading } = useAppContext();
  const monitoring = useWatcherMonitoring();
  useEffect(() => {
    const openAnchor = () => {
      const id = window.location.hash.slice(1);
      if (!["markets", "notifications", "account-system"].includes(id)) return;
      const target = document.getElementById(id);
      if (target instanceof HTMLDetailsElement) target.open = true;
    };
    openAnchor();
    window.addEventListener("hashchange", openAnchor);
    return () => window.removeEventListener("hashchange", openAnchor);
  }, []);
  return (
    <div className="space-y-4 [&_input:not([type=checkbox])]:text-base [&_select]:text-base lg:[&_input:not([type=checkbox])]:text-sm lg:[&_select]:text-sm" data-testid="settings-workspace">
      <h1 className="text-2xl font-semibold">Settings</h1>
      <details
        className="rounded-card border border-border-subtle bg-surface-1 p-4"
        id="markets"
      >
        <summary className="min-h-11 cursor-pointer text-lg font-semibold">
          Market Monitoring
        </summary>
        <div className="space-y-4 pt-4">
          <WatcherWatchlistSection />
          <WatcherAssignmentsSection />
          <SettingsReadout
            rows={[
              [
                "Watcher",
                monitoring.loading
                  ? "Refreshing…"
                  : monitoring.error
                    ? "Unavailable"
                    : (monitoring.data?.paper_monitoring_status ??
                      "Unavailable"),
              ],
              [
                "Last scan",
                monitoring.data?.last_scan_at
                  ? formatDateTime(monitoring.data.last_scan_at)
                  : "Unavailable",
              ],
            ]}
          />
          <Button
            variant="outline"
            disabled={monitoring.loading}
            onClick={() => void monitoring.reload()}
          >
            Refresh monitoring
          </Button>
        </div>
      </details>
      <details
        className="rounded-card border border-border-subtle bg-surface-1 p-4"
        id="notifications"
      >
        <summary className="min-h-11 cursor-pointer text-lg font-semibold">
          Notifications
        </summary>
        <div className="pt-4">
          <NotificationSettingsPanel
            snapshot={monitoring.loading ? null : monitoring.data}
          />
        </div>
      </details>
      <details
        className="rounded-card border border-border-subtle bg-surface-1 p-4"
        id="account-system"
      >
        <summary className="min-h-11 cursor-pointer text-lg font-semibold">
          Account &amp; System
        </summary>
        <div className="space-y-4 pt-4">
          <SettingsReadout
            rows={[
              ["Profile", user?.email ?? "Unavailable"],
              ["Workspace", organization?.name ?? "Unavailable"],
              ["System", health?.status ?? "Unavailable"],
            ]}
          />
          <EmailVerificationNotice />
          <PaperAccountSetup key={`${organization?.id}:${user?.id}`} />
          <div className="flex flex-wrap gap-4 text-sm text-accent">
            <Link href="/settings/exchange">Exchange connections</Link>
            <Link href="/settings/team">Team</Link>
            <Link href="/settings/billing">Billing &amp; Usage</Link>
            <Link href="/settings/advanced">Diagnostics</Link>
            <Link href="/settings/help">Help &amp; Guide</Link>
          </div>
          <details>
            <summary className="min-h-11 cursor-pointer text-sm">
              Provider details
            </summary>
            {providers?.providers.map((p) => (
              <p className="text-sm" key={`${p.kind}:${p.name}`}>
                {p.name}: {p.health}
                {p.is_mock ? " · Simulated" : ""}
                {p.using_fallback ? " · Fallback" : ""}
              </p>
            )) ?? <p>Unavailable</p>}
          </details>
          <Button
            variant="outline"
            disabled={loading}
            onClick={() => {
              void refreshStatus();
              void monitoring.reload();
            }}
          >
            Refresh system status
          </Button>
        </div>
      </details>
    </div>
  );
}
