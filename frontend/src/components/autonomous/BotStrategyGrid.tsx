"use client";

import { useCallback, useMemo, useState } from "react";

import { FilterChips, type FilterChipOption } from "@/components/ui/chip";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { BrainStrategy } from "@/lib/api/brain-types";
import type { WorkerStrategyFamily, WorkerTradingScope } from "@/lib/api/types";
import { cn } from "@/lib/utils";

export type CellStatus = "trading" | "watching" | "off" | "unknown";
type Family = WorkerStrategyFamily | "other";
type Direction = "long" | "short";

const FAMILIES: Array<{ id: Family; label: string }> = [
  { id: "d_line", label: "D-line" },
  { id: "nested", label: "Nested" },
  { id: "sfp", label: "SFP" },
  { id: "other", label: "Other" },
];
const DIRECTIONS: Array<{ id: Direction; label: string }> = [
  { id: "long", label: "Long" },
  { id: "short", label: "Short" },
];
const BASE_TIMEFRAMES = ["15m", "30m", "1h"];
const UNIT_MINUTES: Record<string, number> = { m: 1, h: 60, d: 1440, w: 10080 };

export const STATUS_LABEL: Record<CellStatus, string> = {
  trading: "Trading",
  watching: "Watching only",
  off: "Off",
  unknown: "Status unknown",
};
const STATUS_ORDER: CellStatus[] = ["trading", "watching", "off", "unknown"];
const STATUS_DOT: Record<CellStatus, string> = {
  trading: "bg-success",
  watching: "bg-warning",
  off: "bg-text-muted",
  unknown: "border border-text-muted bg-transparent",
};
const STATUS_TEXT: Record<CellStatus, string> = {
  trading: "text-text-primary",
  watching: "text-text-secondary",
  off: "text-text-muted",
  unknown: "text-text-muted",
};

export function strategyFamily(kind: string): Family {
  if (kind.startsWith("d_line/")) return "d_line";
  if (kind.startsWith("operational_nested_continuation/")) return "nested";
  if (kind.startsWith("swing_failure_pattern/")) return "sfp";
  return "other";
}

/** "BTCUSDT" → "BTC". Other quotes stay whole. */
export function coinLabel(symbol: string): string {
  const upper = symbol.trim().toUpperCase();
  return upper.endsWith("USDT") && upper.length > 4 ? upper.slice(0, -4) : upper;
}

function minutes(timeframe: string): number {
  const match = /^(\d+)([mhdw])$/.exec(timeframe);
  return match ? Number(match[1]) * (UNIT_MINUTES[match[2] ?? "m"] ?? 1) : Number.MAX_SAFE_INTEGER;
}

/**
 * Trading: enabled and inside the worker's traded scope. Watching only: enabled
 * but outside it. Off: disabled. Status unknown: no recent worker report.
 */
export function cellStatus(
  strategy: Pick<BrainStrategy, "enabled"> & { spec: Pick<BrainStrategy["spec"], "kind" | "trigger_timeframe"> },
  scope: WorkerTradingScope | null | undefined,
): CellStatus {
  if (!strategy.enabled) return "off";
  if (!scope) return "unknown";
  const family = scope.families?.find((entry) => entry.family === strategyFamily(strategy.spec.kind));
  if (!family) return "unknown";
  if (!family.trading) return "watching";
  if (family.timeframes == null) return "trading";
  return family.timeframes.includes(strategy.spec.trigger_timeframe) ? "trading" : "watching";
}

type Cell = { status: CellStatus; count: number };
type Grid = {
  family: Family;
  label: string;
  coins: Array<{ symbol: string; cells: Map<string, Cell> }>;
};

function cellKey(timeframe: string, direction: Direction): string {
  return `${timeframe}:${direction}`;
}

/** Best status wins when one cell holds more than one strategy version. */
function merge(current: Cell | undefined, status: CellStatus): Cell {
  if (!current) return { status, count: 1 };
  const best = STATUS_ORDER.indexOf(status) < STATUS_ORDER.indexOf(current.status) ? status : current.status;
  return { status: best, count: current.count + 1 };
}

