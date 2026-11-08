"use client";

import { BloFinSyncPanel } from "@/components/BloFinSyncPanel";
import { ExchangeDemoAccountSync } from "@/components/exchange/ExchangeDemoAccountSync";
import { ExchangeDiagnosticsCard } from "@/components/ExchangeDiagnosticsCard";
import { ErrorState, LoadingState } from "@/components/states";
import { PageHeader } from "@/components/ui/page-header";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";

export default function ExchangeDiagnosticsPage() {
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
      <PageHeader
        eyebrow="Settings"
        title="Exchange status"
        description={
          <>
            BloFin demo readiness, leverage, venue positions and mirror health. Read-only: exchange
            credentials are provisioned by the operator in the server environment, and there is no key
            form in the app.
          </>
        }
      />
      <p className="text-xs text-text-muted" data-testid="exchange-credentials-note">
        If the demo account shows as not configured, ask the operator to set the BloFin demo
        credentials for this deployment. Real-money exchange access is never enabled here.
      </p>
      <ExchangeDiagnosticsCard diagnostics={data} />
      <ExchangeDemoAccountSync />
      <BloFinSyncPanel />
    </div>
  );
}
