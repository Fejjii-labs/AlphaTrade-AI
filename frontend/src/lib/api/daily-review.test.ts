import { expect, it, vi } from "vitest";

import { dailyReviewFixture } from "@/test/pilot-fixtures";
const apiFetch = vi.fn().mockResolvedValue(dailyReviewFixture);
vi.mock("@/lib/api/client", () => ({ apiFetch: (...args: unknown[]) => apiFetch(...args) }));

it("requests an authenticated review with date and timezone", async () => {
  const { api } = await import("@/lib/api");
  await api.dashboard.dailyReview({ date: "2026-10-25", timezone: "Europe/Berlin" });
  expect(apiFetch).toHaveBeenCalledWith("/dashboard/daily-review", {
    method: "GET", signal: undefined, headers: undefined, query: { date: "2026-10-25", timezone: "Europe/Berlin" }, auth: true,
  });
});
