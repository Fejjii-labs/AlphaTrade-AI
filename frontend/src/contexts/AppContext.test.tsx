import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { HealthResponse, KillSwitchStatus, ProviderStatusResponse } from "@/lib/api/types";

import {
  AppProvider,
  POSTURE_REFRESH_INTERVAL_MS,
  useAppContext,
  useSafetyPosture,
} from "./AppContext";

vi.mock("@/lib/api", () => ({
  api: {
    health: { get: vi.fn() },
    providers: { status: vi.fn() },
    risk: {
      killSwitch: vi.fn(),
      activateKillSwitch: vi.fn(),
      deactivateKillSwitch: vi.fn(),
    },
  },
  ApiError: class MockApiError extends Error {},
}));

vi.mock("@/lib/auth/session", () => ({
  isAuthenticated: () => true,
}));

const healthOk: HealthResponse = {
  status: "ok",
  app: "alphatrade",
  version: "0.1.0",
  environment: "test",
  execution_mode: "paper",
  real_trading_enabled: false,
  must_verify_email: false,
  timestamp: "2026-07-28T00:00:00Z",
};

const providersOk: ProviderStatusResponse = {
  generated_at: "2026-07-28T00:00:00Z",
  providers: [],
};

const killSwitchOk: KillSwitchStatus = {
  organization_id: "org-1",
  active: false,
  reason: null,
  activated_by: null,
  activated_at: null,
  deactivated_by: null,
  deactivated_at: null,
  version: 1,
  scope: "organization",
  global_active: false,
  execution_blocked: false,
};

function mockHappyPath() {
  vi.mocked(api.health.get).mockResolvedValue(healthOk);
  vi.mocked(api.providers.status).mockResolvedValue(providersOk);
  vi.mocked(api.risk.killSwitch).mockResolvedValue(killSwitchOk);
}

function renderAppContext() {
  return renderHook(() => ({ ctx: useAppContext(), posture: useSafetyPosture() }), {
    wrapper: AppProvider,
  });
}

