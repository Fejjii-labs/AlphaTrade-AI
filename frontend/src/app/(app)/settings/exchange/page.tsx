"use client";

import { BloFinSyncPanel } from "@/components/BloFinSyncPanel";
import { ExchangeDemoAccountSync } from "@/components/exchange/ExchangeDemoAccountSync";
import { ExchangeDiagnosticsCard } from "@/components/ExchangeDiagnosticsCard";
import { ErrorState, LoadingState } from "@/components/states";
import { PageHeader } from "@/components/ui/page-header";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";

export default function ExchangeSettingsPage() {
  const { data, loading, error, reload } = useAsyncData(
    () => api.exchange.diagnosticsSummary(),
    [],
  );

  if (loading) return <LoadingState label="Loading exchange status…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!data) {
    return (
      <ErrorState message="Exchange status unavailable." onRetry={() => void reload()} />
    );
  }

  return (
    <div className="page-sections">
      <PageHeader title="Exchange" />
      <ExchangeDiagnosticsCard diagnostics={data} />
      <ExchangeDemoAccountSync />
      <BloFinSyncPanel />
    </div>
  );
}
