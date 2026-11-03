import type { ReactNode } from "react";

import type { JournalDashboardSummary } from "@/lib/api/types";
import { formatCount, formatMonetary, formatPercent, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { pnlTone, toNumber } from "./performance-model";

const toneText = {
  profit: "text-success",
  loss: "text-danger",
  flat: "text-text-primary",
  none: "text-text-muted",
} as const;

function KpiCard({
  label,
  value,
  valueClassName,
  detail,
  visual,
  testId,
}: {
  label: string;
  value: string;
  valueClassName?: string;
  detail?: ReactNode;
  visual?: ReactNode;
  testId: string;
}) {
  return (
    <div
      className="flex min-h-[112px] items-center justify-between gap-3 rounded-card border border-border-subtle bg-surface-1 p-4"
      data-testid={testId}
    >
      <div className="min-w-0">
        <p className="text-xs font-medium text-text-secondary">{label}</p>
        <p className={cn("mt-1 font-data text-2xl font-semibold tabular-nums text-text-primary", valueClassName)}>
          {value}
        </p>
        {detail ? <div className="mt-1 text-xs text-text-muted">{detail}</div> : null}
      </div>
      {visual ? <div className="shrink-0">{visual}</div> : null}
    </div>
  );
}

/** Donut: gross profit (green) vs gross loss (red) share. */
export function ProfitFactorRing({
  grossProfit,
  grossLoss,
}: {
  grossProfit: number | null;
  grossLoss: number | null;
}) {
  const profit = Math.max(0, grossProfit ?? 0);
  const loss = Math.abs(Math.min(0, grossLoss ?? 0));
  const total = profit + loss;
  const radius = 22;
  const circumference = 2 * Math.PI * radius;
  const profitLength = total ? (profit / total) * circumference : 0;
  return (
    <svg viewBox="0 0 56 56" className="h-14 w-14" aria-hidden="true">
      <circle cx="28" cy="28" r={radius} fill="none" strokeWidth="6" stroke="var(--color-border-subtle)" />
      {total ? (
        <>
          <circle
            cx="28"
            cy="28"
            r={radius}
            fill="none"
            strokeWidth="6"
            stroke="var(--color-danger)"
            transform="rotate(-90 28 28)"
          />
          <circle
            cx="28"
            cy="28"
            r={radius}
            fill="none"
            strokeWidth="6"
            stroke="var(--color-success)"
            strokeDasharray={`${profitLength} ${circumference}`}
            transform="rotate(-90 28 28)"
          />
        </>
      ) : null}
    </svg>
  );
}

/** Semicircle split into win / breakeven / loss arcs. */
export function WinRateGauge({
  wins,
  breakeven,
  losses,
}: {
  wins: number;
  breakeven: number;
  losses: number;
}) {
  const total = wins + breakeven + losses;
  const radius = 26;
  const half = Math.PI * radius;
  const winLength = total ? (wins / total) * half : 0;
  const beLength = total ? (breakeven / total) * half : 0;
  const lossLength = total ? (losses / total) * half : 0;
  const arc = "M 6 34 A 26 26 0 0 1 58 34";
  const segment = (length: number, offset: number, color: string) =>
    length > 0 ? (
      <path
        d={arc}
        fill="none"
        strokeWidth="6"
        stroke={color}
        strokeDasharray={`${length} ${half}`}
        strokeDashoffset={-offset}
      />
    ) : null;
  return (
    <div className="flex w-16 flex-col items-center">
      <svg viewBox="0 0 64 38" className="w-16" aria-hidden="true">
        <path d={arc} fill="none" strokeWidth="6" stroke="var(--color-border-subtle)" />
        {segment(winLength, 0, "var(--color-success)")}
        {segment(beLength, winLength, "var(--color-accent)")}
        {segment(lossLength, winLength + beLength, "var(--color-danger)")}
      </svg>
      <div className="flex w-full justify-between font-data text-[11px]" data-testid="win-rate-counts">
        <span className="text-success" title="Wins">{wins}</span>
        <span className="text-accent" title="Breakeven">{breakeven}</span>
        <span className="text-danger" title="Losses">{losses}</span>
      </div>
    </div>
  );
}

/** Horizontal bar: avg win (green) vs |avg loss| (red), proportional. */
export function WinLossBar({
  averageWin,
  averageLoss,
}: {
  averageWin: string | null;
  averageLoss: string | null;
}) {
  const win = Math.max(0, toNumber(averageWin) ?? 0);
  const loss = Math.abs(Math.min(0, toNumber(averageLoss) ?? 0));
  const total = win + loss;
  return (
    <div className="w-28 sm:w-32">
      <div className="flex h-2 overflow-hidden rounded-full bg-border-subtle">
        {total ? (
          <>
            <div className="h-full bg-success" style={{ width: `${(win / total) * 100}%` }} />
            <div className="ml-0.5 h-full flex-1 bg-danger" />
          </>
        ) : null}
      </div>
      <div className="mt-1 flex justify-between font-data text-[11px]">
        <span className="text-success">{formatMonetary(averageWin)}</span>
        <span className="text-danger">{formatMonetary(averageLoss)}</span>
      </div>
    </div>
  );
}

export function PerformanceKpiCards({ summary }: { summary: JournalDashboardSummary | null }) {
  const s = summary;
  const tone = pnlTone(s?.net_pnl);
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" data-testid="performance-kpis">
      <KpiCard
        testId="kpi-net-pnl"
        label="Net P&L"
        value={formatMonetary(s?.net_pnl)}
        valueClassName={toneText[tone]}
        detail={s ? `${formatCount(s.trade_count)} closed trades` : "No data"}
      />
      <KpiCard
        testId="kpi-profit-factor"
        label="Profit factor"
        value={formatRatio(s?.profit_factor)}
        detail={
          s && s.profit_factor === null && s.losses === 0 && s.wins > 0
            ? "No losing trades yet"
            : "Gross profit ÷ gross loss"
        }
        visual={
          <ProfitFactorRing
            grossProfit={toNumber(s?.gross_profit)}
            grossLoss={toNumber(s?.gross_loss)}
          />
        }
      />
      <KpiCard
        testId="kpi-win-rate"
        label="Trade win %"
        value={formatPercent(s?.win_rate, 1)}
        detail="Wins ÷ (wins + losses)"
        visual={
          <WinRateGauge
            wins={s?.wins ?? 0}
            breakeven={s?.breakeven ?? 0}
            losses={s?.losses ?? 0}
          />
        }
      />
      <KpiCard
        testId="kpi-win-loss"
        label="Avg win / avg loss"
        value={formatRatio(s?.win_loss_ratio)}
        visual={<WinLossBar averageWin={s?.average_win ?? null} averageLoss={s?.average_loss ?? null} />}
      />
    </div>
  );
}
