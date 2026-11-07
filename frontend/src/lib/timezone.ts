/**
 * One resolved timezone for day buckets across the UI (calendar, Daily Review,
 * comparison bounds): the risk-settings timezone when it is valid, else the
 * browser zone, else UTC. The account daily equity stop always uses 00:00 UTC
 * (AT-ADR-152); this zone only decides how journal days are bucketed.
 */

export type TimezoneSource = "risk_settings" | "browser" | "utc";

export type ResolvedTimezone = { timezone: string; source: TimezoneSource };

export const TIMEZONE_SOURCE_LABELS: Record<TimezoneSource, string> = {
  risk_settings: "risk settings",
  browser: "browser",
  utc: "default",
};

export function isValidTimeZone(value: string | null | undefined): value is string {
  if (!value) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: value });
    return true;
  } catch {
    return false;
  }
}

export function browserTimeZone(): string | undefined {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return isValidTimeZone(zone) ? zone : undefined;
  } catch {
    return undefined;
  }
}

export function resolveTimezone(
  risk: { timezone?: string | null; timezone_fallback?: boolean } | null | undefined,
  browser: string | null | undefined = browserTimeZone(),
): ResolvedTimezone {
  // A server-side fallback means the stored zone was unusable; do not echo it.
  if (risk && !risk.timezone_fallback && isValidTimeZone(risk.timezone)) {
    return { timezone: risk.timezone, source: "risk_settings" };
  }
  if (isValidTimeZone(browser)) return { timezone: browser, source: "browser" };
  return { timezone: "UTC", source: "utc" };
}

function zonedParts(instant: Date, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const get = (type: string) => Number(parts.find((part) => part.type === type)?.value);
  return {
    year: get("year"),
    month: get("month") - 1,
    day: get("day"),
    hour: get("hour") % 24,
    minute: get("minute"),
    second: get("second"),
  };
}

/** Calendar date in `timeZone` as YYYY-MM-DD. */
export function zonedIsoDate(instant: Date, timeZone: string): string {
  const { year, month, day } = zonedParts(instant, timeZone);
  return `${year}-${String(month + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

/**
 * A local-clock Date whose year/month/day getters equal today's date in
 * `timeZone`, so existing local-date helpers bucket by the resolved zone.
 */
export function zonedToday(timeZone: string, now: Date = new Date()): Date {
  const { year, month, day } = zonedParts(now, timeZone);
  return new Date(year, month, day, 12);
}

function offsetMs(instant: Date, timeZone: string): number {
  const p = zonedParts(instant, timeZone);
  const asUtc = Date.UTC(p.year, p.month, p.day, p.hour, p.minute, p.second);
  return asUtc - Math.floor(instant.getTime() / 1000) * 1000;
}

/** UTC instant of wall-clock `dateIso` + time in `timeZone` (DST-safe). */
export function zonedWallTimeToUtc(
  dateIso: string,
  timeZone: string,
  time: { hour: number; minute: number; second: number },
): string {
  const [year, month, day] = dateIso.split("-").map(Number);
  const wall = Date.UTC(year, month - 1, day, time.hour, time.minute, time.second);
  let guess = wall - offsetMs(new Date(wall), timeZone);
  // A second pass settles instants that cross a DST transition.
  guess = wall - offsetMs(new Date(guess), timeZone);
  return new Date(guess).toISOString().replace(".000Z", "Z");
}

/** Inclusive day bounds for `dateIso` in `timeZone`, as UTC ISO instants. */
export function zonedDayBoundsUtc(dateIso: string, timeZone: string) {
  return {
    start: zonedWallTimeToUtc(dateIso, timeZone, { hour: 0, minute: 0, second: 0 }),
    end: zonedWallTimeToUtc(dateIso, timeZone, { hour: 23, minute: 59, second: 59 }),
  };
}

const FALLBACK_ZONES = [
  "UTC",
  "Europe/London",
  "Europe/Paris",
  "Europe/Berlin",
  "Africa/Tunis",
  "America/New_York",
  "America/Chicago",
  "America/Los_Angeles",
  "Asia/Dubai",
  "Asia/Singapore",
  "Asia/Tokyo",
  "Australia/Sydney",
];

/** IANA zones for a picker; always includes UTC and the current value. */
export function timeZoneOptions(current?: string | null): string[] {
  let zones: string[] = FALLBACK_ZONES;
  try {
    const supported = (
      Intl as typeof Intl & { supportedValuesOf?: (key: string) => string[] }
    ).supportedValuesOf?.("timeZone");
    if (supported?.length) zones = supported;
  } catch {
    // Older engines: keep the short list.
  }
  const set = new Set(zones);
  set.add("UTC");
  if (current && isValidTimeZone(current)) set.add(current);
  return [...set].sort((a, b) => (a === "UTC" ? -1 : b === "UTC" ? 1 : a.localeCompare(b)));
}
