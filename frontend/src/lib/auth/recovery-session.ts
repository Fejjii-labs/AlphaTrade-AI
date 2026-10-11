// Recovery belongs to an authenticated tab session, not a module's reloadable
// generation counter. Token refresh does not change this non-secret identity.
const SESSION_KEY = "alphatrade:recovery-session";
const PREFIXES = ["alphatrade:experiment-draft:", "alphatrade:screening:", "alphatrade:watcher-assignments:"];
type RecoverySession = { id: string; organizationId: string; userId: string };
let current: RecoverySession | null = null;
let invalidated = false;
let durable = false;

export function recoverySession(): RecoverySession | null {
  if (current || invalidated || typeof window === "undefined") return current;
  try {
    const saved: unknown = JSON.parse(sessionStorage.getItem(SESSION_KEY) ?? "null");
    if (saved && typeof saved === "object" && "id" in saved && "organizationId" in saved && "userId" in saved
      && typeof saved.id === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(saved.id)
      && typeof saved.organizationId === "string" && saved.organizationId && typeof saved.userId === "string" && saved.userId) {
      current = saved as RecoverySession;
      durable = true;
    }
  } catch { /* Storage denial must not prevent authentication cleanup. */ }
  return current;
}

export function recoverySessionId(organizationId: string, userId: string): string | null {
  const session = recoverySession();
  return durable && session?.organizationId === organizationId && session.userId === userId ? session.id : null;
}

export function bindRecoverySession(organizationId: string, userId: string): void {
  if (recoverySessionId(organizationId, userId)) return;
  invalidateRecoverySession();
  current = { id: crypto.randomUUID(), organizationId, userId };
  try { sessionStorage.setItem(SESSION_KEY, JSON.stringify(current)); durable = true; }
  catch { /* Mutations separately require durable pending storage before transport. */ }
}

export function invalidateRecoverySession(): void {
  current = null;
  durable = false;
  // Never restore a record that could not be erased during this lifecycle.
  invalidated = true;
  if (typeof window === "undefined") return;
  try {
    for (let i = sessionStorage.length - 1; i >= 0; i--) {
      const key = sessionStorage.key(i);
      if (key === SESSION_KEY || PREFIXES.some(prefix => key?.startsWith(prefix))) {
        try { sessionStorage.removeItem(key!); }
        catch { /* Continue removing other records when one operation is denied. */ }
      }
    }
  } catch { /* A denied storage getter must not stop logout or session listeners. */ }
}
