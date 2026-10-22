import { expect, it, vi } from "vitest";
import Page from "./page";
const mocks = vi.hoisted(() => ({ redirect: vi.fn() }));
vi.mock("next/navigation", () => ({ redirect: mocks.redirect }));
it("opens the consolidated destination", () => {
  Page();
  expect(mocks.redirect).toHaveBeenCalledWith("/");
});
