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
