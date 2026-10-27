import type { StrategyProposalRecord } from "@/lib/api/types";

type Rules = NonNullable<StrategyProposalRecord["proposed_structured_rules"]>;
type Condition = NonNullable<NonNullable<Rules["entry_rules"]>[number]["conditions"]>[number];

const OPERATORS: Record<string, string> = {
  gt: ">", gte: "≥", lt: "<", lte: "≤", eq: "=", neq: "≠",
  crosses_above: "crosses above", crosses_below: "crosses below",
};

/** Trader shorthand inside rule tokens, spelled out or kept as acronyms. */
const ABBREVIATIONS: Record<string, string> = { tp: "take profit", sl: "stop loss", atr: "ATR", rsi: "RSI", ema: "EMA", sma: "SMA", vwap: "VWAP", cvd: "CVD", htf: "HTF", ltf: "LTF" };

function words(token: string | null | undefined): string {
  if (!token) return "";
  const text = token.toLowerCase().split("_").map((w) => ABBREVIATIONS[w] ?? w).join(" ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Short indicator tokens (rsi, ema, vwap) read as acronyms. */
function indicator(token: string): string {
  return /^[a-z]{2,4}$/i.test(token) ? token.toUpperCase() : words(token);
}

function condition(c: Condition): string {
  const parts = [
    c.indicator ? indicator(c.indicator) : null,
    c.operator ? OPERATORS[c.operator] ?? words(c.operator).toLowerCase() : null,
    c.value ?? null,
    c.timeframe ? `on ${c.timeframe}` : null,
    c.lookback_candles ? `over ${c.lookback_candles} candles` : null,
    c.confirmation_required ? "(confirmed close)" : null,
  ].filter(Boolean);
  return parts.join(" ");
}

function withConditions(head: string, conditions: Condition[] | undefined, notes?: string | null): string {
  const list = (conditions ?? []).map(condition).filter(Boolean);
  return [head, list.length ? `when ${list.join(" and ")}` : null, notes ? `— ${notes}` : null]
    .filter(Boolean)
    .join(" ");
}

/** A strategy draft's structured rules as plain sentences, grouped. Empty groups are left out. */
export function strategyRuleLines(rules: Rules | null | undefined): { group: string; lines: string[] }[] {
  if (!rules) return [];
  const groups = [
    {
      group: "Entry",
      lines: (rules.entry_rules ?? []).map((r) =>
        withConditions(`${words(r.direction)} on ${words(r.trigger_type).toLowerCase()}`.trim(), r.conditions, r.notes),
      ),
    },
    {
      group: "Exit",
      lines: (rules.exit_rules ?? []).map((r) =>
        withConditions(
          [
            words(r.rule_type),
            r.value ?? null,
            r.r_multiple ? `${r.r_multiple}R` : null,
            r.size_fraction != null ? `${Math.round(r.size_fraction * 100)}% of size` : null,
          ].filter(Boolean).join(" "),
          r.conditions,
          r.notes,
        ),
      ),
    },
    {
      group: "No trade",
      lines: (rules.no_trade_rules ?? []).map((r) =>
        withConditions([words(r.rule_type), r.threshold ? `above ${r.threshold}` : null].filter(Boolean).join(" "), r.conditions, r.notes),
      ),
    },
  ];
  const result = groups.filter((g) => g.lines.length > 0);
  if (rules.primary_timeframe) result.unshift({ group: "Timeframe", lines: [rules.primary_timeframe] });
  return result;
}