export function buildGrids(
  strategies: BrainStrategy[],
  scope: WorkerTradingScope | null | undefined,
  watched: string[] = [],
): { grids: Grid[]; timeframes: string[] } {
  const order = new Map(watched.map((symbol, index) => [symbol.toUpperCase(), index]));
  const byFamily = new Map<Family, Map<string, Map<string, Cell>>>();
  const timeframes = new Set(BASE_TIMEFRAMES);
  for (const strategy of strategies) {
    const family = strategyFamily(strategy.spec.kind);
    const symbol = strategy.spec.symbol.trim().toUpperCase();
    const direction: Direction = strategy.spec.direction.toLowerCase() === "short" ? "short" : "long";
    const timeframe = strategy.spec.trigger_timeframe;
    timeframes.add(timeframe);
    const coins = byFamily.get(family) ?? new Map<string, Map<string, Cell>>();
    const cells = coins.get(symbol) ?? new Map<string, Cell>();
    const key = cellKey(timeframe, direction);
    cells.set(key, merge(cells.get(key), cellStatus(strategy, scope)));
    coins.set(symbol, cells);
    byFamily.set(family, coins);
  }
  const rank = (symbol: string) => order.get(symbol) ?? Number.MAX_SAFE_INTEGER;
  const grids = FAMILIES.flatMap(({ id, label }) => {
    const coins = byFamily.get(id);
    if (!coins) return [];
    const rows = [...coins.entries()]
      .map(([symbol, cells]) => ({ symbol, cells }))
      .sort((a, b) => rank(a.symbol) - rank(b.symbol) || a.symbol.localeCompare(b.symbol));
    return [{ family: id, label, coins: rows }];
  });
  return { grids, timeframes: [...timeframes].sort((a, b) => minutes(a) - minutes(b)) };
}

function StatusLine({ direction, status }: { direction: Direction; status: CellStatus }) {
  const name = direction === "long" ? "Long" : "Short";
  return (
    <span className="flex items-start gap-1.5 text-xs" data-status={status}>
      <span aria-hidden className={cn("mt-1 size-2 shrink-0 rounded-full", STATUS_DOT[status])} />
      <span className="flex min-w-0 flex-col sm:flex-row sm:gap-1.5">
        <span className="text-text-muted sm:w-9">{name}</span>
        <span className={STATUS_TEXT[status]}>{STATUS_LABEL[status]}</span>
      </span>
    </span>
  );
}

type Filters = { family: string[]; direction: string[]; timeframe: string[]; status: string[] };
type View = { timeframes: string[]; directions: Direction[]; statuses: CellStatus[] | null };

function shown(cell: Cell | undefined, statuses: CellStatus[] | null): cell is Cell {
  return cell !== undefined && (statuses === null || statuses.includes(cell.status));
}

function visibleCoins(grid: Grid, view: View): Grid["coins"] {
  return grid.coins.filter((coin) =>
    view.timeframes.some((tf) => view.directions.some((dir) => shown(coin.cells.get(cellKey(tf, dir)), view.statuses))),
  );
}

