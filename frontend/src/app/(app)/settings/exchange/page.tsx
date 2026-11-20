"use client";

import { ExchangeStatusCard } from "@/components/exchange/ExchangeStatusCard";
import { PageHeader } from "@/components/ui/page-header";

/** One card: BloFin demo status, last sync, Sync now, balances and positions. */
export default function ExchangeSettingsPage() {
  return (
    <div className="page-sections">
      <PageHeader title="Exchange" />
      <ExchangeStatusCard />
    </div>
  );
}
