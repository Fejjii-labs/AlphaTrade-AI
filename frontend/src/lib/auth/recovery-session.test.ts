import { afterEach, beforeEach, expect, it, vi } from "vitest";

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); });
afterEach(() => { vi.restoreAllMocks(); });
const draftKey = "alphatrade:experiment-draft:original";
const screenKey = "alphatrade:screening:original";
const assignmentKey = "alphatrade:watcher-assignments:original";
function pending() {
  sessionStorage.setItem(draftKey, JSON.stringify({ idempotency_key: "original-draft", configuration: { exact: true } }));
  sessionStorage.setItem(screenKey, JSON.stringify({ request_id: "original-screen", trigger_end: "2026-10-10T12:00:00Z" }));
  sessionStorage.setItem(assignmentKey, JSON.stringify({ request_key: "original-assignment", assignments: [{ exact: true }] }));
}

it("restores the same authenticated recovery identity and requests when module generation resets on reload", async () => {
  const events = await import("./session-events");
  const session = await import("./recovery-session");
  events.sessionCleared(); session.bindRecoverySession("org", "user"); pending();
  const id = session.recoverySessionId("org", "user");
  const exactDraft = sessionStorage.getItem(draftKey), exactScreen = sessionStorage.getItem(screenKey);
  expect(events.sessionGeneration()).toBe(1);
  vi.resetModules();
  const restored = await import("./recovery-session");
  const reloadedEvents = await import("./session-events");
  expect(reloadedEvents.sessionGeneration()).toBe(0);
  restored.bindRecoverySession("org", "user");
  expect(restored.recoverySessionId("org", "user")).toBe(id);
  expect(sessionStorage.getItem(draftKey)).toBe(exactDraft);
  expect(sessionStorage.getItem(screenKey)).toBe(exactScreen);
});

it("token rotation preserves the recovery identity and both pending requests", async () => {
  const session = await import("./recovery-session");
  const tokens = await import("./session");
  session.bindRecoverySession("org", "user"); pending();
  const id = session.recoverySessionId("org", "user");
  tokens.setTokens("initial", "refresh"); tokens.setTokens("rotated", "rotated-refresh");
  expect(session.recoverySessionId("org", "user")).toBe(id);
  expect(sessionStorage.getItem(draftKey)).not.toBeNull(); expect(sessionStorage.getItem(screenKey)).not.toBeNull();
});

it("invalidates both namespaces with no panels mounted and rotates a same-account session", async () => {
  const session = await import("./recovery-session");
  const events = await import("./session-events");
  session.bindRecoverySession("org", "user"); pending();
  const previous = session.recoverySessionId("org", "user");
  events.sessionCleared();
  expect(session.recoverySessionId("org", "user")).toBeNull();
  expect(sessionStorage.getItem(draftKey)).toBeNull(); expect(sessionStorage.getItem(screenKey)).toBeNull();
  expect(sessionStorage.getItem(assignmentKey)).toBeNull();
  session.bindRecoverySession("org", "user");
  expect(session.recoverySessionId("org", "user")).not.toBe(previous);
});

it("rejects a stored foreign binding and removes its requests before binding the new account", async () => {
  const session = await import("./recovery-session");
  session.bindRecoverySession("old-org", "old-user"); pending();
  vi.resetModules();
  const restored = await import("./recovery-session");
  expect(restored.recoverySessionId("new-org", "new-user")).toBeNull();
  restored.bindRecoverySession("new-org", "new-user");
  expect(restored.recoverySessionId("old-org", "old-user")).toBeNull();
  expect(sessionStorage.getItem(draftKey)).toBeNull(); expect(sessionStorage.getItem(screenKey)).toBeNull();
  expect(sessionStorage.getItem(assignmentKey)).toBeNull();
});

it.each(["not-json", JSON.stringify({ id: "incomplete" })])("rejects malformed stored bindings: %s", async value => {
  sessionStorage.setItem("alphatrade:recovery-session", value); pending();
  const session = await import("./recovery-session");
  expect(session.recoverySession()).toBeNull(); session.bindRecoverySession("org", "user");
  expect(session.recoverySessionId("org", "user")).toBeTruthy();
  expect(sessionStorage.getItem(draftKey)).toBeNull(); expect(sessionStorage.getItem(screenKey)).toBeNull();
});

it("continues cleanup after one storage removal and one listener throw", async () => {
  const session = await import("./recovery-session");
  const events = await import("./session-events");
  const tokens = await import("./session");
  session.bindRecoverySession("org", "user"); tokens.setTokens("access", "refresh"); pending();
  const remove = Storage.prototype.removeItem;
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(function (this: Storage, key) {
    if (key === draftKey) throw new DOMException("Denied", "SecurityError");
    remove.call(this, key);
  });
  const last = vi.fn(); events.onSessionCleared(() => { throw new Error("listener failed"); }); events.onSessionCleared(last);
  expect(() => tokens.clearTokens()).not.toThrow();
  expect(last).toHaveBeenCalledOnce(); expect(tokens.getAccessToken()).toBeNull();
  expect(session.recoverySession()).toBeNull(); expect(sessionStorage.getItem(screenKey)).toBeNull();
  expect(sessionStorage.getItem(assignmentKey)).toBeNull();
  expect(document.cookie).not.toContain("alphatrade_session=1");
});

it("fences local identity and clears the marker when all storage access is denied", async () => {
  const session = await import("./recovery-session");
  const tokens = await import("./session");
  session.bindRecoverySession("org", "user"); tokens.setTokens("access", "refresh");
  vi.spyOn(window, "sessionStorage", "get").mockImplementation(() => { throw new DOMException("Denied", "SecurityError"); });
  expect(() => tokens.clearTokens()).not.toThrow();
  expect(tokens.getAccessToken()).toBeNull(); expect(tokens.getRefreshToken()).toBeNull();
  expect(session.recoverySession()).toBeNull(); expect(document.cookie).not.toContain("alphatrade_session=1");
});

it("does not expose a recovery scope when its session identity cannot be persisted", async () => {
  const session = await import("./recovery-session");
  const set = Storage.prototype.setItem;
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
    if (key === "alphatrade:recovery-session") throw new DOMException("Denied", "SecurityError");
    set.call(this, key, value);
  });
  expect(() => session.bindRecoverySession("org", "user")).not.toThrow();
  expect(session.recoverySessionId("org", "user")).toBeNull();
});
