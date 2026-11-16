"use client";

import Link from "next/link";

import { EmptyState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { SectionHeader } from "@/components/ui/section-header";
import { cn } from "@/lib/utils";
import { useDemoAccountSnapshot } from "./useDemoAccountSnapshot";
import { TradingMetric } from "./TradingMetric";
import {
  formatCount,
  formatDateTime,
  formatMoney,
  formatPercent,
} from "@/lib/format";

const statusLabel = {
  ok: "Fresh snapshot",
  degraded: "Degraded snapshot",
  stale: "Stale snapshot",
  unavailable: "Account unavailable",
  not_synced: "No snapshot yet",
  inactive: "Not configured",
};

export function BloFinDemoAccountCard({
  refreshKey = 0,
}: {
  refreshKey?: number;
}) {
  const { account, busy, error, status, hasData, load } =
    useDemoAccountSnapshot(refreshKey);
  const usdt = hasData
    ? account?.balances.find((balance) => balance.asset === "USDT")
    : null;
  const performance = account?.performance;
  const activityStatus = performance?.activity_sync_status ?? "disabled";
  const activityDisabled = activityStatus === "disabled";
  const mirrorStatus = performance?.manual_mirror_status ?? "disabled";
  const mirrorReady = mirrorStatus === "ready";
  const winRate =
    performance?.win_rate == null
      ? null
      : formatPercent(Number(performance.win_rate));
  const hideHeadline = !mirrorReady && (activityDisabled || mirrorStatus === "withheld");
  const showWinRate =
    winRate != null &&
    (mirrorReady || (activityStatus === "ready" && mirrorStatus !== "withheld"));
  const realizedPnl = hideHeadline ? null : performance?.realized_pnl;
  const notYet = activityDisabled && !mirrorReady;
  const quietEmpty = status === "inactive" || status === "not_synced";
  const configureLink = (
    <Link
      href="/settings/exchange"
      className={cn(buttonVariants({ variant: "outline" }), "h-11")}
    >
      View exchange status
    </Link>
  );
  const refreshControl = account?.can_refresh ? (
    <Button
      variant="outline"
      className="h-11"
      disabled={busy}
      onClick={() => void load(true)}
    >
      {busy ? "Refreshing…" : "Refresh demo account"}
    </Button>
  ) : error ? (
    <Button
      variant="outline"
      className="h-11"
      disabled={busy}
      onClick={() => void load()}
    >
      Retry account
    </Button>
  ) : null;

  return (
    <Card data-testid="dashboard-demo-account">
      <CardHeader className="flex-row flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 flex-1 space-y-2">
          <SectionHeader eyebrow="Account" title="BloFin demo account" />
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="info">BLOFIN_DEMO</Badge>
            <Badge variant={status === "ok" ? "success" : "warning"}>
              {status
                ? statusLabel[status]
                : error
                  ? "Account unavailable"
                  : "Loading snapshot…"}
            </Badge>
          </div>
        </div>
        {!quietEmpty ? (
          <div className="flex flex-wrap items-center gap-2">
            {configureLink}
            {refreshControl}
          </div>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
        {quietEmpty ? (
          <EmptyState
            className="py-8"
            title={
              status === "inactive"
                ? "Demo account not connected"
                : "No snapshot yet"
            }
            description={
              account?.message ??
              "BloFin demo credentials are set by the operator. Once they are, equity, balances and positions appear here."
            }
            action={refreshControl ?? configureLink}
          />
        ) : (
        <>
        <div className="grid grid-cols-2 items-stretch gap-3 lg:grid-cols-5">
          <TradingMetric
            label="Account equity"
            value={`${formatMoney(hasData ? account?.total_equity_usd : null)} USD`}
            testId="dashboard-equity"
          />
          <TradingMetric
            label="Available balance"
            value={`${formatMoney(usdt?.available)} USDT`}
            testId="dashboard-available"
          />
          <TradingMetric
            label="Open positions"
            value={formatCount(hasData ? account?.position_count : null)}
            note={account?.positions_truncated ? "Incomplete list" : null}
            testId="dashboard-open-count"
          />
          <TradingMetric
            label="Win rate"
            value={showWinRate ? winRate : "—"}
            note={showWinRate ? null : notYet ? "Not available yet" : "Withheld"}
            testId="dashboard-win-rate"
          />
          <TradingMetric
            label="Realized PnL"
            value={`${formatMoney(realizedPnl)} USDT`}
            note={
              realizedPnl == null
                ? notYet
                  ? "Not available yet"
                  : "Withheld"
                : mirrorReady
                  ? null
                  : "Fees not included"
            }
            testId="dashboard-pnl"
          />
        </div>
        {account?.synced_at ? (
          <p
            data-testid="demo-last-synced"
            className={
              status === "stale"
                ? "text-xs text-warning"
                : "text-xs text-text-secondary"
            }
          >
            Last synced {formatDateTime(account.synced_at)}
          </p>
        ) : null}
        </>
        )}
      </CardContent>
    </Card>
  );
}
