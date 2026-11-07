import { describe, expect, it } from "vitest";

import {
  isValidTimeZone,
  resolveTimezone,
  timeZoneOptions,
  zonedDayBoundsUtc,
  zonedIsoDate,
  zonedToday,
} from "./timezone";

describe("resolved day-bucket timezone", () => {
  it("prefers a valid risk-settings zone, then the browser, then UTC", () => {
    expect(resolveTimezone({ timezone: "Asia/Tokyo", timezone_fallback: false }, "Europe/Paris")).toEqual({
      timezone: "Asia/Tokyo",
      source: "risk_settings",
    });
    expect(resolveTimezone({ timezone: "Mars/Base", timezone_fallback: false }, "Europe/Paris")).toEqual({
      timezone: "Europe/Paris",
      source: "browser",
    });
    // A server-side fallback means the stored zone was unusable.
    expect(resolveTimezone({ timezone: "UTC", timezone_fallback: true }, "Europe/Paris").source).toBe("browser");
    expect(resolveTimezone(null, null)).toEqual({ timezone: "UTC", source: "utc" });
    expect(isValidTimeZone("")).toBe(false);
  });

  it("buckets today and day bounds in the zone, across DST", () => {
    const instant = new Date("2026-11-07T23:30:00Z");
    expect(zonedIsoDate(instant, "UTC")).toBe("2026-11-07");
    expect(zonedIsoDate(instant, "Asia/Tokyo")).toBe("2026-11-08");
    const today = zonedToday("Asia/Tokyo", instant);
    expect([today.getFullYear(), today.getMonth(), today.getDate()]).toEqual([2026, 10, 8]);
    expect(zonedDayBoundsUtc("2026-07-01", "Europe/Paris")).toEqual({
      start: "2026-06-30T22:00:00Z",
      end: "2026-07-01T21:59:59Z",
    });
    expect(zonedDayBoundsUtc("2026-01-15", "Europe/Paris").start).toBe("2026-01-14T23:00:00Z");
    // DST end day in New York: 25 hours long.
    expect(zonedDayBoundsUtc("2026-11-01", "America/New_York")).toEqual({
      start: "2026-11-01T04:00:00Z",
      end: "2026-11-02T04:59:59Z",
    });
    expect(zonedDayBoundsUtc("2026-07-01", "UTC")).toEqual({
      start: "2026-07-01T00:00:00Z",
      end: "2026-07-01T23:59:59Z",
    });
  });

  it("offers IANA zones with UTC first and the current value included", () => {
    const zones = timeZoneOptions("Africa/Tunis");
    expect(zones[0]).toBe("UTC");
    expect(zones).toContain("Africa/Tunis");
    expect(zones).toContain("Europe/Berlin");
  });
});
