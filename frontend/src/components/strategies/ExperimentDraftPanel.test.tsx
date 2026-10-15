import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ExperimentDraftPanel } from "./ExperimentDraftPanel";
import { experimentCreate, paperAccount, strategyVersions } from "@/lib/api/generated/client";
import { experimentVersion } from "@/test/experiment-fixtures";
import { ApiError } from "@/lib/api/client";
import { bindRecoverySession } from "@/lib/auth/recovery-session";
import { onSessionCleared, sessionCleared } from "@/lib/auth/session-events";
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => ({ user: { id: "u" }, organization: { id: "o" } }) }));
vi.mock("@/lib/api/generated/client", () => ({ experimentCreate: vi.fn(), paperAccount: vi.fn(), strategyVersions: vi.fn() }));
const version = experimentVersion();
const authored: Awaited<ReturnType<typeof strategyVersions>>["items"][number] = {
  id: version.configuration.strategy_version_id, strategy_id: version.configuration.strategy_id,
  version: 2, card: {}, created_at: "2026-10-10T12:00:00Z", validation_status: "draft",
  backtest_status: "not_run", paper_validation_status: "not_started",
  pattern_spec: { kind: "trendpulse_1r/v1", parameters: { fixed: "1" }, symbol: "BTCUSDT", trigger_timeframe: "5m", trend_timeframe: "15m" },
};
const pendingCount = () => Object.keys(sessionStorage).filter(key => key.startsWith("alphatrade:experiment-draft:") || key.startsWith("alphatrade:screening:")).length;
beforeEach(() => {
  sessionCleared(); sessionStorage.clear(); bindRecoverySession("o", "u"); vi.mocked(experimentCreate).mockReset().mockResolvedValue(version);
  vi.mocked(strategyVersions).mockReset().mockResolvedValue({ items: [authored], total: 1, limit: 100, offset: 0 });
  vi.mocked(paperAccount).mockReset().mockResolvedValue({ account: { id: version.configuration.account.execution_account_id, name: "Synthetic", execution_mode: "PAPER", account_mode: "NET", enabled: true }, can_register: false });
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
async function fill() {
  await screen.findByText("Prepare research draft"); fireEvent.click(screen.getByText("Prepare research draft"));
  for (const label of ["Risk per trade", "Position notional", "Total exposure", "Daily loss", "Weekly loss", "Drawdown"]) fireEvent.change(screen.getByLabelText(`${label} (USDT)`), { target: { value: "10" } });
  fireEvent.change(screen.getByLabelText("Cost allowance (USDT)"), { target: { value: "1" } });
}
it("creates a bounded baseline from the exact authored version without native/activation/model authority", async () => {
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />); await fill();
  fireEvent.change(screen.getByLabelText("Experiment mode"), { target: { value: "validation" } }); fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" }));
  await screen.findByRole("link", { name: "Open experiment" }); const body = vi.mocked(experimentCreate).mock.calls[0][0];
  expect(body.configuration.mode).toBe("validation"); expect(body.configuration.variants).toEqual([{ key: "baseline", strategy_version_id: authored.id, parameters: authored.pattern_spec!.parameters }]);
  expect(body.configuration.timeframes).toEqual(["5m", "15m"]); expect(body.configuration.model_policy.mode).toBe("disabled"); expect(body.configuration.account.source).toBe("internal_simulation");
  expect(pendingCount()).toBe(0);
});
it("keeps invalid local bounds editable without a request or recovery lock", async () => {
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />); await fill();
  fireEvent.change(screen.getByLabelText("Risk per trade (USDT)"), { target: { value: "invalid" } }); fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" }));
  await screen.findByText("Correct the draft bounds before submitting."); expect(experimentCreate).not.toHaveBeenCalled(); expect(pendingCount()).toBe(0);
  fireEvent.change(screen.getByLabelText("Risk per trade (USDT)"), { target: { value: "10" } }); fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" })); await screen.findByRole("link", { name: "Open experiment" });
});
it("recovers an ambiguous create across remount with the exact original key/configuration", async () => {
  vi.mocked(experimentCreate).mockRejectedValueOnce(new TypeError("timeout"));
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />); await fill(); fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" }));
  await screen.findByRole("button", { name: "Recover draft" }); const original = vi.mocked(experimentCreate).mock.calls[0][0]; cleanup();
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="Changed label" />); await screen.findByRole("button", { name: "Recover draft" }); expect(experimentCreate).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole("button", { name: "Recover draft" })); await waitFor(() => expect(experimentCreate).toHaveBeenCalledTimes(2)); expect(vi.mocked(experimentCreate).mock.calls[1][0]).toEqual(original);
});
it("reports a missing enabled existing account without registering one", async () => {
  vi.mocked(paperAccount).mockResolvedValue({ account: null, can_register: true }); render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />);
  await screen.findByText(/An existing enabled paper account is required/); expect(experimentCreate).not.toHaveBeenCalled();
});
it("releases a proven account refusal and lets a corrected draft submit", async () => {
  vi.mocked(experimentCreate).mockRejectedValueOnce(new ApiError("account", 409, { error: { code: "experiment_account_invalid" } }));
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />); await fill();
  fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" }));
  await screen.findByText(/Draft rejected/); expect(pendingCount()).toBe(0);
  expect(screen.queryByRole("button", { name: "Recover draft" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" })); await screen.findByRole("link", { name: "Open experiment" });
});
it("invalidates the old view and continues session listeners when recovery storage is denied", async () => {
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />); await fill();
  sessionStorage.setItem("alphatrade:experiment-draft:old", "private");
  vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("denied", "SecurityError"); });
  const next = vi.fn(); const unsubscribe = onSessionCleared(next);
  try { act(sessionCleared); expect(next).toHaveBeenCalledOnce(); }
  finally { unsubscribe(); }
  await waitFor(() => expect(strategyVersions).toHaveBeenCalledOnce());
  expect(vi.mocked(strategyVersions).mock.calls[0][2]!.signal!.aborted).toBe(true);
});
it("keeps an ambiguous original when its explicit retry is forbidden", async () => {
  vi.mocked(experimentCreate).mockRejectedValueOnce(new TypeError("timeout"))
    .mockRejectedValueOnce(new ApiError("forbidden", 403, { error: { code: "forbidden" } }));
  render(<ExperimentDraftPanel strategyId={version.configuration.strategy_id} name="TrendPulse" />); await fill();
  fireEvent.click(screen.getByRole("button", { name: "Create experiment draft" }));
  await screen.findByRole("button", { name: "Recover draft" }); const original = vi.mocked(experimentCreate).mock.calls[0][0];
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover draft" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Recover draft" }));
  await waitFor(() => expect(vi.mocked(experimentCreate).mock.calls).toHaveLength(2));
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover draft" })).toBeEnabled());
  expect(pendingCount()).toBe(1); expect(screen.queryByRole("button", { name: "Create experiment draft" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Recover draft" })); await screen.findByRole("link", { name: "Open experiment" });
  expect(vi.mocked(experimentCreate).mock.calls.slice(1).map(call => call[0])).toEqual([original, original]);
});
