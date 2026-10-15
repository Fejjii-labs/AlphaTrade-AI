import { beforeEach, describe, expect, it, vi } from "vitest";
import { activityPage, nativeFill } from "@/test/native-activity-fixtures";
import { apiFetch } from "./client";
import { readNativeActivity } from "./blofin-activity";
import * as validators from "./generated/validators";

vi.mock("./client", async original => ({ ...await original<typeof import("./client")>(), apiFetch: vi.fn() }));
beforeEach(() => vi.mocked(apiFetch).mockReset().mockResolvedValue(activityPage()));

describe("generated native activity contract", () => {
  it("reads stored history with authentication, cursor and cancellation; never mutates", async () => {
    const signal = new AbortController().signal;
    const result = await readNativeActivity({ kind: "fill", limit: 20, cursor: "opaque" }, { signal });
    expect(result).toEqual(activityPage());
    expect(apiFetch).toHaveBeenCalledOnce();
    expect(apiFetch).toHaveBeenCalledWith("/exchange/blofin/activity", expect.objectContaining({ method: "GET", auth: true, signal, query: { kind: "fill", limit: 20, cursor: "opaque" } }));
  });
  it("keeps original decimal/millisecond strings and nullable monetary evidence", () => {
    const page = activityPage();
    expect(validators.blofinActivityResponse(page)).toBe(true);
    expect(page.items[0].quantity).toBe("0.123456789123456789");
    expect(page.items[0].fee_currency).toBeNull();
    expect(page.items[0].realized_pnl).toBeNull();
    expect(page.items[0].funding).toBeNull();
    expect(validators.blofinActivityResponse(activityPage({ items: [nativeFill({ fee: "0", realized_pnl: "0" })] }))).toBe(true);
  });
  it.each(["quantity", "price", "fee", "occurred_at_ms"])("rejects numeric %s rather than coercing precision", field => {
    const page = activityPage();
    const item = { ...page.items[0], [field]: 123.456 };
    expect(validators.blofinActivityResponse({ ...page, items: [item] })).toBe(false);
    expect(item[field as keyof typeof item]).toBe(123.456);
  });
  it("rejects simulated origin, substituted units and invented complete coverage", () => {
    const page = activityPage();
    for (const item of [{ ...page.items[0], origin: "simulated" }, { ...page.items[0], quantity_unit: "coins" }]) {
      expect(validators.blofinActivityResponse({ ...page, items: [item] })).toBe(false);
    }
    expect(validators.blofinActivityResponse({ ...page, partial_coverage: false })).toBe(false);
  });
  it("withholds malformed pages and does not automatically repeat a request", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ ...activityPage(), items: [{ secret: "private-contract-evidence" }] });
    await expect(readNativeActivity()).rejects.toThrow("API response did not match its contract");
    expect(apiFetch).toHaveBeenCalledOnce();
  });
});
