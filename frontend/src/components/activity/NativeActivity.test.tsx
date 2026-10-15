import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api/client";
import { sessionCleared } from "@/lib/auth/session-events";
import { readNativeActivity, type NativeActivityPage } from "@/lib/api/blofin-activity";
import { activityOrganization, activityPage, nativeFill } from "@/test/native-activity-fixtures";
import { NativeActivity } from "./NativeActivity";

let identity: { user: { id: string } | null; organization: { id: string } | null };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/blofin-activity", () => ({ readNativeActivity: vi.fn() }));

beforeEach(() => {
  identity = { user: { id: "reader-1" }, organization: { id: activityOrganization } };
  vi.mocked(readNativeActivity).mockReset().mockResolvedValue(activityPage());
});
afterEach(cleanup);

describe("stored native activity", () => {
  it("renders exact contracts/prices, unknown fees/PnL and details with a working return control", async () => {
    const item = nativeFill({ origin: "alphatrade_matched", command_id: "22222222-2222-4222-8222-222222222222" });
    vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ items: [item] }));
    render(<NativeActivity statistics />);
    const row = await screen.findByTestId("native-activity-row");
    const summary = row.querySelector("summary")!;
    expect(summary).toHaveTextContent("0.123456789123456789 contracts");
    expect(summary).toHaveTextContent("67000.123456789123456789");
    expect(summary.textContent).not.toContain(item.native_id);
    expect(summary.textContent).not.toContain(item.command_id!);
    expect(summary.textContent).not.toContain("http");
    fireEvent.click(summary);
    (row as HTMLDetailsElement).open = true;
    expect(within(row).getByText("-0.000123456789123456789 (currency unknown)")).toBeInTheDocument();
    expect(within(row).getByText("Native closing PnL").nextElementSibling).toHaveTextContent("Unknown");
    expect(within(row).getByRole("link", { name: "View matched command" })).toHaveAttribute("href", `/execution/manual-demo/${item.command_id}`);
    fireEvent.click(within(row).getByRole("button", { name: "Back to activity" }));
    expect(row).not.toHaveAttribute("open");
    expect(summary).toHaveFocus();
    expect(readNativeActivity).toHaveBeenCalledOnce();
  });

  it("deduplicates overlapping fills, even when two fills link to the same command", async () => {
    const fill = nativeFill({ origin: "alphatrade_matched", command_id: "22222222-2222-4222-8222-222222222222" });
    vi.mocked(readNativeActivity).mockResolvedValueOnce(activityPage({ items: [fill], next_cursor: "continuation" }))
      .mockResolvedValueOnce(activityPage({ items: [fill, { ...fill, native_id: "fill-2", trade_id: "fill-2" }] }));
    render(<NativeActivity statistics />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older activity" }));
    await waitFor(() => expect(screen.getAllByTestId("native-activity-row")).toHaveLength(2));
    expect(screen.getByText("Native fills shown").nextElementSibling).toHaveTextContent("2");
    expect(readNativeActivity).toHaveBeenLastCalledWith({ kind: "fill", limit: 20, cursor: "continuation" }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
  });

  it("keeps Journal compact and statistics on Dashboard", async () => {
    render(<NativeActivity />);
    await screen.findByTestId("native-activity-row");
    expect(screen.queryByTestId("native-activity-statistics")).not.toBeInTheDocument();
    expect(screen.getByText(/incomplete coverage/)).toBeInTheDocument();
  });

  it("keeps completed orders as context rather than additional fills", async () => {
    vi.mocked(readNativeActivity).mockResolvedValueOnce(activityPage()).mockResolvedValueOnce(activityPage({ items: [
      nativeFill({ kind: "order", trade_id: null, quantity: "10.000", filled_quantity: "3.000", state: "canceled" }),
    ] }));
    render(<NativeActivity statistics />);
    await screen.findByTestId("native-activity-row");
    fireEvent.click(screen.getByRole("button", { name: "Completed orders" }));
    await waitFor(() => expect(screen.getByTestId("native-activity-row").querySelector("summary")).toHaveTextContent("10.000 contracts requested"));
    expect(screen.queryByTestId("native-activity-statistics")).not.toBeInTheDocument();
    expect(readNativeActivity).toHaveBeenLastCalledWith(expect.objectContaining({ kind: "order" }), expect.anything());
  });

  it("clears cached rows on user changes within the same organization", async () => {
    const { rerender } = render(<NativeActivity />);
    await screen.findByTestId("native-activity-row");
    identity = { ...identity, user: { id: "reader-2" } };
    vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ items: [] }));
    rerender(<NativeActivity />);
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
    await screen.findByText(/No stored fills/);
  });

  it("aborts and fences a late response from a former organization", async () => {
    let resolve!: (page: NativeActivityPage) => void;
    vi.mocked(readNativeActivity).mockReturnValueOnce(new Promise(done => { resolve = done; }));
    const { rerender } = render(<NativeActivity />);
    const signal = vi.mocked(readNativeActivity).mock.calls[0][1]!.signal!;
    identity = { user: { id: "reader-2" }, organization: { id: "33333333-3333-4333-8333-333333333333" } };
    vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ organization_id: identity.organization!.id, items: [] }));
    rerender(<NativeActivity />);
    expect(signal.aborted).toBe(true);
    await screen.findByText(/No stored fills/);
    await act(async () => resolve(activityPage()));
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
  });

  it("clears history on logout and ignores the pending read", async () => {
    const { rerender } = render(<NativeActivity />);
    await screen.findByTestId("native-activity-row");
    identity = { user: null, organization: null };
    act(() => sessionCleared());
    rerender(<NativeActivity />);
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
    expect(readNativeActivity).toHaveBeenCalledOnce();
  });

  it("withholds unverified native accounts even if the response carries old items", async () => {
    vi.mocked(readNativeActivity).mockResolvedValue(activityPage({ identity_status: "unverified", freshness: "unverified" }));
    render(<NativeActivity statistics />);
    await screen.findByText(/Account identity unverified/);
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
    expect(screen.queryByTestId("native-activity-statistics")).not.toBeInTheDocument();
  });

  it("clears a continuation when the configured native account changes", async () => {
    vi.mocked(readNativeActivity).mockResolvedValueOnce(activityPage({ next_cursor: "old-account-cursor" }))
      .mockResolvedValueOnce(activityPage({ account_uid: "new-account" }));
    render(<NativeActivity />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older activity" }));
    await screen.findByRole("alert");
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("account changed");
    expect(readNativeActivity).toHaveBeenCalledTimes(2);
  });

  it("drops history for a foreign cursor and preserves no recovery cursor", async () => {
    vi.mocked(readNativeActivity).mockResolvedValueOnce(activityPage({ next_cursor: "cursor" }))
      .mockRejectedValueOnce(new ApiError("foreign", 422, null));
    render(<NativeActivity />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older activity" }));
    await screen.findByRole("alert");
    expect(screen.queryByTestId("native-activity-row")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Load older activity" })).not.toBeInTheDocument();
  });

  it("labels retained same-account history when pagination fails", async () => {
    vi.mocked(readNativeActivity).mockResolvedValueOnce(activityPage({ next_cursor: "cursor" }))
      .mockRejectedValueOnce(new ApiError("down", 503, null));
    render(<NativeActivity />);
    fireEvent.click(await screen.findByRole("button", { name: "Load older activity" }));
    await screen.findByRole("alert");
    expect(screen.getByTestId("native-activity-row")).toBeInTheDocument();
    expect(screen.getByText("Refresh failed")).toBeInTheDocument();
    expect(screen.queryByText("Fresh at read")).not.toBeInTheDocument();
  });
});
