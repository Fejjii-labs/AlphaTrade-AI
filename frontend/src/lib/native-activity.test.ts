import { describe, expect, it } from "vitest";
import { activityPage, nativeFill } from "@/test/native-activity-fixtures";
import { mergeNativePages, nativeMoney, nativeTime } from "./native-activity";

describe("native accounting", () => {
  it("counts native fill identities once across overlapping pages, never matched commands", () => {
    const command_id = "22222222-2222-4222-8222-222222222222";
    const fill = nativeFill({ command_id, origin: "alphatrade_matched" });
    const result = mergeNativePages(activityPage({ items: [fill] }), activityPage({ items: [
      { ...fill, native_id: "alternate-projection" },
      nativeFill({ native_id: "fill-2", trade_id: "fill-2", command_id, origin: "alphatrade_matched" }),
    ] }));
    expect(result.items).toHaveLength(2);
    expect(result.items[0].quantity).toBe("0.123456789123456789");
  });
  it("discards former native accounts and withholds unverified data", () => {
    const previous = activityPage();
    expect(mergeNativePages(previous, activityPage({ account_uid: "new", items: [] })).items).toEqual([]);
    expect(mergeNativePages(previous, activityPage({ identity_status: "unverified" })).items).toEqual([]);
  });
  it("keeps exact signed amounts and distinguishes zero from unknown", () => {
    expect(nativeMoney("-0.000123456789123456789", null)).toBe("-0.000123456789123456789 (currency unknown)");
    expect(nativeMoney(null, "USDT")).toBe("Unknown");
    expect(nativeMoney("0", "USDT")).toBe("0 USDT");
    expect(nativeTime("not-time")).toBe("Time unknown");
    expect(nativeTime("99999999999999999999")).toBe("Time unknown");
  });
});
