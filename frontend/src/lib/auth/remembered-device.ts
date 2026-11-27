/**
 * "Remember this device" tokens for two-factor sign-in.
 *
 * The server signs each token and can revoke it (Settings > Account > Forget
 * remembered devices, or turning 2FA off). It only skips the code step: the
 * password is still required. Kept per email so a shared browser does not mix
 * accounts; the server ignores a token that belongs to another user.
 */

const KEY = "alphatrade:2fa-remembered-devices";
const MAX_ENTRIES = 5;

type Entry = { token: string; expiresAt: string };
type Store = Record<string, Entry>;

function normalize(email: string): string {
  return email.trim().toLowerCase();
}

function read(): Store {
  if (typeof window === "undefined") return {};
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return parsed && typeof parsed === "object" ? (parsed as Store) : {};
  } catch {
    return {};
  }
}

function write(store: Store): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(store));
  } catch {
    /* Storage denied: the device is simply not remembered. */
  }
}

export function rememberedDeviceToken(email: string): string | undefined {
  const entry = read()[normalize(email)];
  if (!entry || typeof entry.token !== "string") return undefined;
  if (Date.parse(entry.expiresAt) <= Date.now()) {
    forgetRememberedDevice(email);
    return undefined;
  }
  return entry.token;
}

export function rememberDevice(email: string, token: string, expiresAt: string): void {
  const store = read();
  store[normalize(email)] = { token, expiresAt };
  const kept = Object.entries(store)
    .sort(([, a], [, b]) => Date.parse(b.expiresAt) - Date.parse(a.expiresAt))
    .slice(0, MAX_ENTRIES);
  write(Object.fromEntries(kept));
}

export function forgetRememberedDevice(email: string): void {
  const store = read();
  delete store[normalize(email)];
  write(store);
}
