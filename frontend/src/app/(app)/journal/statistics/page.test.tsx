import { expect, it, vi } from "vitest";
import Page from "./page";
const mocks = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));
it("opens the Stats tab of Trades", () => {
  Page();
  expect(mocks.redirect).toHaveBeenCalledWith("/journal?tab=stats");
});
