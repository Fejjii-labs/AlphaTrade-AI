import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetch = vi.fn().mockResolvedValue({});

vi.mock("@/lib/api/client", () => ({
  apiFetch: (...args: unknown[]) => apiFetch(...args),
}));

describe("api.auth.requestVerifyEmail", () => {
  afterEach(() => {
    apiFetch.mockClear();
  });

  it("sends the signed-in session so the server can use the profile email", async () => {
    const { api } = await import("@/lib/api");
    await api.auth.requestVerifyEmail();
    expect(apiFetch).toHaveBeenCalledWith("/auth/verify-email/request", {
      method: "POST",
      body: "{}",
      auth: true,
    });
  });
});
