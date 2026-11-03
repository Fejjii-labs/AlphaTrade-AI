"use client";

import Link from "next/link";
import { useCallback } from "react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DataTable, type DataTableColumn } from "@/components/ui/data-table";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { StrategyStatsRow } from "@/lib/api/types";
import { formatCount, formatDateTime, formatMonetary, formatPercent } from "@/lib/format";

const COLUMNS: Array<DataTableColumn<StrategyStatsRow>> = [
  {
    id: "strategy",
    header: "Strategy",
    cell: (row) => (
      <span className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium">{row.name}</span>
        <Badge variant={row.enabled ? "success" : "muted"}>{row.enabled ? "Enabled" : "Paused"}</Badge>
        {row.bot_selected ? <Badge variant="paper">Bot target</Badge> : null}
      </span>
    ),
  },
  { id: "setup", header: "Last setup", cell: (row) => (row.last_setup_at ? formatDateTime(row.last_setup_at) : "None yet") },
  {
    id: "trade",
    header: "Last trade",
    cell: (row) => (row.stats.last_trade_at ? formatDateTime(row.stats.last_trade_at) : "None yet"),
  },
  { id: "closed", header: "Trades", numeric: true, cell: (row) => formatCount(row.stats.closed_trades) },
  { id: "win", header: "Win rate", numeric: true, cell: (row) => formatPercent(row.stats.win_rate) },
  { id: "pnl", header: "P&L", numeric: true, cell: (row) => (row.stats.closed_trades ? formatMonetary(row.stats.net_pnl) : "—") },
  { id: "open", header: "Open", numeric: true, cell: (row) => formatCount(row.stats.open_positions) },
];

/** Per strategy: whether it runs, its last setup and trade, and journal trade stats. */
export function StrategyStatsPanel() {
  const loader = useCallback(() => api.autonomous.strategyStats(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const unattributed = data?.unattributed;
  return (
    <Card id="strategies" data-testid="strategy-stats" className="scroll-mt-4">
      <CardHeader className="flex flex-row items-baseline justify-between gap-2">
        <CardTitle>Per strategy</CardTitle>
        <Button variant="outline" size="sm" disabled={loading} onClick={() => void reload()}>
          Refresh
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {error ? (
          <p role="status" className="text-text-muted">
            Strategy stats unavailable.
          </p>
        ) : (
          <DataTable
            columns={COLUMNS}
            rows={data?.strategies ?? []}
            getRowId={(row) => row.strategy_id}
            caption="Trades and stats per strategy"
            loading={loading}
            empty={
              <>
                No strategies yet. <Link className="text-accent" href="/strategies">Set one up</Link>.
              </>
            }
          />
        )}
        <p className="text-xs text-text-muted" data-testid="strategy-stats-note">
          Closed journal trades (manual and bot; backtests excluded). Win rate is wins ÷ closed trades.
          {unattributed && (unattributed.closed_trades > 0 || unattributed.open_positions > 0)
            ? ` Trades without a strategy: ${unattributed.closed_trades} closed, ${unattributed.open_positions} open, P&L ${formatMonetary(unattributed.net_pnl)}.`
            : ""}
        </p>
      </CardContent>
    </Card>
  );
}
