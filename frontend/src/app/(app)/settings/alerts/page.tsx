"use client";

import { AttentionCard } from "@/components/dashboard/AttentionCard";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { PageHeader } from "@/components/ui/page-header";

export default function AlertsSettingsPage() {
  return (
    <SessionScoped>
      <div className="page-sections" data-testid="settings-alerts">
        <PageHeader title="Alerts" />
        <AttentionCard limit={Infinity} />
      </div>
    </SessionScoped>
  );
}
