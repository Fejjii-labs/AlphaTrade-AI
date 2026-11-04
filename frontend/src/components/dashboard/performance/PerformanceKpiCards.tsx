import type { LucideIcon } from "lucide-react";
import { CalendarCheck, Percent, Scale, Sigma, Wallet, Waypoints } from "lucide-react";
import type { ReactNode } from "react";

import { Skeleton } from "@/components/ui/skeleton";
import type { JournalDashboardRisk, JournalDashboardSummary } from "@/lib/api/types";
import { formatCount, formatMonetary, formatPercent, formatRatio } from "@/lib/format";
import { cn } from "@/lib/utils";

import { pnlTone, toNumber } from "./performance-model";

/** profit/loss follow the value's sign; neutral cards carry no verdict; none = no data. */
export type KpiTone = "profit" | "loss" | "neutral" | "none";

const toneText: Record<KpiTone, string> = {
  profit: "text-success",
  loss: "text-danger",
  neutral: "text-text-primary",
  none: "text-text-muted",
};

const toneCard: Record<KpiTone, string> = {
  profit: "border-success-border bg-gradient-to-br from-success-muted to-surface-1 to-70%",
  loss: "border-danger-border bg-gradient-to-br from-danger-muted to-surface-1 to-70%",
  neutral: "border-accent-border bg-gradient-to-br from-accent-muted to-surface-1 to-70%",
  none: "border-border-subtle bg-surface-1",
};

const toneBar: Record<KpiTone, string> = {
  profit: "bg-success",
  loss: "bg-danger",
  neutral: "bg-accent",
  none: "bg-border-subtle",
};

const toneIcon: Record<KpiTone, string> = {
  profit: "bg-success-muted text-success",
  loss: "bg-danger-muted text-danger",
  neutral: "bg-accent-muted text-accent",
  none: "bg-surface-2 text-text-muted",
};

/** Sign-driven tone for money values (zero is neutral). */
export function moneyTone(value: string | number | null | undefined): KpiTone {
  const tone = pnlTone(value);
  return tone === "flat" ? "neutral" : tone;
}

/** Profit factor below 1 loses money; at or above 1 it does not. */
export function profitFactorTone(value: number | null | undefined): KpiTone {
  if (value === null || value === undefined || !Number.isFinite(value)) return "none";
  return value >= 1 ? "profit" : "loss";
}

