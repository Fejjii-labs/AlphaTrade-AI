import { afterEach, describe, expect, it, vi } from "vitest";

describe("api client deployment config", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  it("uses configured NEXT_PUBLIC_API_URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.staging.example.com");
    const { appConfig } = await import("@/lib/config");
    expect(appConfig.apiBaseUrl).toBe("https://api.staging.example.com");
  });

  it("registers paper identity with authentication and an empty body", async () => {
    (await import("@/lib/auth/session")).setTokens("synthetic-owner-token");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => "{}" });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.execution.registerPaperAccount();
    expect(fetchMock).toHaveBeenCalledExactlyOnceWith(
      expect.stringContaining("/execution/accounts/paper"),
      expect.objectContaining({
        method: "POST", body: "{}",
        headers: expect.objectContaining({ Authorization: "Bearer synthetic-owner-token" }),
      }),
    );
    sessionStorage.clear();
  });

  it("lets the browser set multipart boundaries and keeps authentication", async () => {
    (await import("@/lib/auth/session")).setTokens("synthetic-access");
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => "{}" });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    const file = new File(["Synthetic note"], "note.txt", { type: "text/plain" });
    await api.knowledge.previewFile(file, "Note", "general_note");
    const [, request] = fetchMock.mock.calls[0];
    expect(request.body).toBeInstanceOf(FormData);
    expect(request.body.get("file")).toBeInstanceOf(File);
    expect(request.body.get("source_type")).toBe("general_note");
    expect(request.headers["Content-Type"]).toBeUndefined();
    expect(request.headers.Authorization).toBe("Bearer synthetic-access");
    sessionStorage.clear();
  });

  it("cookie mode enables credentials include on fetch", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_COOKIE_MODE", "true");
    const { usesCookieRefresh } = await import("@/lib/auth/session");
    expect(usesCookieRefresh()).toBe(true);

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ access_token: "new-access" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { apiFetch } = await import("@/lib/api/client");
    await apiFetch("/health", { auth: false });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/health"),
      expect.objectContaining({ credentials: "include" }),
    );
  });

  it("bearer mode uses same-origin credentials", async () => {
    vi.stubEnv("NEXT_PUBLIC_AUTH_COOKIE_MODE", "false");
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ app: "AlphaTrade AI" }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { apiFetch } = await import("@/lib/api/client");
    await apiFetch("/health", { auth: false });

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/health"),
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("deduplicates concurrent token refreshes (single-flight)", async () => {
    (await import("@/lib/auth/session")).setTokens("stale-token", "refresh-token");

    let refreshCalls = 0;
    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).includes("/auth/refresh")) {
        refreshCalls += 1;
        await new Promise((resolve) => setTimeout(resolve, 10));
        return {
          ok: true,
          status: 200,
          json: async () => ({ access_token: "fresh-token" }),
          text: async () => JSON.stringify({ access_token: "fresh-token" }),
        };
      }
      const token = sessionStorage.getItem("alphatrade_access_token");
      if (token === "fresh-token") {
        return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true }) };
      }
      return { ok: false, status: 401, text: async () => "" };
    });
    vi.stubGlobal("fetch", fetchMock);

    const { apiFetch } = await import("@/lib/api/client");
    await Promise.all([apiFetch("/proposals"), apiFetch("/positions")]);

    expect(refreshCalls).toBe(1);
    sessionStorage.clear();
  });
  it("discards a response arriving after logout instead of returning private data", async () => {
    let finish!: (response: unknown) => void;
    vi.stubGlobal("fetch", vi.fn(() => new Promise(resolve => { finish = resolve; })));
    const { apiFetch } = await import("@/lib/api/client");
    const { clearTokens } = await import("@/lib/auth/session");
    const pending = apiFetch("/private");
    const checked = expect(pending).rejects.toMatchObject({ name: "AbortError" });
    clearTokens();
    finish({ ok: true, status: 200, text: async () => JSON.stringify({ private: "old-tenant" }) });
    await checked;
  });
  it("a new identity refresh has its own flight and a late old refresh cannot overwrite tokens", async () => {
    const completions: ((response: unknown) => void)[] = [];
    (await import("@/lib/auth/session")).setTokens("old-stale", "old-refresh");
    vi.stubGlobal("fetch", vi.fn(async (url: string, options: RequestInit) => {
      if (url.includes("/auth/refresh")) return new Promise(resolve => completions.push(resolve));
      if ((options.headers as Record<string, string>).Authorization === "Bearer new-fresh")
        return { ok: true, status: 200, text: async () => "{}" };
      return { ok: false, status: 401, text: async () => "" };
    }));
    const { apiFetch } = await import("@/lib/api/client");
    const { clearTokens, setTokens } = await import("@/lib/auth/session");
    const oldRequest = apiFetch("/old-identity");
    const oldChecked = expect(oldRequest).rejects.toMatchObject({ name: "AbortError" });
    await vi.waitFor(() => expect(completions).toHaveLength(1));
    clearTokens();
    setTokens("new-stale", "new-refresh");
    const newRequest = apiFetch("/new-identity");
    await vi.waitFor(() => expect(completions).toHaveLength(2));
    completions[0]({ ok: true, json: async () => ({ access_token: "old-fresh" }) });
    await oldChecked;
    expect(sessionStorage.getItem("alphatrade_access_token")).toBe("new-stale");
    completions[1]({ ok: true, json: async () => ({ access_token: "new-fresh" }) });
    await newRequest;
    expect(sessionStorage.getItem("alphatrade_access_token")).toBe("new-fresh");
  });
});

