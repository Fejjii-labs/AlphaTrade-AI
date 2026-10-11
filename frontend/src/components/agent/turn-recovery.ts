import { agentTurnRequest, turnConflictDetails } from "@/lib/api/generated/validators";
import type { components } from "@/lib/api/generated/types";
import type { VoiceTerminalProof, VoiceTurn } from "@/lib/voice/conversation-types";

export type TurnBody = components["schemas"]["AgentTurnRequest"];
export type ConflictDetails = components["schemas"]["TurnConflictDetails"];
export type PendingTurn = {
  key: string;
  body: TurnBody;
  conversationId: string | null;
  state: "uncertain" | ConflictDetails["reason"];
  createdAt: string;
  origin?: "text" | "voice";
  voice?: VoiceTurn;
};

const prefix = "alphatrade:pending-turn:";
const terminalPrefix = "alphatrade:turn-outcome:";
const draftPrefix = "alphatrade:typed-draft:";
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const memory = new Map<string, Map<string, PendingTurn>>();
const terminals = new Map<string, Map<string, VoiceTerminalProof>>();
const drafts = new Map<string, string>();
const listeners = new Map<string, Set<() => void>>();

export function typedDraftFor(scope: string, conversation: string | null): string {
  const key = draftPrefix + scope + ":" + (conversation ?? "new");
  if (drafts.has(key)) return drafts.get(key)!;
  try { return sessionStorage.getItem(key) ?? ""; } catch { return ""; }
}

export function persistTypedDraft(scope: string, conversation: string | null, text: string): void {
  const key = draftPrefix + scope + ":" + (conversation ?? "new");
  drafts.set(key, text);
  try { sessionStorage.setItem(key, text); } catch { /* Keep the editable draft in this session. */ }
}

function stored(key: string): unknown[] {
  try { const value: unknown = JSON.parse(sessionStorage.getItem(key) ?? "[]"); return Array.isArray(value) ? value : []; }
  catch { return []; }
}

function validPending(p: unknown): p is PendingTurn {
  if (!p || typeof p !== "object") return false;
  const value = p as PendingTurn;
  return uuid.test(value.key) && agentTurnRequest(value.body) &&
    typeof value.createdAt === "string" && Number.isFinite(Date.parse(value.createdAt)) &&
    (value.conversationId === null || (typeof value.conversationId === "string" && uuid.test(value.conversationId))) &&
    (value.origin === undefined || value.origin === "text" || value.origin === "voice") &&
    (value.origin !== "voice" || Boolean(value.voice)) &&
    (!value.voice || (value.origin === "voice" && value.voice.origin === "voice" &&
      value.voice.turnKey === value.key && value.voice.transcript === value.body.message &&
      uuid.test(value.voice.conversationId) && value.voice.conversationId === value.body.conversation_id)) &&
    (value.state === "uncertain" || turnConflictDetails({ reason: value.state, turn_id: value.key,
      conversation_id: value.conversationId ?? "00000000-0000-0000-0000-000000000000" }));
}

function validTerminal(value: unknown): value is VoiceTerminalProof {
  if (!value || typeof value !== "object") return false;
  const p = value as VoiceTerminalProof;
  return uuid.test(p.turnKey) && uuid.test(p.conversationId) && (p.outcome === "acknowledged" || p.outcome === "rejected") &&
    (p.origin === undefined || p.origin === "text" || p.origin === "voice");
}

export function terminalFor(scope: string, key: string): VoiceTerminalProof | null {
  const all = new Map(stored(terminalPrefix + scope).filter(validTerminal).map(p => [p.turnKey, p]));
  for (const [id, proof] of terminals.get(scope) ?? []) all.set(id, proof);
  return all.get(key) ?? null;
}

function read(scope: string): PendingTurn[] {
  const all = new Map(stored(prefix + scope).filter(validPending).map(p => [p.key, p]));
  for (const [id, pending] of memory.get(scope) ?? []) all.set(id, pending);
  return [...all.values()].filter(p => {
    const proof = terminalFor(scope, p.key);
    const identity = p.voice?.conversationId ?? p.body.conversation_id ?? p.conversationId;
    return !proof || Boolean(identity && identity !== proof.conversationId);
  });
}

function write(scope: string, values: PendingTurn[]) {
  memory.set(scope, new Map(values.map(p => [p.key, p])));
  try { sessionStorage.setItem(prefix + scope, JSON.stringify(values)); }
  catch { /* Keep actor-scoped in-memory recovery even when storage disappears. */ }
  listeners.get(scope)?.forEach(listener => listener());
}

export function subscribeRecovery(scope: string, listener: () => void): () => void {
  const set = listeners.get(scope) ?? new Set<() => void>();
  listeners.set(scope, set);
  set.add(listener);
  return () => { set.delete(listener); };
}

export function pendingTurn(scope: string, key: string): PendingTurn | null {
  return read(scope).find(p => p.key === key) ?? null;
}

export function pendingFor(scope: string, conversation: string | null): PendingTurn | null {
  return read(scope).filter(p => p.conversationId === conversation || (conversation === null && !p.body.conversation_id)).at(-1) ?? null;
}

export function persistPending(scope: string, pending: PendingTurn): void {
  if (!validPending(pending)) throw new Error("Invalid local turn recovery envelope.");
  write(scope, [...read(scope).filter(p => p.key !== pending.key), pending]);
}

/** A realtime turn starts only after its exact recovery envelope survives storage. */
export function persistPendingDurably(scope: string, pending: PendingTurn): boolean {
  persistPending(scope, pending);
  try {
    return stored(prefix + scope).some(value => validPending(value) &&
      value.key === pending.key && JSON.stringify(value.body) === JSON.stringify(pending.body));
  } catch { return false; }
}

/** Only call with a proven terminal outcome for this exact request, never from missing storage/history. */
export function recordTerminal(scope: string, proof: VoiceTerminalProof): void {
  if (!validTerminal(proof)) return;
  const retained = pendingTurn(scope, proof.turnKey);
  const identity = retained?.voice?.conversationId ?? retained?.body.conversation_id ?? retained?.conversationId;
  if (identity && identity !== proof.conversationId) return;
  const all = new Map(stored(terminalPrefix + scope).filter(validTerminal).map(p => [p.turnKey, p]));
  for (const [id, value] of terminals.get(scope) ?? []) all.set(id, value);
  all.set(proof.turnKey, Object.freeze({ ...proof }));
  terminals.set(scope, all);
  try { sessionStorage.setItem(terminalPrefix + scope, JSON.stringify([...all.values()])); }
  catch { /* Terminal evidence remains in memory for this authenticated session. */ }
  removePending(scope, proof.turnKey);
}

export function removePending(scope: string, key: string): void {
  write(scope, read(scope).filter(p => p.key !== key));
}

export function clearPendingTurns(): void {
  memory.clear(); terminals.clear(); drafts.clear();
  try {
    Object.keys(sessionStorage).filter(key => key.startsWith(prefix) || key.startsWith(terminalPrefix) || key.startsWith(draftPrefix)).forEach(key => sessionStorage.removeItem(key));
  } catch { /* Storage may be unavailable. */ }
  listeners.forEach(set => set.forEach(listener => listener()));
}

export function recoveryDetails(body: unknown): ConflictDetails | null {
  const details = (body as { error?: { details?: unknown } } | null)?.error?.details;
  return turnConflictDetails(details) ? details : null;
}