function KpiCard({
  label,
  value,
  tone,
  icon: Icon,
  detail,
  visual,
  testId,
}: {
  label: string;
  value: string;
  tone: KpiTone;
  icon: LucideIcon;
  detail?: ReactNode;
  visual?: ReactNode;
  testId: string;
}) {
  return (
    <div
      className={cn(
        "relative flex min-h-[112px] items-center justify-between gap-3 overflow-hidden rounded-card border p-4 pt-5",
        toneCard[tone],
      )}
      data-testid={testId}
      data-tone={tone}
    >
      <span className={cn("absolute inset-x-0 top-0 h-1", toneBar[tone])} aria-hidden="true" />
      <div className="min-w-0">
        <p className="flex items-center gap-2 text-xs font-medium text-text-secondary">
          <span
            className={cn("inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-control", toneIcon[tone])}
            aria-hidden="true"
          >
            <Icon className="h-3.5 w-3.5" />
          </span>
          {label}
        </p>
        <p className={cn("mt-1.5 font-data text-2xl font-semibold tabular-nums", toneText[tone])}>{value}</p>
        {detail ? <div className="mt-1 text-xs text-text-secondary">{detail}</div> : null}
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

/** Semicircle split into win / breakeven / loss arcs, with a text alternative. */
export function WinRateGauge({
  wins,
  breakeven,
  losses,
  unit = "trades",
  testId = "win-rate-counts",
}: {
  wins: number;
  breakeven: number;
  losses: number;
  unit?: "trades" | "days";
  testId?: string;
}) {
  const total = wins + breakeven + losses;
  const radius = 26;
  const half = Math.PI * radius;
  const winLength = total ? (wins / total) * half : 0;
  const beLength = total ? (breakeven / total) * half : 0;
  const lossLength = total ? (losses / total) * half : 0;
  const arc = "M 6 34 A 26 26 0 0 1 58 34";
  const showBreakeven = unit === "trades";
  const description = total
    ? `${wins} winning, ${showBreakeven ? `${breakeven} breakeven, ` : ""}${losses} losing ${unit}`
    : `No closed ${unit} yet`;
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
    <div className="flex w-16 flex-col items-center" role="img" aria-label={description}>
      <svg viewBox="0 0 64 38" className="w-16" aria-hidden="true">
        <path d={arc} fill="none" strokeWidth="6" stroke="var(--color-border-subtle)" />
        {segment(winLength, 0, "var(--color-success)")}
        {segment(beLength, winLength, "var(--color-accent)")}
        {segment(lossLength, winLength + beLength, "var(--color-danger)")}
      </svg>
      <div className="flex w-full justify-between font-data text-[11px]" data-testid={testId} aria-hidden="true">
        <span className="text-success" title={`Winning ${unit}`}>{wins}</span>
        {showBreakeven ? <span className="text-accent" title="Breakeven">{breakeven}</span> : null}
        <span className="text-danger" title={`Losing ${unit}`}>{losses}</span>
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

const KPI_TEST_IDS = [
  "kpi-net-pnl",
  "kpi-expectancy",
  "kpi-profit-factor",
  "kpi-win-rate",
  "kpi-day-win-rate",
  "kpi-win-loss",
] as const;

function KpiSkeletons() {
  return (
    <div
      className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
      role="status"
      aria-label="Loading performance figures"
      aria-busy="true"
      data-testid="performance-kpis-loading"
    >
      {KPI_TEST_IDS.map((id) => (
        <div key={id} className="min-h-[112px] rounded-card border border-border-subtle bg-surface-1 p-4 pt-5">
          <Skeleton className="h-3 w-24" />
          <Skeleton className="mt-3 h-7 w-28" />
          <Skeleton className="mt-2 h-3 w-36" />
        </div>
      ))}
    </div>
  );
}

/** Day win % = winning days ÷ (winning + losing days); flat days are excluded. */
export function dayWinRate(risk: JournalDashboardRisk | null | undefined): number | null {
  if (!risk) return null;
  const decided = risk.winning_days + risk.losing_days;
  return decided ? risk.winning_days / decided : null;
}

export function PerformanceKpiCards({
  summary,
  risk = null,
  loading = false,
}: {
  summary: JournalDashboardSummary | null;
  risk?: JournalDashboardRisk | null;
  loading?: boolean;
}) {
  if (loading) return <KpiSkeletons />;
  const s = summary;
  const winRate = s?.win_rate ?? null;
  const dayRate = dayWinRate(risk);
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" data-testid="performance-kpis">
      <KpiCard
        testId="kpi-net-pnl"
        label="Net P&L"
        icon={Wallet}
        value={formatMonetary(s?.net_pnl)}
        tone={moneyTone(s?.net_pnl)}
        detail={s ? `${formatCount(s.trade_count)} closed trades` : "No data"}
      />
      <KpiCard
        testId="kpi-expectancy"
        label="Expectancy"
        icon={Sigma}
        value={formatMonetary(s?.expectancy)}
        tone={moneyTone(s?.expectancy)}
        detail="Net P&L per closed trade"
      />
      <KpiCard
        testId="kpi-profit-factor"
        label="Profit factor"
        icon={Scale}
        value={formatRatio(s?.profit_factor)}
        tone={
          s && s.profit_factor === null && s.losses === 0 && s.wins > 0
            ? "profit"
            : profitFactorTone(s?.profit_factor)
        }
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
        icon={Percent}
        value={formatPercent(winRate, 1)}
        tone={winRate === null ? "none" : "neutral"}
        detail="Wins ÷ (wins + losses) · excludes breakevens"
        visual={
          <WinRateGauge
            wins={s?.wins ?? 0}
            breakeven={s?.breakeven ?? 0}
            losses={s?.losses ?? 0}
          />
        }
      />
      <KpiCard
        testId="kpi-day-win-rate"
        label="Day win %"
        icon={CalendarCheck}
        value={formatPercent(dayRate, 1)}
        tone={dayRate === null ? "none" : "neutral"}
        detail="Green days ÷ (green + red days)"
        visual={
          <WinRateGauge
            wins={risk?.winning_days ?? 0}
            breakeven={0}
            losses={risk?.losing_days ?? 0}
            unit="days"
            testId="day-win-rate-counts"
          />
        }
      />
      <KpiCard
        testId="kpi-win-loss"
        label="Avg win / avg loss"
        icon={Waypoints}
        value={formatRatio(s?.win_loss_ratio)}
        tone={s?.win_loss_ratio == null ? "none" : "neutral"}
        visual={<WinLossBar averageWin={s?.average_win ?? null} averageLoss={s?.average_loss ?? null} />}
      />
    </div>
  );
}
