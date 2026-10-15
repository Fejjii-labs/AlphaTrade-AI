import { invalidateRecoverySession } from "./recovery-session";

let generation = 0;
const listeners = new Set<() => void>();
export const sessionGeneration = () => generation;
export function onSessionCleared(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function sessionCleared(): void {
  generation++;
  invalidateRecoverySession();
  for (const listener of listeners) {
    try { listener(); } catch { /* Every identity owner must still be invalidated. */ }
  }
}
