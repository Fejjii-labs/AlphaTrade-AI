import type { NativeActivityItem, NativeActivityPage } from "./api/blofin-activity";

/** Native fill identity, never the linked command or a local execution projection. */
export function nativeIdentity(page: NativeActivityPage, item: NativeActivityItem): string {
  return JSON.stringify([page.organization_id, page.environment, page.account_uid,
    item.kind, item.kind === "fill" ? item.trade_id ?? item.native_id : item.native_id]);
}

export function nativeAccount(page: NativeActivityPage): string {
  return JSON.stringify([page.organization_id, page.environment, page.account_uid]);
}

export function mergeNativePages(previous: NativeActivityPage | null, next: NativeActivityPage): NativeActivityPage {
  if (next.identity_status !== "verified") return { ...next, items: [] };
  const items = new Map<string, NativeActivityItem>();
  if (previous?.identity_status === "verified" && nativeAccount(previous) === nativeAccount(next)) {
    for (const item of previous.items) items.set(nativeIdentity(previous, item), item);
  }
  for (const item of next.items) items.set(nativeIdentity(next, item), item);
  return { ...next, items: [...items.values()] };
}

/** Timestamp conversion only; quantities, prices and money never pass through Number. */
export function nativeTime(milliseconds: string | null | undefined): string {
  if (!milliseconds || !/^\d+$/.test(milliseconds)) return "Time unknown";
  const time = new Date(Number(milliseconds));
  return Number.isNaN(time.valueOf()) ? "Time unknown" : time.toLocaleString();
}

export function nativeMoney(value: string | null | undefined, currency: string | null | undefined): string {
  return value == null ? "Unknown" : `${value} ${currency || "(currency unknown)"}`;
}
