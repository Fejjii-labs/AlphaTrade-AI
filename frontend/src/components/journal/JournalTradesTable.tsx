import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { DataNumber } from "@/components/ui/data-number";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { SectionHeader } from "@/components/ui/section-header";
import type { CanonicalJournalTradeListItem } from "@/lib/api/types";
import { formatDateTime, formatMonetary, UNAVAILABLE } from "@/lib/format";

type Trade = CanonicalJournalTradeListItem;

function sideLabel(direction: string): string {
  return direction ? direction.charAt(0).toUpperCase() + direction.slice(1).toLowerCase() : UNAVAILABLE;
}

function pnlTone(value: string | null | undefined): "positive" | "negative" | "default" {
  const numeric = value == null || value === "" ? NaN : Number(value);
  if (!Number.isFinite(numeric) || numeric === 0) return "default";
  return numeric > 0 ? "positive" : "negative";
}

function columns(onOpen: () => void): Array<DataTableColumn<Trade>> {
  return [
    {
      id: "trade",
      header: "Trade",
      className: "min-w-36",
      cell: (t) => (
        <span className="flex flex-col">
          <span className="font-medium">{t.symbol}</span>
          <span className="text-caption text-text-muted">
            {[t.strategy_label ?? "No strategy", t.timeframe].filter(Boolean).join(" · ")}
          </span>
        </span>
      ),
    },
    { id: "side", header: "Side", cell: (t) => sideLabel(t.direction) },
    {
      id: "status",
      header: "Status",
      cell: (t) => <Badge variant={t.status === "closed" ? "muted" : "default"}>{t.status}</Badge>,
    },
    {
      id: "source",
      header: "Source",
      cell: (t) =>
        t.source === "manual_demo_test" ? (
          <Badge variant="info" data-testid="connectivity-test-marker">Connectivity test</Badge>
        ) : (
          <span className="text-text-secondary">Execution</span>
        ),
    },
    {
      id: "entry",
      header: "Entry",
      className: "whitespace-nowrap",
      cell: (t) => <span className="font-data">{formatDateTime(t.entry_time)}</span>,
    },
    {
      id: "exit",
      header: "Exit",
      className: "whitespace-nowrap",
      cell: (t) => <span className="font-data">{formatDateTime(t.exit_time)}</span>,
    },
    {
      id: "pnl",
      header: "Net P&L",
      numeric: true,
      cell: (t) =>
        t.net_pnl == null || t.net_pnl === "" ? (
          <DataNumber value={UNAVAILABLE} tone="muted" />
        ) : (
          <DataNumber value={formatMonetary(t.net_pnl)} tone={pnlTone(t.net_pnl)} data-testid="trade-net-pnl" />
        ),
    },
    {
      id: "open",
      header: "Review",
      className: "w-px whitespace-nowrap",
      cell: (t) => (
        <Link
          onClick={onOpen}
          href={`/journal?trade_id=${encodeURIComponent(t.id)}`}
          className="inline-flex min-h-11 min-w-11 items-center rounded-control px-2 text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          Open{" "}<span className="sr-only">exact trade</span>
        </Link>
      ),
    },
  ];
}

/** Recorded BloFin demo trades as a dense table; each row opens the exact trade review. */
export function JournalTradesTable({ trades, onOpen }: { trades: Trade[]; onOpen: () => void }) {
  return (
    <section aria-labelledby="journal-trades-heading" className="space-y-2" data-testid="journal-trades-table">
      <SectionHeader headingId="journal-trades-heading" title="Recorded trades" />
      <DataTable
        columns={columns(onOpen)}
        rows={trades}
        getRowId={(t) => t.id}
        caption="Recorded trades; open a row to review the exact trade"
        empty="No matching recorded trades."
      />
    </section>
  );
}
