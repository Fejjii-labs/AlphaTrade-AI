"use client";

import Link from "next/link";

import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ExchangeDiagnosticsSummary } from "@/lib/api/types";
import { workerFlagFreshness } from "@/lib/worker-runtime";
import { formatDecimal } from "@/lib/utils";

function readinessTone(readiness: ExchangeDiagnosticsSummary["readiness"]) {
  if (readiness === "ready") return "healthy";
  if (readiness === "degraded") return "warn";
  return "blocked";
}

function mirrorResultLabel(result: string | null | undefined): string {
  if (result === "created") return "Mirrored successfully";
  if (result === "failed") return "Mirror failed";
  return "No mirror events yet";
}

export function ExchangeDiagnosticsCard({
  diagnostics,
  compact = false,
}: {
  diagnostics: ExchangeDiagnosticsSummary;
  compact?: boolean;
}) {
  return (
    <Card data-testid="exchange-diagnostics-card">
      <CardHeader className="flex flex-row items-start justify-between gap-3 space-y-0">
        <div>
          <CardTitle className="text-base">Exchange diagnostics</CardTitle>
          <p className="mt-1 text-xs text-zinc-500">
            BloFin demo readiness and latest mirror health — read-only, no orders placed.
          </p>
        </div>
        <StatusBadge label={diagnostics.readiness} tone={readinessTone(diagnostics.readiness)} />
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <div className="flex flex-wrap gap-2" data-testid="exchange-safety-badges">
          <StatusBadge label="Paper only" tone="paper" />
          <StatusBadge
            label={
              diagnostics.real_trading_enabled ? "Real trading enabled" : "Real trading disabled"
            }
            tone={diagnostics.real_trading_enabled ? "blocked" : "paper"}
          />
          <StatusBadge
            label={diagnostics.worker_enabled ? "Worker enabled" : "Worker disabled"}
            tone={diagnostics.worker_enabled ? "warn" : "paper"}
          />
          <StatusBadge
            label={diagnostics.telegram_enabled ? "Telegram enabled" : "Telegram disabled"}
            tone={diagnostics.telegram_enabled ? "warn" : "paper"}
          />
        </div>

        {diagnostics.worker_effective?.source === "worker" ? (
          <p className="text-xs text-zinc-500" data-testid="exchange-worker-flags">
            Worker flags: {workerFlagFreshness(diagnostics.worker_effective)}. Real money stays
            blocked.
          </p>
        ) : null}

        {diagnostics.readiness === "blocked" ? (
          <p className="rounded-lg border border-red-900/50 bg-red-950/30 px-3 py-2 text-xs text-red-200" data-testid="exchange-blocked-warning">
            Demo exchange is blocked. Resolve warnings before relying on venue mirroring.
          </p>
        ) : null}

        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-xs text-zinc-500">Demo exchange</dt>
            <dd className="text-zinc-200">
              {diagnostics.demo_active ? diagnostics.exchange_mode : "inactive"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Provider health</dt>
            <dd className="text-zinc-200">{diagnostics.provider_health ?? "unknown"}</dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Position mode</dt>
            <dd className="text-zinc-200">{diagnostics.position_mode ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Leverage (BTC-USDT cross)</dt>
            <dd className="text-zinc-200">
              {diagnostics.leverage?.probe_ok && diagnostics.leverage.leverage != null
                ? `${formatDecimal(diagnostics.leverage.leverage)}x`
                : "Unavailable"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Instrument (BTCUSDT)</dt>
            <dd className="text-zinc-200">
              {diagnostics.instrument?.active === false
                ? "Inactive"
                : diagnostics.instrument?.active
                  ? "Active"
                  : "Unknown"}
            </dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Venue positions</dt>
            <dd className="text-zinc-200">{diagnostics.venue_positions_count ?? "—"}</dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Last mirror order</dt>
            <dd className="text-zinc-200">{mirrorResultLabel(diagnostics.last_demo_mirror_result)}</dd>
          </div>
          <div>
            <dt className="text-xs text-zinc-500">Last cancel</dt>
            <dd className="text-zinc-200">{diagnostics.last_cancel_status ?? "—"}</dd>
          </div>
          {diagnostics.last_exchange_order_status ? (
            <div>
              <dt className="text-xs text-zinc-500">Last venue order status</dt>
              <dd className="text-zinc-200">{diagnostics.last_exchange_order_status}</dd>
            </div>
          ) : null}
          {diagnostics.last_demo_mirror_error_code ? (
            <div className="sm:col-span-2">
              <dt className="text-xs text-zinc-500">Last mirror error</dt>
              <dd className="text-zinc-200">
                Code {diagnostics.last_demo_mirror_error_code}
                {diagnostics.last_demo_mirror_error_message
                  ? ` — ${diagnostics.last_demo_mirror_error_message}`
                  : ""}
              </dd>
            </div>
          ) : null}
        </dl>

        {diagnostics.warnings.length ? (
          <div data-testid="exchange-diagnostics-warnings">
            <p className="mb-1 text-xs font-medium text-amber-400">Warnings</p>
            <ul className="space-y-1 text-xs text-amber-200/90">
              {diagnostics.warnings.map((warning) => (
                <li key={warning}>• {warning}</li>
              ))}
            </ul>
          </div>
        ) : null}

        {compact ? (
          <Link href="/exchange" className="inline-block text-xs text-zinc-400 underline">
            Open full exchange diagnostics
          </Link>
        ) : null}
      </CardContent>
    </Card>
  );
}
