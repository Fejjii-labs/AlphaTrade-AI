/**
 * Product-wide display formatters for money, prices, percentages, ratios, counts, and dates.
 *
 * Honesty rules:
 * - null / undefined / empty / NaN → "—" (never invent 0)
 * - Do not invent a currency symbol when the backend does not provide a currency code
 * - Prefer concise trading-product precision (not raw five/six-decimal dumps)
 */

export const UNAVAILABLE = "—";

function toFiniteNumber(value: string | number | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

/** Account-currency-agnostic monetary formatter — never emits currency symbols/codes. */
export function formatMonetary(value: string | number | null | undefined): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return UNAVAILABLE;
  // Include explicit "+" for zero so genuine zero is visually distinct from "—".
  const sign = numeric >= 0 ? "+" : "−";
  return `${sign}${Math.abs(numeric).toFixed(2)}`;
}

/** Decimal account money: two decimals, preserving meaningful tiny fees/PnL. */
export function formatMoney(value: string | number | null | undefined): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return UNAVAILABLE;
  const magnitude = Math.abs(numeric);
  const digits = magnitude > 0 && magnitude < 1
    ? Math.min(20, Math.max(8, -Math.floor(Math.log10(magnitude)) + 3)) : 2;
  return numeric.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: digits,
  });
}

/**
 * Amount with a unit suffix: "50 USDT", "10,240.5 USDT".
 * At most two decimals with trailing zeros trimmed, so Decimal strings such as
 * "50.00000000" read as "50".
 */
export function formatAmount(
  value: string | number | null | undefined,
  unit?: string | null,
): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return UNAVAILABLE;
  const amount = numeric.toLocaleString("en-US", {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  });
  const suffix = unit?.trim();
  return suffix ? `${amount} ${suffix}` : amount;
}

/**
 * Format an amount with an explicit ISO currency code from the backend.
 * When currency is missing, falls back to plain decimal money (no invented symbol).
 */
export function formatCurrency(
  value: string | number | null | undefined,
  currencyCode?: string | null,
): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return UNAVAILABLE;
  const code = currencyCode?.trim();
  if (!code) {
    return numeric.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  }
  try {
    return numeric.toLocaleString(undefined, {
      style: "currency",
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  } catch {
    return `${numeric.toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })} ${code}`;
  }
}

/** Absolute price levels (entry/exit/mark). Null-safe; compact decimals. */
export function formatPrice(
  value: string | number | null | undefined,
  options?: { maximumFractionDigits?: number },
): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return UNAVAILABLE;
  const maxDigits = options?.maximumFractionDigits ?? 4;
  return numeric.toLocaleString(undefined, {
    minimumFractionDigits: 0,
    maximumFractionDigits: maxDigits,
  });
}

/**
 * Format a ratio already expressed as a fraction (0.5123 → "51.2%").
 * Pass `alreadyPercent: true` when the value is already on a 0–100 scale, and
 * `trimZeros: true` to drop trailing zeros ("0.50%" → "0.5%").
 */
export function formatPercent(
  value: number | null | undefined,
  digits = 1,
  options?: { alreadyPercent?: boolean; trimZeros?: boolean },
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNAVAILABLE;
  const pct = options?.alreadyPercent ? value : value * 100;
  const fixed = pct.toFixed(digits);
  return `${options?.trimZeros && fixed.includes(".") ? fixed.replace(/\.?0+$/, "") : fixed}%`;
}

export function formatSignedPercent(
  value: number | null | undefined,
  digits = 1,
  options?: { alreadyPercent?: boolean },
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNAVAILABLE;
  const pct = options?.alreadyPercent ? value : value * 100;
  const sign = pct > 0 ? "+" : pct < 0 ? "−" : "";
  return `${sign}${Math.abs(pct).toFixed(digits)}%`;
}

/** Compact quantity / size (position size, quantity). */
export function formatQuantity(
  value: string | number | null | undefined,
  options?: { maximumFractionDigits?: number },
): string {
  const numeric = toFiniteNumber(value);
  if (numeric === null) return UNAVAILABLE;
  return numeric.toLocaleString(undefined, {
    maximumFractionDigits: options?.maximumFractionDigits ?? 4,
  });
}

/** Integer counts (trades, wins, samples). */
export function formatCount(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNAVAILABLE;
  return Math.trunc(value).toLocaleString();
}

/** Ratios such as profit factor, R-multiples. */
export function formatRatio(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return UNAVAILABLE;
  return value.toFixed(digits);
}

export function formatProfitFactor(
  value: number | null | undefined,
  warnings?: string[],
): string {
  if (value === null || value === undefined) {
    if (warnings?.some((w) => w.includes("no losing") || w.includes("no_losing"))) {
      return "n/a — no losing trades";
    }
    return UNAVAILABLE;
  }
  return value.toFixed(2);
}

/** Human date-time for UI (never raw ISO microsecond stamps). */
export function formatDateTime(value: string | Date | null | undefined): string {
  if (!value) return UNAVAILABLE;
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return UNAVAILABLE;
  return date.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Date-only label (UTC calendar date when given an ISO date/datetime). */
export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return UNAVAILABLE;
  if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const [y, m, d] = value.split("-").map(Number);
    const date = new Date(Date.UTC(y, m - 1, d));
    return date.toLocaleDateString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      timeZone: "UTC",
    });
  }
  const date = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(date.getTime())) return UNAVAILABLE;
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateRangeLabel(from: string | null, to: string | null): string {
  if (!from && !to) return "All time";
  if (from && to) return `${formatDate(from)} → ${formatDate(to)}`;
  if (from) return `From ${formatDate(from)}`;
  return `Until ${formatDate(to)}`;
}

export function isoDateOnly(date: Date): string {
  return date.toISOString().slice(0, 10);
}

export function addDays(date: Date, days: number): Date {
  const copy = new Date(date);
  copy.setUTCDate(copy.getUTCDate() + days);
  return copy;
}

export function formatTrendLabel(label: string | null | undefined): string {
  if (!label) return UNAVAILABLE;
  if (label === "insufficient_data") return "Insufficient data";
  if (label === "improving") return "Improving";
  if (label === "flat") return "Flat";
  if (label === "deteriorating") return "Deteriorating";
  return humanizeToken(label);
}

const VENUE_LABELS: Record<string, string> = { BLOFIN_DEMO: "BloFin demo", PAPER: "Paper" };

/** Readable venue name; the raw venue code belongs in Technical details. */
export function venueLabel(code: string | null | undefined): string {
  if (!code) return UNAVAILABLE;
  return VENUE_LABELS[code] ?? humanizeToken(code.toLowerCase());
}

/** Convert snake_case / enum tokens to short product labels. */
export function humanizeToken(value: string | null | undefined): string {
  if (!value) return UNAVAILABLE;
  return value
    .replaceAll("_", " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * Humanize backend limitation / config phrases that contain snake_case tokens
 * (e.g. "daily_loss_limit is not configured" → "Daily loss limit is not configured").
 */
export function humanizeLimitation(message: string | null | undefined): string {
  if (!message) return UNAVAILABLE;
  return message.replace(/\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b/g, (token) => {
    const words = token.split("_").join(" ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  });
}

/** Returns true when a rendered monetary string accidentally contains currency markers. */
export function containsCurrencySymbol(text: string): boolean {
  return /[$£€]|USD|EUR|GBP/i.test(text);
}

export function parseDecimal(value: string | null | undefined): number | null {
  return toFiniteNumber(value);
}