describe("api client proactive access-token refresh (kill-switch 401s)", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.unstubAllGlobals();
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  const jwt = (expSeconds: number) =>
    `h.${btoa(JSON.stringify({ exp: expSeconds })).replace(/=+$/, "")}.s`;

  it("refreshes an expiring token before the kill-switch poll instead of taking a 401", async () => {
    (await import("@/lib/auth/session")).setTokens(jwt(Math.floor(Date.now() / 1000) - 5));
    const fresh = jwt(Math.floor(Date.now() / 1000) + 900);
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/auth/refresh")) {
        return { ok: true, status: 200, json: async () => ({ access_token: fresh }) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify({ active: false }) };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.risk.killSwitch();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toContain("/auth/refresh");
    const [url, req] = fetchMock.mock.calls[1] as unknown as [string, { headers: Record<string, string> }];
    expect(url).toContain("/risk/kill-switch");
    expect(req.headers.Authorization).toBe(`Bearer ${fresh}`);
  });

  it("does not refresh a token that is still valid", async () => {
    const valid = jwt(Math.floor(Date.now() / 1000) + 600);
    (await import("@/lib/auth/session")).setTokens(valid);
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, text: async () => "{}" });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.risk.killSwitch();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][0]).toContain("/risk/kill-switch");
  });

  it("still sends the request (401 fallback) when the proactive refresh fails", async () => {
    (await import("@/lib/auth/session")).setTokens(jwt(Math.floor(Date.now() / 1000) - 5));
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/auth/refresh")) return { ok: false, status: 401, json: async () => ({}) };
      return { ok: true, status: 200, text: async () => "{}" };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.risk.killSwitch();
    expect(fetchMock.mock.calls.map((c) => String(c[0]))).toEqual([
      expect.stringContaining("/auth/refresh"),
      expect.stringContaining("/risk/kill-switch"),
    ]);
  });

  it("decodes exp only from three-part tokens", async () => {
    const { accessTokenExpiresAtMs } = await import("@/lib/api/client");
    expect(accessTokenExpiresAtMs("synthetic-access")).toBeNull();
    expect(accessTokenExpiresAtMs(jwt(1000))).toBe(1_000_000);
  });

  it("storm guard: a skewed-clock client refreshes once, then trusts expires_in", async () => {
    // Server token already "expired" by the local clock (client runs 20 min ahead).
    const skewed = () => jwt(Math.floor(Date.now() / 1000) - 300);
    (await import("@/lib/auth/session")).setTokens(skewed());
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/auth/refresh")) {
        return { ok: true, status: 200, json: async () => ({ access_token: skewed() + "x", expires_in: 900 }) };
      }
      return { ok: true, status: 200, text: async () => "{}" };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    await api.risk.killSwitch();
    await api.risk.killSwitch();
    await api.risk.killSwitch();
    const refreshes = fetchMock.mock.calls.filter((c) => String(c[0]).includes("/auth/refresh"));
    expect(refreshes).toHaveLength(1);
  });

  it("schedules from expires_in against local receipt time, not exp", async () => {
    const { noteIssuedAccessToken, accessTokenNeedsRefresh } = await import("@/lib/api/client");
    const token = jwt(1); // exp far in the past by the local clock
    noteIssuedAccessToken(token, 900, 1_000_000);
    expect(accessTokenNeedsRefresh(token, 1_000_000 + 30_000)).toBe(false);
    expect(accessTokenNeedsRefresh(token, 1_000_000 + 600_000)).toBe(false);
    expect(accessTokenNeedsRefresh(token, 1_000_000 + 880_000)).toBe(true);
  });

  it("logout mid-refresh: the late refresh response does not resurrect the session", async () => {
    const session = await import("@/lib/auth/session");
    session.setTokens(jwt(Math.floor(Date.now() / 1000) - 5));
    let release!: () => void;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const fetchMock = vi.fn(async (url: string) => {
      if (url.includes("/auth/refresh")) {
        await gate;
        return { ok: true, status: 200, json: async () => ({ access_token: "late-token", expires_in: 900 }) };
      }
      return { ok: true, status: 401, text: async () => "{}" };
    });
    vi.stubGlobal("fetch", fetchMock);
    const { api } = await import("@/lib/api");
    const pending = api.risk.killSwitch().catch(() => undefined);
    await Promise.resolve();
    session.clearTokens();
    release();
    await pending;
    expect(session.getAccessToken()).toBeNull();
    expect(sessionStorage.getItem("alphatrade_access_token")).not.toBe("late-token");
  });
});

