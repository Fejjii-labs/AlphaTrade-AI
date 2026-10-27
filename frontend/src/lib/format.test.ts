import { describe, expect, it } from "vitest";

import {
  containsCurrencySymbol,
  formatAmount,
  formatCount,
  formatCurrency,
  formatDate,
  formatDateTime,
  formatMonetary,
  formatPercent,
  formatPrice,
  formatProfitFactor,
  formatQuantity,
  formatRatio,
  formatSignedPercent,
  formatTrendLabel,
  humanizeLimitation,
  humanizeToken,
  UNAVAILABLE,
  venueLabel,
} from "./format";

describe("shared product formatters", () => {
  it("never converts unavailable values into zero", () => {
    expect(formatMonetary(null)).toBe(UNAVAILABLE);
    expect(formatMonetary(undefined)).toBe(UNAVAILABLE);
    expect(formatMonetary("")).toBe(UNAVAILABLE);
    expect(formatMonetary("not-a-number")).toBe(UNAVAILABLE);
    expect(formatPercent(null)).toBe(UNAVAILABLE);
    expect(formatPercent(undefined)).toBe(UNAVAILABLE);
    expect(formatPrice(null)).toBe(UNAVAILABLE);
    expect(formatCount(null)).toBe(UNAVAILABLE);
    expect(formatQuantity(undefined)).toBe(UNAVAILABLE);
    expect(formatRatio(null)).toBe(UNAVAILABLE);
    expect(formatCurrency(null, "USD")).toBe(UNAVAILABLE);
    expect(formatDateTime(null)).toBe(UNAVAILABLE);
    expect(formatDate(null)).toBe(UNAVAILABLE);
  });

  it("distinguishes genuine zero from unavailable", () => {
    expect(formatMonetary(0)).toBe("+0.00");
    expect(formatPercent(0)).toBe("0.0%");
    expect(formatCount(0)).toBe("0");
    expect(formatPrice(0)).toBe("0");
  });

  it("formats money without inventing currency symbols", () => {
    expect(formatMonetary("10004.96448")).toBe("+10004.96");
    expect(formatMonetary("-45.1")).toBe("−45.10");
    expect(containsCurrencySymbol(formatMonetary("100"))).toBe(false);
    expect(formatCurrency(12.5, null)).toBe("12.50");
    expect(containsCurrencySymbol(formatCurrency(12.5, null))).toBe(false);
  });

  it("uses backend currency codes when provided", () => {
    const usd = formatCurrency(12.5, "USD");
    expect(usd).toMatch(/12\.50/);
    expect(usd).toMatch(/\$|USD/);
    const eur = formatCurrency("99.9", "EUR");
    expect(eur).toMatch(/99\.90/);
    expect(eur).toMatch(/€|EUR/);
  });

  it("keeps price and quantity precision compact", () => {
    expect(formatPrice("42150.123456")).toBe("42,150.1235");
    expect(formatQuantity("0.123456789")).toBe("0.1235");
    expect(formatRatio(1.87654)).toBe("1.88");
  });

  it("formats percentages and signed percentages", () => {
    expect(formatPercent(0.5123)).toBe("51.2%");
    expect(formatSignedPercent(-0.05)).toBe("−5.0%");
    expect(formatPercent(51.2, 1, { alreadyPercent: true })).toBe("51.2%");
  });

  it("formats dates without raw ISO microsecond stamps", () => {
    const stamped = formatDateTime("2026-07-28T16:43:30.123937Z");
    expect(stamped).not.toMatch(/T16:43:30\.123937Z/);
    expect(stamped).not.toBe(UNAVAILABLE);
    expect(formatDate("2026-07-28")).toMatch(/2026/);
  });

  it("humanizes tokens and trend labels", () => {
    expect(humanizeToken("daily_loss_limit")).toBe("Daily Loss Limit");
    expect(humanizeLimitation("daily_loss_limit is not configured")).toBe(
      "Daily loss limit is not configured",
    );
    expect(formatTrendLabel("insufficient_data")).toBe("Insufficient data");
    expect(formatProfitFactor(null, ["no_losing_trades"])).toBe("n/a — no losing trades");
    expect(formatProfitFactor(1.8)).toBe("1.80");
  });
});

describe("formatAmount", () => {
  it.each([
    ["50.00000000", "USDT", "50 USDT"],
    ["10240.50000000", "USDT", "10,240.5 USDT"],
    [1000.125, "USDT", "1,000.13 USDT"],
    ["0.5", undefined, "0.5"],
    [null, "USDT", UNAVAILABLE],
    ["", "USDT", UNAVAILABLE],
  ])("formats %s %s as %s", (value, unit, expected) => {
    expect(formatAmount(value, unit)).toBe(expected);
  });

  it("trims percent zeros only when asked", () => {
    expect(formatPercent(0.5, 2, { alreadyPercent: true, trimZeros: true })).toBe("0.5%");
    expect(formatPercent(1, 2, { alreadyPercent: true, trimZeros: true })).toBe("1%");
    expect(formatPercent(0.5, 2, { alreadyPercent: true })).toBe("0.50%");
  });

  it("labels venues without exposing raw venue codes", () => {
    expect(venueLabel("BLOFIN_DEMO")).toBe("BloFin demo");
    expect(venueLabel("PAPER")).toBe("Paper");
    expect(venueLabel("SOME_TESTNET")).toBe("Some Testnet");
    expect(venueLabel(null)).toBe(UNAVAILABLE);
  });
});
