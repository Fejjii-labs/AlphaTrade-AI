"use client";

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { SectionHeader } from "@/components/ui/section-header";
import { useDemoAccountSnapshot } from "./useDemoAccountSnapshot";
import { TradingMetric } from "./TradingMetric";
import {
  formatCount,
  formatDateTime,
  formatMoney,
  formatPrice,
  formatQuantity,
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
  const verifiedPerformance = performance?.status === "partial";

  return (
    <Card data-testid="dashboard-demo-account">
      <CardHeader className="flex-row flex-wrap items-start justify-between gap-3">
        <div className="card-inner min-w-0 flex-1">
          <SectionHeader eyebrow="Account" title="BloFin demo account" />
          <div className="flex flex-wrap gap-2">
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
        <div className="flex flex-wrap items-center gap-3">
          <Link href="/settings/exchange" className="text-sm text-accent">
            Exchange settings
          </Link>
          {account?.can_refresh ? (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void load(true)}
            >
              {busy ? "Refreshing…" : "Refresh demo account"}
            </Button>
          ) : error ? (
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void load()}
            >
              Retry account
            </Button>
          ) : null}
        </div>
      </CardHeader>
      <CardContent className="card-inner">
        <details>
          <summary className="min-h-11 cursor-pointer text-sm">
            Snapshot scope &amp; refresh
          </summary>
          <p className="text-xs text-text-secondary">
            Configured demo venue account · balances and native open positions.
            {account?.can_refresh
              ? " Native data refreshes every 3 minutes while visible; retries slow down after failures."
              : " Saved snapshots reload every 3 minutes while visible."}
            All balances and positions below use this configured account.
          </p>
        </details>
        <div className="grid grid-cols-2 gap-2 lg:grid-cols-5">
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
            value="—"
            note="Incomplete verified history"
            testId="dashboard-win-rate"
          />
          <TradingMetric
            label="Verified net PnL"
            value={`${formatMoney(verifiedPerformance ? performance.net_pnl : null)} USDT`}
            note="Verified recorded closures only"
            testId="dashboard-pnl"
          />
        </div>
        <p className="text-xs text-text-secondary">
          USD equity and USDT balances use different valuation units. Conversion
          differences are not trading PnL.
        </p>
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
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
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
              <p className="text-xs text-text-secondary">
                As of {formatDateTime(account.synced_at)}
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
            <p className="text-xs text-text-secondary">
              Account refresh reads balances and positions. Order fills, SL/TP
              verification and Journal evidence are reviewed separately.
            </p>
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