describe("api client resilience (timeouts, one GET retry, shared in-flight GETs)", () => {
  afterEach(() => {
    sessionStorage.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  const ok = (body: unknown) => ({ ok: true, status: 200, text: async () => JSON.stringify(body) });

  it("retries a GET once after a network failure", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("Failed to fetch")).mockResolvedValue(ok({ a: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch } = await import("@/lib/api/client");
    await expect(apiFetch("/thing", { auth: false })).resolves.toEqual({ a: 1 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("retries a GET once on 503 and gives up after the second failure", async () => {
    const unavailable = { ok: false, status: 503, statusText: "Service Unavailable", text: async () => "" };
    const fetchMock = vi.fn().mockResolvedValue(unavailable);
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch, ApiError } = await import("@/lib/api/client");
    await expect(apiFetch("/thing", { auth: false })).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("never retries a POST", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch, NetworkError } = await import("@/lib/api/client");
    await expect(apiFetch("/thing", { method: "POST", auth: false })).rejects.toBeInstanceOf(NetworkError);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not retry a 4xx", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "Not Found", text: async () => "" });
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch } = await import("@/lib/api/client");
    await expect(apiFetch("/thing", { auth: false })).rejects.toMatchObject({ status: 404 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("times out a hung request instead of waiting forever", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_url: string, init: RequestInit) =>
      new Promise((_resolve, reject) => {
        init.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch, NetworkError, REQUEST_TIMEOUT_MS } = await import("@/lib/api/client");
    const pending = apiFetch("/slow", { method: "POST", auth: false });
    const assertion = expect(pending).rejects.toBeInstanceOf(NetworkError);
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS.write + 10);
    await assertion;
    // A write with its own signal (agent turn) keeps its own budget.
    const owned = apiFetch("/turn", { method: "POST", auth: false, signal: new AbortController().signal });
    let settled = false;
    owned.catch(() => {}).finally(() => { settled = true; });
    await vi.advanceTimersByTimeAsync(REQUEST_TIMEOUT_MS.write * 3);
    expect(settled).toBe(false);
  });

  it("does not retry when the caller aborts", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn(() => {
      controller.abort();
      return Promise.reject(new DOMException("Aborted", "AbortError"));
    });
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch } = await import("@/lib/api/client");
    await expect(apiFetch("/thing", { auth: false, signal: controller.signal })).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("shares one request between identical concurrent GETs", async () => {
    const resolvers: Array<(value: unknown) => void> = [];
    const fetchMock = vi.fn(() => new Promise((r) => { resolvers.push(r); }));
    vi.stubGlobal("fetch", fetchMock);
    const { apiFetch } = await import("@/lib/api/client");
    const first = apiFetch("/shared", { auth: false });
    const second = apiFetch("/shared", { auth: false });
    const other = apiFetch("/shared", { auth: false, query: { x: 1 } });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      expect.stringMatching(/\/shared$/),
      expect.stringMatching(/\/shared\?x=1$/),
    ]);
    resolvers[0](ok({ n: 1 }));
    resolvers[1](ok({ n: 2 }));
    await expect(first).resolves.toEqual({ n: 1 });
    await expect(second).resolves.toEqual({ n: 1 });
    await expect(other).resolves.toEqual({ n: 2 });
    // Once settled, the next call fetches again (no stale cache).
    const again = apiFetch("/shared", { auth: false });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(3));
    resolvers[2](ok({ n: 3 }));
    await expect(again).resolves.toEqual({ n: 3 });
  });

  it("turns errors into short plain text", async () => {
    const { ApiError, NetworkError, friendlyError } = await import("@/lib/api/client");
    expect(friendlyError(new TypeError("Failed to fetch"))).toBe("Couldn't load.");
    expect(friendlyError(new NetworkError("timeout"))).toBe("Couldn't load.");
    expect(friendlyError(new ApiError("Internal Server Error", 500, null))).toBe("Couldn't load.");
    expect(friendlyError(new ApiError("Forbidden", 403, null))).toBe("No access.");
    expect(friendlyError(new ApiError("Strategy name already exists.", 409, null))).toBe(
      "Strategy name already exists.",
    );
    expect(friendlyError(new Error("Trade not found in this organization"))).toBe(
      "Trade not found in this organization",
    );
    expect(friendlyError(new Error("Failed to fetch"), "Couldn't save.")).toBe("Couldn't save.");
  });
});
