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
  formatPrice,
  formatQuantity,
} from "@/lib/format";
import { DEMO_REFRESH_INTERVAL_MS } from "./demoAccountRefresh";

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
  const verifiedPerformance = performance?.status === "partial";
  const activityStatus = performance?.activity_sync_status ?? "disabled";
  const activityDisabled = activityStatus === "disabled";
  const mirrorStatus = performance?.manual_mirror_status ?? "disabled";
  const mirrorReady = mirrorStatus === "ready";
  const activityCoverage = performance?.activity_coverage ?? "";
  const activityCoverageWithheld =
    /win rate and realized p&l are withheld/i.test(activityCoverage);
  const intervalSeconds =
    account?.refresh_interval_seconds ?? DEMO_REFRESH_INTERVAL_MS / 1000;
  const winRate =
    performance?.win_rate == null
      ? null
      : formatPercent(Number(performance.win_rate));
  const hideHeadline = !mirrorReady && (activityDisabled || mirrorStatus === "withheld");
  const showWinRate =
    winRate != null &&
    (mirrorReady || (activityStatus === "ready" && mirrorStatus !== "withheld"));
  const quietEmpty = status === "inactive" || status === "not_synced";
  const configureLink = (
    <Link
      href="/settings/exchange"
      className={cn(buttonVariants({ variant: "outline" }), "h-11")}
    >
      Exchange settings
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
              "Configure BloFin demo sync to see equity, balances, and positions."
            }
            action={refreshControl ?? configureLink}
          />
        ) : (
        <>
        <details>
          <summary className="flex h-11 cursor-pointer items-center text-sm">
            Snapshot scope &amp; refresh
          </summary>
          <div className="space-y-2 pt-2 text-xs text-text-secondary">
            <p>
              Configured demo venue account · balances and native open positions.
              {account?.can_refresh
                ? ` Native data refreshes when this page opens, then every ${intervalSeconds} seconds while it is visible.`
                : ` Saved snapshots reload every ${intervalSeconds} seconds while it is visible.`}
              All balances and positions below use this configured account.
            </p>
            <p>
              USD equity and USDT balances use different valuation units. Conversion
              differences are not trading PnL.
            </p>
            <p>
              Account refresh reads balances and positions. Order fills, SL/TP
              verification and Journal evidence are reviewed separately.
            </p>
          </div>
        </details>
        <div className="grid grid-cols-2 items-stretch gap-3 lg:grid-cols-5">
          <TradingMetric
            label="Account equity"
            value={`${formatMoney(hasData ? account?.total_equity_usd : null)} USD`}
            note="Native account valuation · USD"
            testId="dashboard-equity"
          />
          <TradingMetric
            label="Available balance"
            value={`${formatMoney(usdt?.available)} USDT`}
            note="Available settlement asset"
            testId="dashboard-available"
          />
          <TradingMetric
            label="Open positions"
            value={formatCount(hasData ? account?.position_count : null)}
            note={
              account?.positions_truncated
                ? "Incomplete native position coverage"
                : "Native account snapshot"
            }
            testId="dashboard-open-count"
          />
          <TradingMetric
            label="Win rate"
            value={showWinRate ? winRate : "—"}
            note={
              mirrorReady
                ? "Closed mirrored BloFin demo trades"
                : mirrorStatus === "withheld"
                  ? "Withheld until positions-history price, fee, and funding match"
                  : activityDisabled
                    ? "Not available yet"
                    : activityStatus === "ready"
                      ? "Closed round-trips from stored fills"
                      : "Withheld until fee basis and a flat start are verified"
            }
            testId="dashboard-win-rate"
          />
          <TradingMetric
            label="Realized PnL"
            value={`${formatMoney(hideHeadline ? null : performance?.realized_pnl)} USDT`}
            note={
              mirrorReady
                ? "Positions-history price P&L minus fee and funding. fillPnl is not used"
                : mirrorStatus === "withheld"
                  ? "Withheld. A mirrored trade does not match the price formula"
                  : activityDisabled
                    ? "Not available yet"
                    : performance?.realized_pnl == null
                      ? "Withheld. fillPnl fee basis and fee sign are not verified"
                      : "Stored fillPnl. Fees are not added"
            }
            testId="dashboard-pnl"
          />
        </div>
        <details
          aria-label="Verified account performance"
          className="space-y-2 rounded-control border border-border-subtle p-3"
        >
          <summary className="min-h-11 cursor-pointer text-sm font-medium">
            Performance coverage · USDT
          </summary>
          <p className="text-xs text-text-secondary">
            {performance?.coverage ??
              "Performance unavailable. Account snapshots do not establish realized returns; complete linked trade history is required."}
          </p>
          {verifiedPerformance ? (
            <>
              <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4 text-sm">
                {(
                  [
                    ["Gross trading PnL", performance.gross_pnl],
                    ["Fees", performance.fees],
                    ["Funding", performance.funding],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label}>
                    <dt className="text-xs text-text-secondary">{label}</dt>
                    <dd className="mt-1 tabular-nums">
                      {formatMoney(value)} USDT
                    </dd>
                  </div>
                ))}
                <div>
                  <dt className="text-xs text-text-secondary">
                    Verified closures
                  </dt>
                  <dd className="mt-1">
                    {formatCount(performance.verified_closed_trades)}
                  </dd>
                </div>
              </dl>
              <p className="text-xs text-text-secondary">
                {performance.unresolved_trades} unresolved trades ·{" "}
                {performance.manual_test_trades} manual connectivity tests
                included in account activity.
              </p>
              <p className="text-xs text-text-secondary">
                Strategy performance unavailable for this coverage. Manual
                connectivity tests are excluded. Win rate and expectancy
                unavailable for this incomplete history.
              </p>
            </>
          ) : null}
        </details>
        {busy ? (
          <p role="status" className="text-sm text-text-secondary">
            Loading demo account snapshot…
            {account ? " Showing the previous saved snapshot." : ""}
          </p>
        ) : null}
        {account ? (
          <>
            <p
              role="status"
              className={
                status === "stale"
                  ? "text-sm text-warning"
                  : "text-sm text-text-secondary"
              }
            >
              {status === "stale"
                ? "Saved demo account snapshot is stale. Refresh for current balances and positions."
                : account.message}
            </p>
            {account.synced_at ? (
              <p
                data-testid="demo-last-synced"
                className={
                  status === "stale"
                    ? "text-sm text-warning"
                    : "text-xs text-text-secondary"
                }
              >
                Last synced {formatDateTime(account.synced_at)}
                {status === "stale" ? " · stale" : ""}
              </p>
            ) : null}
            {mirrorReady ? (
              <p role="status" data-testid="manual-mirror-ready" className="text-sm text-text-secondary">
                {performance?.manual_mirror_reason}
                {performance?.trade_count != null
                  ? ` ${formatCount(performance.trade_count)} closed trades.`
                  : ""}
                {performance?.average_r != null ? ` Average R ${performance.average_r}.` : " Average R —."}
              </p>
            ) : null}
            {mirrorStatus === "withheld" ? (
              <p role="status" data-testid="manual-mirror-withheld" className="text-sm text-warning">
                {performance?.manual_mirror_reason}
              </p>
            ) : null}
            {activityDisabled && !mirrorReady ? (
              <p role="status" data-testid="activity-sync-disabled" className="text-sm text-warning">
                Activity sync disabled. Win rate and realized P&L are withheld
                until the activity worker is enabled.
              </p>
            ) : activityCoverage && !(mirrorReady && (activityDisabled || activityCoverageWithheld)) ? (
              <p className="text-xs text-text-secondary" data-testid="activity-coverage">
                {activityCoverage}
                {!mirrorReady && performance?.trade_count != null
                  ? ` ${formatCount(performance.trade_count)} closed trades · ${formatCount(performance.wins)} wins · ${formatCount(performance.losses)} losses.`
                  : ""}
                {!mirrorReady && performance?.window_start
                  ? ` Window ${formatDateTime(performance.window_start)} – ${formatDateTime(performance.window_end)}.`
                  : ""}
              </p>
            ) : null}
            {account.last_attempt_at ? (
              <p className="text-xs text-text-secondary">
                Last refresh attempt {formatDateTime(account.last_attempt_at)}
              </p>
            ) : null}
            {!account.can_refresh && account.status !== "inactive" ? (
              <p className="text-xs text-text-secondary">
                An organization owner can refresh the demo account.
              </p>
            ) : null}
            {hasData ? (
              <>
                <details>
                  <summary className="min-h-11 cursor-pointer text-sm">
                    Balances by currency
                  </summary>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {account.balances.map((balance) => (
                      <dl
                        key={balance.asset}
                        className="rounded-control border border-border-subtle p-3 text-sm"
                      >
                        <dt className="font-medium text-text-primary">
                          {balance.asset} balance
                        </dt>
                        <dd className="mt-2 break-words text-text-secondary">
                          Cash balance: {formatMoney(balance.total)}{" "}
                          {balance.asset}
                        </dd>
                        <dd className="break-words text-text-secondary">
                          Available: {formatMoney(balance.available)}{" "}
                          {balance.asset}
                        </dd>
                        <dd className="break-words text-text-secondary">
                          Equity: {formatMoney(balance.equity)} {balance.asset}
                        </dd>
                      </dl>
                    ))}
                  </div>
                </details>
                {!account.balances.length ? (
                  <p className="text-sm text-text-secondary">
                    No balances reported.
                  </p>
                ) : null}
                {account.balances_truncated ? (
                  <p className="text-xs text-warning">
                    Balance list is incomplete.
                  </p>
                ) : null}
                <div className="space-y-2">
                  <h3 className="text-sm font-medium text-text-primary">
                    Native open positions ·{" "}
                    {account.positions_truncated
                      ? `at least ${account.positions.length}`
                      : formatCount(account.position_count)}
                  </h3>
                  {account.positions.map((position, index) => (
                    <div
                      key={`${position.symbol}-${position.side}-${index}`}
                      className="rounded-control border border-border-subtle p-3 text-sm"
                    >
                      <p className="font-medium text-text-primary">
                        {position.symbol} · {position.side}
                      </p>
                      <p className="mt-1 text-text-secondary">
                        {formatQuantity(position.contracts, {
                          maximumFractionDigits: 8,
                        })}{" "}
                        contracts · Leverage {position.leverage ?? "—"}x
                      </p>
                      <p className="break-words text-text-secondary">
                        Base quantity:{" "}
                        {formatQuantity(position.base_quantity, {
                          maximumFractionDigits: 8,
                        })}{" "}
                        {position.base_asset ??
                          "(unverified instrument metadata)"}
                      </p>
                      <p className="text-text-secondary">
                        Entry {formatPrice(position.entry_price)} · Mark{" "}
                        {formatPrice(position.mark_price)}{" "}
                        {position.quote_asset ?? "(currency unverified)"}
                      </p>
                      <p className="break-words text-text-secondary">
                        Unrealized PnL {formatMoney(position.unrealized_pnl)}{" "}
                        {position.quote_asset ?? "(currency unverified)"}
                      </p>
                    </div>
                  ))}
                  {!account.positions.length && !account.positions_truncated ? (
                    <p className="text-sm text-text-secondary">
                      No native open positions at this snapshot.
                    </p>
                  ) : null}
                  {account.positions_truncated ? (
                    <p className="text-xs text-warning">
                      Position list is incomplete; a full account count is
                      unavailable.
                    </p>
                  ) : null}
                </div>
              </>
            ) : null}
          </>
        ) : null}
        </>
        )}
      </CardContent>
    </Card>
  );
}
