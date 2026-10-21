"use client";

import { BloFinSyncPanel } from "@/components/BloFinSyncPanel";
import { ExchangeDemoAccountSync } from "@/components/exchange/ExchangeDemoAccountSync";
import { ExchangeDiagnosticsCard } from "@/components/ExchangeDiagnosticsCard";
import { ErrorState, LoadingState } from "@/components/states";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";

export default function ExchangeDiagnosticsPage() {
  const { data, loading, error, reload } = useAsyncData(
    () => api.exchange.diagnosticsSummary(),
    [],
  );

  if (loading) return <LoadingState label="Loading exchange diagnostics…" />;
  if (error) return <ErrorState message={error} onRetry={() => void reload()} />;
  if (!data) {
    return (
      <ErrorState message="Exchange diagnostics unavailable." onRetry={() => void reload()} />
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-50">Exchange diagnostics</h1>
        <p className="text-sm text-zinc-400">
          Operator view of BloFin demo readiness, leverage, venue positions, and mirror health.
        </p>
      </div>
      <ExchangeDiagnosticsCard diagnostics={data} />
      <ExchangeDemoAccountSync />
      <BloFinSyncPanel />
    </div>
  );
}
