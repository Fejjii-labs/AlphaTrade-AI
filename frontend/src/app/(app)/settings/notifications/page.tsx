"use client";

import { NotificationSettingsPanel } from "@/components/NotificationSettingsPanel";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { PageHeader } from "@/components/ui/page-header";
import { useWatcherMonitoring } from "@/hooks/useWatcherMonitoring";

export default function NotificationSettingsPage() {
  return (
    <SessionScoped>
      <NotificationSettings />
    </SessionScoped>
  );
}

function NotificationSettings() {
  const monitoring = useWatcherMonitoring();
  return (
    <div className="page-sections" data-testid="settings-notifications">
      <PageHeader title="Notifications" />
      <NotificationSettingsPanel snapshot={monitoring.loading ? null : monitoring.data} />
    </div>
  );
}