async function flushAsync() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("AppContext posture lifecycle (FP2-105)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockHappyPath();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("fetches health, providers, and kill switch exactly once on mount", async () => {
    const { result } = renderAppContext();
    await flushAsync();

    expect(api.health.get).toHaveBeenCalledTimes(1);
    expect(api.providers.status).toHaveBeenCalledTimes(1);
    expect(api.risk.killSwitch).toHaveBeenCalledTimes(1);
    expect(result.current.ctx.health).toEqual(healthOk);
    expect(result.current.posture.postureKnown).toBe(true);
    expect(result.current.posture.executionMode).toBe("paper");
    expect(result.current.posture.realTradingEnabled).toBe(false);
  });

  it("refreshes health and kill switch on the ~60s interval without toggling loading", async () => {
    const { result } = renderAppContext();
    await flushAsync();
    expect(api.health.get).toHaveBeenCalledTimes(1);
    expect(result.current.ctx.loading).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });

    expect(api.health.get).toHaveBeenCalledTimes(2);
    expect(api.risk.killSwitch).toHaveBeenCalledTimes(2);
    expect(result.current.ctx.loading).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    expect(api.health.get).toHaveBeenCalledTimes(3);
  });

  it("refreshes posture when the window regains focus or becomes visible", async () => {
    renderAppContext();
    await flushAsync();
    expect(api.health.get).toHaveBeenCalledTimes(1);

    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
    });
    expect(api.health.get).toHaveBeenCalledTimes(2);
    await flushAsync();

    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
      await Promise.resolve();
    });
    expect(api.health.get).toHaveBeenCalledTimes(3);
  });

  it("does not refresh while the document is hidden", async () => {
    renderAppContext();
    await flushAsync();
    expect(api.health.get).toHaveBeenCalledTimes(1);

    const visibilityDescriptor = Object.getOwnPropertyDescriptor(
      Document.prototype,
      "visibilityState",
    );
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => "hidden",
    });
    try {
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
        await Promise.resolve();
      });
      expect(api.health.get).toHaveBeenCalledTimes(1);
    } finally {
      if (visibilityDescriptor) {
        Object.defineProperty(document, "visibilityState", visibilityDescriptor);
      } else {
        delete (document as unknown as Record<string, unknown>).visibilityState;
      }
    }
  });

  it("skips interval refreshes while the tab is hidden and resumes when visible", async () => {
    renderAppContext();
    await flushAsync();
    expect(api.risk.killSwitch).toHaveBeenCalledTimes(1);

    const visibilityDescriptor = Object.getOwnPropertyDescriptor(
      Document.prototype,
      "visibilityState",
    );
    let visibility: DocumentVisibilityState = "hidden";
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      get: () => visibility,
    });
    try {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS * 3);
      });
      expect(api.health.get).toHaveBeenCalledTimes(1);
      expect(api.risk.killSwitch).toHaveBeenCalledTimes(1);

      visibility = "visible";
      await act(async () => {
        await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
      });
      expect(api.risk.killSwitch).toHaveBeenCalledTimes(2);
    } finally {
      if (visibilityDescriptor) {
        Object.defineProperty(document, "visibilityState", visibilityDescriptor);
      } else {
        delete (document as unknown as Record<string, unknown>).visibilityState;
      }
    }
  });

  it("never overlaps background requests for the same source, while siblings keep refreshing", async () => {
    const { result } = renderAppContext();
    await flushAsync();
    expect(api.health.get).toHaveBeenCalledTimes(1);
    expect(api.providers.status).toHaveBeenCalledTimes(1);

    let resolveSlow: (value: HealthResponse) => void = () => undefined;
    vi.mocked(api.health.get).mockImplementationOnce(
      () =>
        new Promise<HealthResponse>((resolve) => {
          resolveSlow = resolve;
        }),
    );

    // First tick starts a health request that stays in flight…
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    expect(api.health.get).toHaveBeenCalledTimes(2);
    expect(api.providers.status).toHaveBeenCalledTimes(2);

    // …so further ticks and focus events must not stack another /health
    // request, but the healthy sibling sources keep refreshing on schedule.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await Promise.resolve();
    });
    await flushAsync();
    expect(api.health.get).toHaveBeenCalledTimes(2);
    expect(api.providers.status).toHaveBeenCalledTimes(4);
    expect(api.risk.killSwitch).toHaveBeenCalledTimes(4);

    await act(async () => {
      resolveSlow(healthOk);
      await Promise.resolve();
    });
    await flushAsync();
    expect(result.current.ctx.health).toEqual(healthOk);
  });

  it("ignores a stale response that resolves after a newer refresh", async () => {
    const { result } = renderAppContext();
    await flushAsync();

    const staleHealth: HealthResponse = { ...healthOk, version: "stale" };
    let resolveStale: (value: HealthResponse) => void = () => undefined;
    vi.mocked(api.health.get).mockImplementationOnce(
      () =>
        new Promise<HealthResponse>((resolve) => {
          resolveStale = resolve;
        }),
    );

    // Older manual refresh hangs…
    act(() => {
      void result.current.ctx.refreshStatus();
    });
    expect(api.health.get).toHaveBeenCalledTimes(2);

    // …a newer manual refresh completes with fresh data…
    vi.mocked(api.health.get).mockResolvedValueOnce({ ...healthOk, version: "newest" });
    await act(async () => {
      await result.current.ctx.refreshStatus();
    });
    await flushAsync();
    expect(result.current.ctx.health?.version).toBe("newest");

    // …then the stale response must be discarded.
    await act(async () => {
      resolveStale(staleHealth);
      await Promise.resolve();
    });
    await flushAsync();
    expect(result.current.ctx.health?.version).toBe("newest");
  });

  it("health-only failure makes posture unverified but keeps the provider result honest", async () => {
    const { result } = renderAppContext();
    await flushAsync();
    expect(result.current.posture.postureKnown).toBe(true);

    vi.mocked(api.health.get).mockRejectedValueOnce(new Error("health unreachable"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();

    expect(result.current.ctx.health).toBeNull();
    expect(result.current.ctx.error).toBe("health unreachable");
    expect(result.current.posture.postureKnown).toBe(false);
    expect(result.current.posture.executionMode).toBeNull();
    expect(result.current.posture.realTradingEnabled).toBeNull();
    // The successfully refreshed provider result is not erased by the
    // sibling failure.
    expect(result.current.ctx.providers).toEqual(providersOk);
  });

  it("provider-only failure sets providers unknown but preserves the verified health posture", async () => {
    const { result } = renderAppContext();
    await flushAsync();
    expect(result.current.ctx.providers).toEqual(providersOk);

    vi.mocked(api.providers.status).mockRejectedValueOnce(new Error("providers down"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();

    expect(result.current.ctx.providers).toBeNull();
    expect(result.current.ctx.health).toEqual(healthOk);
    expect(result.current.ctx.error).toBeNull();
    expect(result.current.posture.postureKnown).toBe(true);
    expect(result.current.posture.executionMode).toBe("paper");
    expect(result.current.posture.realTradingEnabled).toBe(false);
  });

  it("keeps the authoritative kill-switch state with an explicit error when only its read fails", async () => {
    const { result } = renderAppContext();
    await flushAsync();
    expect(result.current.ctx.killSwitchStatus).toEqual(killSwitchOk);

    vi.mocked(api.risk.killSwitch).mockRejectedValueOnce(new Error("kill switch unreachable"));

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();

    // Never invent an inactive kill switch; last known state stays + error.
    expect(result.current.ctx.killSwitchStatus).toEqual(killSwitchOk);
    expect(result.current.ctx.killSwitchError).toBe("kill switch unreachable");
  });

  it("recovers to verified posture on the next successful refresh", async () => {
    const { result } = renderAppContext();
    await flushAsync();

    vi.mocked(api.health.get).mockRejectedValueOnce(new Error("blip"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();
    expect(result.current.posture.postureKnown).toBe(false);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();
    expect(result.current.posture.postureKnown).toBe(true);
    expect(result.current.ctx.error).toBeNull();
  });
});

describe("AppContext posture-source isolation", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockHappyPath();
  });

  afterEach(() => {
    cleanup();
    vi.useRealTimers();
    vi.clearAllMocks();
  });

  it("a hanging /health request does not delay kill-switch or provider results", async () => {
    let resolveHealth: (value: HealthResponse) => void = () => undefined;
    vi.mocked(api.health.get).mockImplementationOnce(
      () =>
        new Promise<HealthResponse>((resolve) => {
          resolveHealth = resolve;
        }),
    );

    const { result } = renderAppContext();
    await flushAsync();

    // Kill switch and providers land while health is still pending.
    expect(result.current.ctx.killSwitchStatus).toEqual(killSwitchOk);
    expect(result.current.ctx.providers).toEqual(providersOk);
    expect(result.current.ctx.health).toBeNull();
    expect(result.current.posture.postureKnown).toBe(false);

    await act(async () => {
      resolveHealth(healthOk);
      await Promise.resolve();
    });
    await flushAsync();
    expect(result.current.ctx.health).toEqual(healthOk);
    expect(result.current.posture.postureKnown).toBe(true);
    expect(result.current.ctx.loading).toBe(false);
  });

  it("a hanging provider-status request does not delay health or kill-switch results", async () => {
    let resolveProviders: (value: ProviderStatusResponse) => void = () => undefined;
    vi.mocked(api.providers.status).mockImplementationOnce(
      () =>
        new Promise<ProviderStatusResponse>((resolve) => {
          resolveProviders = resolve;
        }),
    );

    const { result } = renderAppContext();
    await flushAsync();

    expect(result.current.ctx.health).toEqual(healthOk);
    expect(result.current.posture.postureKnown).toBe(true);
    expect(result.current.ctx.killSwitchStatus).toEqual(killSwitchOk);
    expect(result.current.ctx.providers).toBeNull();

    await act(async () => {
      resolveProviders(providersOk);
      await Promise.resolve();
    });
    await flushAsync();
    expect(result.current.ctx.providers).toEqual(providersOk);
  });

  it("failed sources recover independently on the next refresh", async () => {
    const { result } = renderAppContext();
    await flushAsync();

    // Tick 1: providers fail while health keeps succeeding.
    vi.mocked(api.providers.status).mockRejectedValueOnce(new Error("providers down"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();
    expect(result.current.ctx.providers).toBeNull();
    expect(result.current.ctx.health).toEqual(healthOk);

    // Tick 2: providers recover on their own; health stayed verified all along.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();
    expect(result.current.ctx.providers).toEqual(providersOk);
    expect(result.current.ctx.health).toEqual(healthOk);
    expect(result.current.posture.postureKnown).toBe(true);
  });

  it("kill-switch mutation authority survives a stale background read and stays unblocked", async () => {
    const { result } = renderAppContext();
    await flushAsync();
    expect(result.current.ctx.killSwitchStatus).toEqual(killSwitchOk);

    // A background kill-switch read hangs…
    let resolveStaleRead: (value: KillSwitchStatus) => void = () => undefined;
    vi.mocked(api.risk.killSwitch).mockImplementationOnce(
      () =>
        new Promise<KillSwitchStatus>((resolve) => {
          resolveStaleRead = resolve;
        }),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });

    // …while a mutation completes with the authoritative state.
    const activated: KillSwitchStatus = {
      ...killSwitchOk,
      active: true,
      execution_blocked: true,
      version: 2,
    };
    vi.mocked(api.risk.activateKillSwitch).mockResolvedValueOnce(activated);
    await act(async () => {
      await result.current.ctx.setKillSwitchActive(true, "isolation test");
    });
    expect(result.current.ctx.killSwitchStatus).toEqual(activated);

    // The pre-mutation read resolving late must be discarded.
    await act(async () => {
      resolveStaleRead(killSwitchOk);
      await Promise.resolve();
    });
    await flushAsync();
    expect(result.current.ctx.killSwitchStatus).toEqual(activated);

    // Background kill-switch refreshes remain unblocked after the mutation
    // invalidated the hanging read.
    vi.mocked(api.risk.killSwitch).mockResolvedValueOnce(activated);
    const readsBefore = vi.mocked(api.risk.killSwitch).mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POSTURE_REFRESH_INTERVAL_MS);
    });
    await flushAsync();
    expect(vi.mocked(api.risk.killSwitch).mock.calls.length).toBe(readsBefore + 1);
    expect(result.current.ctx.killSwitchStatus).toEqual(activated);
  });
});
