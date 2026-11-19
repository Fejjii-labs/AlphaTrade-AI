import Link from "next/link";

import { DataNumber } from "@/components/ui/data-number";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import type { CanonicalJournalTradeListItem } from "@/lib/api/types";
import { formatDateTime, formatMonetary, formatPrice, UNAVAILABLE } from "@/lib/format";

type Trade = CanonicalJournalTradeListItem;

const SOURCE_NAMES: Partial<Record<NonNullable<Trade["source"]>, string>> = {
  paper_execution: "Bot",
  blofin_demo_manual: "Mine",
  manual_demo_test: "App order",
};

export function tradeSourceName(source: Trade["source"]): string {
  return (source && SOURCE_NAMES[source]) || "Other";
}

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
      id: "date",
      header: "Date",
      className: "whitespace-nowrap",
      cell: (t) => <span className="font-data">{formatDateTime(t.exit_time ?? t.entry_time)}</span>,
    },
    {
      id: "trade",
      header: "Coin",
      className: "min-w-28",
      cell: (t) => (
        <span className="flex flex-col">
          <span className="font-medium">{t.symbol}</span>
          <span className="text-caption text-text-muted">{t.strategy_label ?? "No strategy"}</span>
        </span>
      ),
    },
    { id: "side", header: "Side", cell: (t) => sideLabel(t.direction) },
    { id: "source", header: "By", cell: (t) => tradeSourceName(t.source) },
    {
      id: "entry",
      header: "Entry",
      numeric: true,
      cell: (t) => <DataNumber value={formatPrice(t.entry_price)} />,
    },
    {
      id: "exit",
      header: "Exit",
      numeric: true,
      cell: (t) =>
        t.status === "closed" ? <DataNumber value={formatPrice(t.exit_price)} /> : <span className="text-text-muted">Open</span>,
    },
    {
      id: "fees",
      header: "Fees",
      numeric: true,
      cell: (t) => <DataNumber value={t.fees == null || t.fees === "" ? UNAVAILABLE : formatPrice(t.fees, { maximumFractionDigits: 2 })} tone="muted" data-testid="trade-fees" />,
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
      header: "Journal",
      className: "w-px whitespace-nowrap",
      cell: (t) => (
        <Link
          onClick={onOpen}
          href={`/journal?trade_id=${encodeURIComponent(t.id)}`}
          className="inline-flex min-h-11 min-w-11 items-center rounded-control px-2 text-accent hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
        >
          Journal<span className="sr-only"> {t.symbol} trade</span>
        </Link>
      ),
    },
  ];
}

/** BloFin demo trades (bot and mine); P&L and fees come from BloFin. Each row opens its journal page. */
export function JournalTradesTable({ trades, onOpen }: { trades: Trade[]; onOpen: () => void }) {
  return (
    <section aria-label="Trades" className="space-y-2" data-testid="journal-trades-table">
      <DataTable
        columns={columns(onOpen)}
        rows={trades}
        getRowId={(t) => t.id}
        caption="BloFin demo trades"
        empty="No trades yet."
      />
    </section>
  );
}
