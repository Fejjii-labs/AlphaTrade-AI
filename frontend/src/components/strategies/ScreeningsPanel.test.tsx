import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ScreeningsPanel } from "./ScreeningsPanel";
import { trendpulseScreen, trendpulseScreenings, trendpulseScreeningDetail, trendpulseCandidate } from "@/lib/api/generated/client";
import { ApiError } from "@/lib/api/client";
import { experimentVersion } from "@/test/experiment-fixtures";
import { bindRecoverySession } from "@/lib/auth/recovery-session";
import { onSessionCleared, sessionCleared } from "@/lib/auth/session-events";
let identity = { user: { id: "u" }, organization: { id: "o" } };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/generated/client", () => ({ trendpulseScreen: vi.fn(), trendpulseScreenings: vi.fn(), trendpulseScreeningDetail: vi.fn(), trendpulseCandidate: vi.fn() }));
const version = experimentVersion({ configuration: { ...experimentVersion().configuration, family: "trendpulse_1r/v1" } });
const receipt = { id: "99999999-9999-4999-8999-999999999999", request_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", status: "refused", reason: "trigger_expired", receipt_provenance: "synthetic_fixture", decision_at: "2026-10-10T12:06:00Z", performance: null, execution_authorized: false } as Awaited<ReturnType<typeof trendpulseScreen>>;
const page = { items: [receipt], total: 1, limit: 5, offset: 0 };
const pendingCount = () => Object.keys(sessionStorage).filter(key => key.startsWith("alphatrade:experiment-draft:") || key.startsWith("alphatrade:screening:")).length;
beforeEach(() => {
  identity = { user: { id: "u" }, organization: { id: "o" } }; sessionCleared(); sessionStorage.clear(); bindRecoverySession("o", "u");
  vi.mocked(trendpulseScreenings).mockReset().mockResolvedValue(page);
  vi.mocked(trendpulseScreeningDetail).mockReset().mockResolvedValue(receipt);
  vi.mocked(trendpulseScreen).mockReset().mockResolvedValue(receipt);
  vi.mocked(trendpulseCandidate).mockReset();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function enter() {
  await screen.findByText("Rejected · trigger expired");
  fireEvent.click(screen.getByText("Screen a closed trigger"));
  fireEvent.change(screen.getByLabelText("Trigger close (UTC)"), { target: { value: "2026-10-10T12:05" } });
}
describe("durable research screening", () => {
  it("reviews an exact public screening explicitly without an order request", async () => {
    vi.mocked(trendpulseScreeningDetail).mockResolvedValue({ ...receipt, status: "qualified_research_signal", receipt_provenance: "live_public_rest" });
    render(<ScreeningsPanel version={{ ...version, state: "running" }} />);
    await screen.findByRole("button", { name: "Details" });
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    const review = await screen.findByRole("button", { name: "Review setup" });
    expect(trendpulseCandidate).not.toHaveBeenCalled();
    fireEvent.click(review);
    await screen.findByText(/Setup passed canonical review/);
    expect(trendpulseCandidate).toHaveBeenCalledExactlyOnceWith(receipt.id, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(trendpulseScreen).not.toHaveBeenCalled();
  });
  it("keeps synthetic research outside the canonical review control", async () => {
    vi.mocked(trendpulseScreeningDetail).mockResolvedValue({ ...receipt, status: "qualified_research_signal", receipt_provenance: "synthetic_fixture" });
    render(<ScreeningsPanel version={{ ...version, state: "running" }} />);
    await screen.findByRole("button", { name: "Details" });
    fireEvent.click(screen.getByRole("button", { name: "Details" }));
    await screen.findByText("Receipts & evidence");
    expect(screen.queryByRole("button", { name: "Review setup" })).not.toBeInTheDocument();
    expect(trendpulseCandidate).not.toHaveBeenCalled();
  });
  it("shows reason/provenance, hides technical evidence until details and navigates Back", async () => {
    render(<ScreeningsPanel version={version} />); await screen.findByText("Rejected · trigger expired");
    expect(screen.getByTestId("screenings")).not.toHaveTextContent(receipt.id);
    expect(screen.getByText(/Performance unavailable/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Details" })); await screen.findByText("Receipts & evidence");
    expect(screen.getByText("Receipts & evidence").closest("details")).not.toHaveAttribute("open");
    fireEvent.click(screen.getByRole("button", { name: /Back to screening history/ })); await screen.findByRole("button", { name: "Details" });
  });
  it("rejects local invalid fields without storage or a request and lets correction submit", async () => {
    render(<ScreeningsPanel version={version} />); await enter();
    fireEvent.change(screen.getByLabelText("Trigger close (UTC)"), { target: { value: "" } });
    fireEvent.submit(screen.getByRole("button", { name: "Screen trigger" }).closest("form")!);
    await screen.findByText("Choose a valid closed trigger and variant."); expect(trendpulseScreen).not.toHaveBeenCalled(); expect(pendingCount()).toBe(0);
    fireEvent.change(screen.getByLabelText("Trigger close (UTC)"), { target: { value: "2026-10-10T12:05" } });
    fireEvent.click(screen.getByRole("button", { name: "Screen trigger" })); await waitFor(() => expect(trendpulseScreen).toHaveBeenCalledOnce());
    expect(vi.mocked(trendpulseScreen).mock.calls[0][2].trigger_end).toBe("2026-10-10T12:05:00.000Z");
  });
  it.each([new TypeError("timeout"), new ApiError("malformed response", 0, null)])("retains exact identity after ambiguity and remount without auto retry: %s", async cause => {
    vi.mocked(trendpulseScreen).mockRejectedValueOnce(cause);
    render(<ScreeningsPanel version={version} />); await enter(); fireEvent.click(screen.getByRole("button", { name: "Screen trigger" }));
    await screen.findByRole("button", { name: "Recover screening" }); const original = vi.mocked(trendpulseScreen).mock.calls[0][2];
    cleanup(); render(<ScreeningsPanel version={version} />); await screen.findByRole("button", { name: "Recover screening" });
    expect(trendpulseScreen).toHaveBeenCalledOnce(); expect(screen.queryByRole("button", { name: "Screen trigger" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recover screening" })); await waitFor(() => expect(trendpulseScreen).toHaveBeenCalledTimes(2));
    expect(vi.mocked(trendpulseScreen).mock.calls[1][2]).toEqual(original); await screen.findByText("Receipts & evidence"); expect(pendingCount()).toBe(0);
  });
  it("keeps disabled production screening readable without an uncertain lock", async () => {
    vi.mocked(trendpulseScreen).mockRejectedValue(new ApiError("disabled", 503, { error: { code: "screening_disabled" } }));
    render(<ScreeningsPanel version={version} />); await enter(); fireEvent.click(screen.getByRole("button", { name: "Screen trigger" }));
    await screen.findByText(/Research screening is unavailable/); expect(pendingCount()).toBe(0);
    expect(screen.queryByRole("button", { name: "Recover screening" })).not.toBeInTheDocument();
  });
  it("drops old tenant reads and clears pending data when the session clears", async () => {
    let resolve!: (value: typeof page) => void;
    vi.mocked(trendpulseScreenings).mockImplementationOnce(() => new Promise(done => { resolve = done; })).mockResolvedValue({ ...page, items: [], total: 0 });
    const view = render(<ScreeningsPanel version={version} />); await waitFor(() => expect(trendpulseScreenings).toHaveBeenCalledOnce());
    const signal = vi.mocked(trendpulseScreenings).mock.calls[0][3]!.signal!;
    identity = { user: { id: "other" }, organization: { id: "other" } }; bindRecoverySession("other", "other"); view.rerender(<ScreeningsPanel version={version} />);
    await screen.findByText("No screenings recorded."); expect(signal.aborted).toBe(true);
    await act(async () => resolve(page)); expect(screen.queryByText("Rejected · trigger expired")).not.toBeInTheDocument();
    sessionStorage.setItem("alphatrade:screening:old", "private"); act(sessionCleared); expect(sessionStorage.getItem("alphatrade:screening:old")).toBeNull();
  });
  it.each([[403, "forbidden"], [404, "not_found"], [422, "validation_error"], [503, "screening_source_not_enabled"]])("releases documented pre-commit rejection %s %s without losing editable input", async (status, code) => {
    vi.mocked(trendpulseScreen).mockRejectedValueOnce(new ApiError("refused", Number(status), { error: { code } }));
    render(<ScreeningsPanel version={version} />); await enter(); fireEvent.click(screen.getByRole("button", { name: "Screen trigger" }));
    await waitFor(() => expect(pendingCount()).toBe(0));
    expect(screen.queryByRole("button", { name: "Recover screening" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Trigger close (UTC)")).toHaveValue("2026-10-10T12:05");
    fireEvent.click(screen.getByRole("button", { name: "Screen trigger" })); await screen.findByText("Receipts & evidence");
  });
  it("retains the original identity when another screening is still in progress", async () => {
    vi.mocked(trendpulseScreen).mockRejectedValueOnce(new ApiError("busy", 409, { error: { code: "screening_in_progress" } }));
    render(<ScreeningsPanel version={version} />); await enter(); fireEvent.click(screen.getByRole("button", { name: "Screen trigger" }));
    await screen.findByRole("button", { name: "Recover screening" }); const original = vi.mocked(trendpulseScreen).mock.calls[0][2];
    expect(pendingCount()).toBe(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Recover screening" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Recover screening" }));
    await screen.findByText("Receipts & evidence"); expect(vi.mocked(trendpulseScreen).mock.calls[1][2]).toEqual(original);
  });
  it("continues identity cleanup when recovery storage removal throws", async () => {
    render(<ScreeningsPanel version={version} />); await enter(); sessionStorage.setItem("alphatrade:screening:old", "private");
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("denied", "SecurityError"); });
    const next = vi.fn(); const unsubscribe = onSessionCleared(next);
    try { act(sessionCleared); expect(next).toHaveBeenCalledOnce(); } finally { unsubscribe(); }
    await waitFor(() => expect(trendpulseScreenings).toHaveBeenCalledOnce());
    expect(vi.mocked(trendpulseScreenings).mock.calls[0][3]!.signal!.aborted).toBe(true);
  });
  it("keeps a malformed-success original when its explicit retry is forbidden", async () => {
    vi.mocked(trendpulseScreen).mockRejectedValueOnce(new ApiError("malformed", 0, null))
      .mockRejectedValueOnce(new ApiError("forbidden", 403, { error: { code: "forbidden" } }));
    render(<ScreeningsPanel version={version} />); await enter(); fireEvent.click(screen.getByRole("button", { name: "Screen trigger" }));
    await screen.findByRole("button", { name: "Recover screening" }); const original = vi.mocked(trendpulseScreen).mock.calls[0][2];
    await waitFor(() => expect(screen.getByRole("button", { name: "Recover screening" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Recover screening" }));
    await waitFor(() => expect(vi.mocked(trendpulseScreen).mock.calls).toHaveLength(2));
    await waitFor(() => expect(screen.getByRole("button", { name: "Recover screening" })).toBeEnabled());
    expect(pendingCount()).toBe(1); expect(screen.queryByRole("button", { name: "Screen trigger" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Recover screening" })); await screen.findByText("Receipts & evidence");
    expect(vi.mocked(trendpulseScreen).mock.calls.slice(1).map(call => call[2])).toEqual([original, original]);
  });
});
