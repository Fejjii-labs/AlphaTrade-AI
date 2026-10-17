import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import JournalPage from "@/app/(app)/journal/page";
import { api } from "@/lib/api";
import { readNativeActivity } from "@/lib/api/blofin-activity";
import { savedEntries } from "@/lib/api/saved-entries";
import { activityOrganization, activityPage } from "@/test/native-activity-fixtures";

let identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
const params = new URLSearchParams();
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/blofin-activity", () => ({ readNativeActivity: vi.fn() }));
vi.mock("next/navigation", () => ({ useSearchParams: () => params, usePathname: () => "/journal", useRouter: () => ({ replace: vi.fn() }) }));

beforeEach(() => {
  identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
  for (const key of [...params.keys()]) params.delete(key);
  vi.mocked(readNativeActivity).mockReset().mockResolvedValue(activityPage());
  vi.spyOn(savedEntries, "list").mockResolvedValue({ items: [], total: 0 });
  vi.spyOn(api.journal, "listTrades").mockResolvedValue({ items: [{ id: "local-simulator", symbol: "SIMULATED", timeframe: "1h", direction: "long", status: "closed", result: "win", source: "paper_execution", exchange: "INTERNAL" }], total: 1, limit: 50, offset: 0 });
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("mounts compact native history on the actual Journal page, without simulator rows or statistics", async () => {
  render(<JournalPage />);
  await screen.findByTestId("native-activity-row");
  expect(screen.getByText("Journal & Knowledge")).toBeInTheDocument();
  expect(screen.queryByTestId("native-activity-statistics")).not.toBeInTheDocument();
  expect(screen.queryByText(/SIMULATED/)).not.toBeInTheDocument();
});

it("clears Journal history when authenticated user changes", async () => {
  const { rerender } = render(<JournalPage />);
  await screen.findByTestId("native-activity-row");
  identity = { ...identity, user: { id: "reader-2" } };
  vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ items: [] }));
  rerender(<JournalPage />);
  expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
  await screen.findByText(/No stored fills/);
});

it("does not mount or fetch native activity in the Knowledge view", async () => {
  params.set("tab", "knowledge");
  vi.spyOn(api.knowledge, "listDocuments").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  render(<JournalPage />);
  await screen.findByText("No matching entries.");
  expect(readNativeActivity).not.toHaveBeenCalled();
});
