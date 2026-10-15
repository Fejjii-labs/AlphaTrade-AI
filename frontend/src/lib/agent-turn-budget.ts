/** Shared end-to-end Agent turn budget, including attachment preparation. */
export const AGENT_TURN_TIMEOUT_MS = 360_000;

/** Bound the caller's wait even if a transport ignores AbortSignal. Late resolution can still be observed. */
export function waitForAgent<T>(promise: Promise<T>, signal: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const cancelled = () => { cleanup(); reject(new DOMException("Aborted", "AbortError")); };
    const cleanup = () => signal.removeEventListener("abort", cancelled);
    promise.then(value => { cleanup(); resolve(value); }, error => { cleanup(); reject(error); });
    if (signal.aborted) cancelled();
    else signal.addEventListener("abort", cancelled, { once: true });
  });
}
