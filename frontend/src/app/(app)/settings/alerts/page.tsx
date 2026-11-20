"use client";

import { AttentionCard } from "@/components/dashboard/AttentionCard";
import { NotificationSettingsPanel } from "@/components/NotificationSettingsPanel";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { PageHeader } from "@/components/ui/page-header";
import { useWatcherMonitoring } from "@/hooks/useWatcherMonitoring";

export default function AlertsSettingsPage() {
  return (
    <SessionScoped>
      <AlertsSettings />
    </SessionScoped>
  );
}

function AlertsSettings() {
  const monitoring = useWatcherMonitoring();
  return (
    <div className="page-sections" data-testid="settings-alerts">
      <PageHeader title="Alerts" />
      <NotificationSettingsPanel snapshot={monitoring.loading || monitoring.error ? null : monitoring.data} />
      <AttentionCard collapsible />
    </div>
  );
}
