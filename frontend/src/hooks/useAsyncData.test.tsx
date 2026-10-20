import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useAsyncData } from "./useAsyncData";

type Deferred<T> = {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: Error) => void;
};

function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("useAsyncData", () => {
  it("loads data on mount", async () => {
    const { result } = renderHook(() => useAsyncData(async () => "loaded", []));

    expect(result.current.loading).toBe(true);
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.data).toBe("loaded");
    expect(result.current.error).toBeNull();
  });

  it("clears previously loaded data when a reload fails (FP2-101)", async () => {
    let shouldFail = false;
    const { result } = renderHook(() =>
      useAsyncData(async () => {
        if (shouldFail) throw new Error("backend down");
        return "fresh";
      }, []),
    );
    await waitFor(() => expect(result.current.data).toBe("fresh"));

    shouldFail = true;
    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.error).toBe("backend down");
    expect(result.current.data).toBeNull();
    expect(result.current.loading).toBe(false);
  });

  it("recovers data and clears the error on a successful retry", async () => {
    let shouldFail = true;
    const { result } = renderHook(() =>
      useAsyncData(async () => {
        if (shouldFail) throw new Error("backend down");
        return "recovered";
      }, []),
    );
    await waitFor(() => expect(result.current.error).toBe("backend down"));

    shouldFail = false;
    await act(async () => {
      await result.current.reload();
    });

    expect(result.current.data).toBe("recovered");
    expect(result.current.error).toBeNull();
  });

  it("ignores a stale response that resolves after a newer request", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const pending = [first, second];
    let calls = 0;

    const { result } = renderHook(() => useAsyncData(() => pending[calls++].promise, []));

    act(() => {
      void result.current.reload();
    });

    await act(async () => {
      second.resolve("newer");
      await second.promise;
    });
    await waitFor(() => expect(result.current.data).toBe("newer"));

    await act(async () => {
      first.resolve("older");
      await first.promise;
    });

    expect(result.current.data).toBe("newer");
    expect(result.current.loading).toBe(false);
  });

  it("ignores a stale failure that resolves after a newer successful request", async () => {
    const first = deferred<string>();
    const second = deferred<string>();
    const pending = [first, second];
    let calls = 0;

    const { result } = renderHook(() => useAsyncData(() => pending[calls++].promise, []));

    act(() => {
      void result.current.reload();
    });

    await act(async () => {
      second.resolve("newer");
      await second.promise;
    });
    await waitFor(() => expect(result.current.data).toBe("newer"));

    await act(async () => {
      first.reject(new Error("stale failure"));
      await first.promise.catch(() => undefined);
    });

    expect(result.current.data).toBe("newer");
    expect(result.current.error).toBeNull();
  });

  it("does not apply a load that resolves after unmount", async () => {
    const pending = deferred<string>();
    const { unmount } = renderHook(() => useAsyncData(() => pending.promise, []));

    unmount();
    await act(async () => {
      pending.resolve("late");
      await pending.promise;
    });
  });

  it("reloads when deps change and never renders the previous deps' data beside an error", async () => {
    let mode: "ok" | "fail" = "ok";
    const { result, rerender } = renderHook(
      ({ dep }: { dep: string }) =>
        useAsyncData(async () => {
          if (mode === "fail") throw new Error(`failed for ${dep}`);
          return `data for ${dep}`;
        }, [dep]),
      { initialProps: { dep: "a" } },
    );
    await waitFor(() => expect(result.current.data).toBe("data for a"));

    mode = "fail";
    rerender({ dep: "b" });

    await waitFor(() => expect(result.current.error).toBe("failed for b"));
    expect(result.current.data).toBeNull();
  });
});
