import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { useDashboardSources } from "./useDashboardSources";

afterEach(cleanup);

function deferred<T>() {
  let resolve: (value: T) => void = () => {};
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

it("drops a response from an older load once a newer load has started", async () => {
  const first = deferred<string>();
  const second = deferred<string>();
  const calls = [first, second];
  let index = 0;
  const loaders = { value: () => calls[index++].promise };
  const { result } = renderHook(() => useDashboardSources(loaders));
  await waitFor(() => expect(index).toBe(1));
  expect(result.current.data.value.pending).toBe(true);

  let reload: Promise<void> = Promise.resolve();
  act(() => {
    reload = result.current.reload();
  });
  await act(async () => {
    second.resolve("new");
    await reload;
  });
  expect(result.current.data.value.data).toBe("new");

  await act(async () => {
    first.resolve("old");
    await first.promise;
  });
  // The late, older response must not overwrite the newer one.
  expect(result.current.data.value.data).toBe("new");
});

it("does not commit results after unmount", async () => {
  const pending = deferred<string>();
  const loaders = { value: () => pending.promise };
  const { result, unmount } = renderHook(() => useDashboardSources(loaders));
  const before = result.current.data.value;
  unmount();
  await act(async () => {
    pending.resolve("late");
    await pending.promise;
  });
  expect(before.pending).toBe(true);
  expect(result.current.data.value.data).toBeNull();
});

it("keeps pending sources pending (not failed) until they settle", async () => {
  const slow = deferred<string>();
  const loaders = {
    slow: () => slow.promise,
    broken: () => Promise.reject(new Error("down")),
  };
  const { result } = renderHook(() => useDashboardSources(loaders));
  await waitFor(() => expect(result.current.data.broken.pending).toBeFalsy());
  expect(result.current.data.broken.available).toBe(false);
  expect(result.current.data.slow.pending).toBe(true);
  expect(result.current.data.slow.error).toBeNull();
  expect(result.current.loading).toBe(true);
  await act(async () => {
    slow.resolve("ok");
    await slow.promise;
  });
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.data.slow.data).toBe("ok");
});
