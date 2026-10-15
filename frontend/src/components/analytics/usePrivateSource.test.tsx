import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PrivateQueryProvider } from "@/components/query/PrivateQueryProvider";
import { usePrivateSource } from "./usePrivateSource";
import { ApiError } from "@/lib/api/client";
import { retryPrivateRead } from "@/lib/private-query-client";
import { sessionCleared } from "@/lib/auth/session-events";

afterEach(cleanup);
let identity = { organizationId: "org-a", userId: "user-a" };
function wrapper({ children }: { children: React.ReactNode }) {
  return <PrivateQueryProvider {...identity}>{children}</PrivateQueryProvider>;
}

describe("private source Query pilot", () => {
  it("deduplicates identical scoped reads and preserves the original evidence timestamp", async () => {
    identity = { organizationId: "org-a", userId: "user-a" };
    const fetcher = vi.fn().mockResolvedValue({ generated_at: "2026-01-01T00:00:00Z", evidence: "recorded" });
    const { result, rerender } = renderHook(() => ({
      one: usePrivateSource<{ generated_at: string; evidence: string }>(true, "/history", "normalized", fetcher),
      two: usePrivateSource<{ generated_at: string; evidence: string }>(true, "/history", "normalized", fetcher),
    }), { wrapper });
    await waitFor(() => expect(result.current.one.loading).toBe(false));
    expect(fetcher).toHaveBeenCalledTimes(1);
    rerender();
    expect(result.current.two.source?.data?.generated_at).toBe("2026-01-01T00:00:00Z");
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("runs slow and failed widgets independently with a narrow retry", async () => {
    let finish!: (value: unknown) => void;
    const slow = vi.fn(() => new Promise(resolve => { finish = resolve; }));
    const failed = vi.fn().mockRejectedValue(new ApiError("Validation", 422, null));
    const ready = vi.fn().mockResolvedValue({ value: "visible" });
    const { result } = renderHook(() => ({
      slow: usePrivateSource(true, "/slow", "all", slow),
      failed: usePrivateSource(true, "/failed", "all", failed),
      ready: usePrivateSource(true, "/ready", "all", ready),
    }), { wrapper });
    await waitFor(() => expect(result.current.ready.source?.available).toBe(true));
    expect(result.current.slow.loading).toBe(true);
    await waitFor(() => expect(result.current.failed.source?.error).toBe("Validation"));
    expect(failed).toHaveBeenCalledTimes(1);
    failed.mockResolvedValue({ value: "recovered" });
    await act(async () => { await result.current.failed.reload(); });
    await waitFor(() => expect(result.current.failed.source?.available).toBe(true));
    expect(ready).toHaveBeenCalledTimes(1);
    await act(async () => finish({ value: "late" }));
    await waitFor(() => expect(result.current.slow.loading).toBe(false));
  });
  it("retries one transient failure but never retries successful unavailable evidence", async () => {
    const transient = vi.fn().mockRejectedValueOnce(new ApiError("Unavailable", 503, null)).mockResolvedValue({ value: "ready" });
    const domain = vi.fn().mockResolvedValue({ availability: "unavailable", generated_at: "2026-01-01T00:00:00Z" });
    const { result } = renderHook(() => ({
      transient: usePrivateSource(true, "/transient", "all", transient),
      domain: usePrivateSource<{ availability: string }>(true, "/domain", "all", domain),
    }), { wrapper });
    await waitFor(() => expect(result.current.transient.loading).toBe(false));
    expect(transient).toHaveBeenCalledTimes(2);
    expect(domain).toHaveBeenCalledTimes(1);
    expect(result.current.domain.source?.data?.availability).toBe("unavailable");
  });
  it.each([0, 400, 401, 403, 404, 409, 422])("does not automatically retry contract/auth/client errors: %s", (status) => {
    expect(retryPrivateRead(0, new ApiError("Synthetic", status, null))).toBe(false);
    expect(retryPrivateRead(1, new ApiError("Synthetic", 503, null))).toBe(false);
    expect(retryPrivateRead(0, new DOMException("Aborted", "AbortError"))).toBe(false);
  });
  it("aborts old identity reads and prevents a late result from entering the new tenant cache", async () => {
    let finish!: (value: unknown) => void;
    let oldSignal!: AbortSignal;
    identity = { organizationId: "org-a", userId: "user-a" };
    const fetcher = vi.fn((signal: AbortSignal) => {
      if (identity.userId === "user-a") { oldSignal = signal; return new Promise(resolve => { finish = resolve; }); }
      return Promise.resolve({ private: "tenant-b" });
    });
    const { result, rerender } = renderHook(() => usePrivateSource(true, "/private", "all", fetcher), { wrapper });
    identity = { organizationId: "org-b", userId: "user-b" };
    rerender();
    await waitFor(() => expect(result.current.source?.data).toEqual({ private: "tenant-b" }));
    expect(oldSignal.aborted).toBe(true);
    await act(async () => finish({ private: "tenant-a-late" }));
    expect(result.current.source?.data).toEqual({ private: "tenant-b" });
  });
  it("cancels private requests synchronously when the session is cleared", async () => {
    let signal!: AbortSignal;
    const fetcher = vi.fn((next: AbortSignal) => { signal = next; return new Promise(() => {}); });
    renderHook(() => usePrivateSource(true, "/private", "all", fetcher), { wrapper });
    act(() => sessionCleared());
    expect(signal.aborted).toBe(true);
  });
  it("cancels filter changes and uses the cached exact-filter response on return", async () => {
    const fetcher = vi.fn().mockResolvedValue({ evidence: "exact-filter" });
    const { result, rerender } = renderHook(({ filters }) => usePrivateSource(true, "/private", filters, fetcher),
      { wrapper, initialProps: { filters: "first" } });
    await waitFor(() => expect(result.current.loading).toBe(false));
    rerender({ filters: "second" });
    await waitFor(() => expect(result.current.loading).toBe(false));
    rerender({ filters: "first" });
    expect(result.current.source?.data).toEqual({ evidence: "exact-filter" });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it("retains cached evidence and exposes a failed refresh without changing its timestamp", async () => {
    const fetcher = vi.fn().mockResolvedValue({ generated_at: "2026-01-01T00:00:00Z" });
    const { result } = renderHook(() => usePrivateSource(true, "/evidence", "all", fetcher), { wrapper });
    await waitFor(() => expect(result.current.source?.available).toBe(true));
    fetcher.mockRejectedValue(new ApiError("Refresh denied", 403, null));
    await act(async () => { await result.current.reload(); });
    expect(result.current.source?.data).toEqual({ generated_at: "2026-01-01T00:00:00Z" });
    await waitFor(() => expect(result.current.refreshError).toBe("Refresh denied"));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});