function FamilyCard({ grid, rows, view }: { grid: Grid; rows: Grid["coins"]; view: View }) {
  const { timeframes, directions, statuses } = view;
  const counts = new Map<CellStatus, number>();
  for (const coin of grid.coins) {
    for (const cell of coin.cells.values()) counts.set(cell.status, (counts.get(cell.status) ?? 0) + cell.count);
  }
  const summary = STATUS_ORDER.filter((status) => counts.get(status))
    .map((status) => `${counts.get(status)} ${STATUS_LABEL[status].toLowerCase()}`)
    .join(" · ");
  return (
    <section
      className="space-y-2 rounded-card border border-border-subtle bg-surface-1 p-3"
      data-testid={`strategy-family-${grid.family}`}
      aria-label={`${grid.label} strategies`}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h4 className="text-sm font-semibold text-text-primary">{grid.label}</h4>
        <p className="text-xs text-text-muted">{summary}</p>
      </div>
      <div className="-mx-3 overflow-x-auto px-3">
        <table className="w-full table-fixed border-collapse text-left">
          <thead>
            <tr className="text-xs text-text-muted">
              <th scope="col" className="w-14 py-1 pr-3 font-medium sm:w-24">Coin</th>
              {timeframes.map((tf) => (
                <th key={tf} scope="col" className="py-1 pr-3 font-medium">{tf}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((coin) => (
              <tr key={coin.symbol} className="border-t border-border-subtle align-top" data-testid={`strategy-row-${grid.family}-${coin.symbol}`}>
                <th scope="row" className="py-2 pr-3 text-sm font-medium text-text-primary">{coinLabel(coin.symbol)}</th>
                {timeframes.map((tf) => {
                  const lines = directions.flatMap((dir) => {
                    const cell = coin.cells.get(cellKey(tf, dir));
                    return shown(cell, statuses) ? [<StatusLine key={dir} direction={dir} status={cell.status} />] : [];
                  });
                  return (
                    <td key={tf} className="py-2 pr-3" data-testid={`strategy-cell-${grid.family}-${coin.symbol}-${tf}`}>
                      {lines.length ? <div className="flex flex-col gap-1.5">{lines}</div> : <span className="text-xs text-text-muted">—</span>}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function FilterRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
      <span className="w-20 shrink-0 text-xs font-medium text-text-muted">{label}</span>
      {children}
    </div>
  );
}

/**
 * The bot's strategies as one small table per family: coins down, timeframes
 * across, Long and Short in each cell with an honest status from the worker.
 */
export function BotStrategyGrid({ scope }: { scope: WorkerTradingScope | null | undefined }) {
  const loader = useCallback(() => api.strategyBrain.overview(), []);
  const { data, loading, error } = useAsyncData(loader, []);
  const [filters, setFilters] = useState<Filters>({ family: [], direction: [], timeframe: [], status: [] });
  const built = useMemo(
    () => (data ? buildGrids(data.strategies, scope, data.watched_symbols) : null),
    [data, scope],
  );
  if (loading) return <p className="text-sm text-text-muted">Loading strategies…</p>;
  if (error || !built) return <p className="text-sm text-text-muted">Strategies unavailable.</p>;
  if (!built.grids.length) return <p className="text-sm text-text-muted">No strategies yet.</p>;

  const set = (key: keyof Filters) => (next: string[]) => setFilters((current) => ({ ...current, [key]: next }));
  const view: View = {
    timeframes: built.timeframes.filter((tf) => !filters.timeframe.length || filters.timeframe.includes(tf)),
    directions: DIRECTIONS.map((d) => d.id).filter((d) => !filters.direction.length || filters.direction.includes(d)),
    statuses: filters.status.length ? (filters.status as CellStatus[]) : null,
  };
  const cards = built.grids
    .filter((grid) => !filters.family.length || filters.family.includes(grid.family))
    .map((grid) => ({ grid, rows: visibleCoins(grid, view) }))
    .filter(({ rows }) => rows.length > 0);
  const filterRows: Array<{ key: keyof Filters; label: string; options: FilterChipOption[] }> = [
    { key: "family", label: "Family", options: built.grids.map((grid) => ({ id: grid.family, label: grid.label })) },
    { key: "direction", label: "Direction", options: DIRECTIONS },
    { key: "timeframe", label: "Timeframe", options: built.timeframes.map((tf) => ({ id: tf, label: tf })) },
    { key: "status", label: "Status", options: STATUS_ORDER.map((status) => ({ id: status, label: STATUS_LABEL[status] })) },
  ];

  return (
    <div className="space-y-3" data-testid="bot-strategies">
      <div className="space-y-2" data-testid="strategy-filters">
        {filterRows.map(({ key, label, options }) => (
          <FilterRow key={key} label={label}>
            <FilterChips aria-label={label} selection="single" options={options} value={filters[key]} onChange={set(key)} />
          </FilterRow>
        ))}
        <p className="text-xs text-text-muted">Tap a chip to filter; tap it again to show all.</p>
      </div>
      {!scope ? (
        <p className="text-xs text-text-muted" data-testid="strategy-scope-unknown">
          The bot has not reported what it trades recently, so enabled strategies show Status unknown.
        </p>
      ) : null}
      {cards.length ? (
        cards.map(({ grid, rows }) => <FamilyCard key={grid.family} grid={grid} rows={rows} view={view} />)
      ) : (
        <p className="text-sm text-text-muted">No strategies match these filters.</p>
      )}
    </div>
  );
}
