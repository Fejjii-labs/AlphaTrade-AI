import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ApiError } from "@/lib/api/client";
import { bindRecoverySession, invalidateRecoverySession } from "@/lib/auth/recovery-session";
import { sessionCleared } from "@/lib/auth/session-events";
import type { AssignmentsConfiguration } from "@/lib/monitoring/assignments-api";
import { WatcherAssignmentsSection } from "./WatcherAssignmentsSection";

const { read, replace, validateRequest, auth } = vi.hoisted(() => ({ read: vi.fn(), replace: vi.fn(), validateRequest: vi.fn(), auth: { user: { id: "user-a" }, organization: { id: "org-a" } } }));
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("@/lib/monitoring/assignments-api", () => ({ monitoringAssignments: { read: (...args: unknown[]) => read(...args), replace: (...args: unknown[]) => replace(...args), validateRequest: (...args: unknown[]) => validateRequest(...args) } }));

function configuration(): AssignmentsConfiguration {
  const options = [1, 2].map(number => ({ strategy_id: `strategy-${number}`, strategy_version_id: `version-${number}`, strategy_name: number === 1 ? "Nested" : "SFP", version: number, version_content_hash: "a".repeat(64), symbol: "BTCUSDT", timeframes: ["15m"], parameters: { symbol: "BTCUSDT", trigger_timeframe: "15m", parameters: { confirmation_window: number * 12 } } }));
  return { revision: 4, configured: true, symbols: ["BTCUSDT", "ETHUSDT"], max_assignments: 25, monitoring_only: true, available_versions: options,
    assignments: options.map((version, index) => ({ ...version, id: `assignment-${index}`, monitoring_enabled: true, eligible: index === 0, reason_code: index === 0 ? "ready" : "draft_not_executable", latest: null })) };
}

beforeEach(() => {
  vi.clearAllMocks(); sessionStorage.clear(); invalidateRecoverySession();
  validateRequest.mockImplementation(() => undefined);
  auth.user = { id: "user-a" }; auth.organization = { id: "org-a" };
  bindRecoverySession("org-a", "user-a");
  read.mockResolvedValue(configuration());
  replace.mockImplementation(async body => ({ ...configuration(), revision: body.revision + 1, assignments: body.assignments.map((row: object, index: number) => ({ ...configuration().assignments[index], ...row })) }));
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function loaded() { render(<WatcherAssignmentsSection />); await screen.findByText("BTCUSDT · Nested"); }
function toggleFirst() { fireEvent.click(screen.getByLabelText("Monitor BTCUSDT Nested")); }

it("renders compact saved versions and reasons, keeping identifiers and parameter evidence in details", async () => {
  await loaded();
  expect(screen.getAllByTestId("monitoring-assignment")).toHaveLength(2);
  expect(screen.getByTestId("monitoring-assignments")).toHaveTextContent("draft not executable");
  expect(screen.getByTestId("monitoring-assignments")).not.toHaveTextContent("assignment-0");
  fireEvent.click(within(screen.getAllByTestId("monitoring-assignment")[0]!).getByRole("button", { name: "Details" }));
  expect(screen.getByTestId("monitoring-assignment-details")).toHaveTextContent("confirmation_window");
  expect(screen.getByTestId("monitoring-assignment-details")).toHaveTextContent("assignment-0");
  fireEvent.click(screen.getByRole("button", { name: "Back to monitoring" }));
  expect(screen.getByTestId("monitoring-assignments")).toBeInTheDocument();
});

it("independently toggles one assignment, retaining exact sibling version parameters and boundaries", async () => {
  await loaded(); toggleFirst();
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Monitoring saved.");
  const body = replace.mock.calls[0]![0];
  expect(body.revision).toBe(4); expect(body.request_key).toMatch(/^[a-f0-9-]{36}$/);
  expect(body.assignments[0].monitoring_enabled).toBe(false); expect(body.assignments[1].monitoring_enabled).toBe(true);
  expect(body.assignments[1].parameters).toEqual(configuration().assignments[1]!.parameters);
  expect(Object.keys(body)).toEqual(["revision", "request_key", "assignments"]);
});

it("adding a saved version uses its immutable snapshot and rejects duplicate coin/version bindings", async () => {
  const initial = configuration(); initial.assignments = [initial.assignments[0]!]; read.mockResolvedValue(initial);
  await loaded();
  fireEvent.change(screen.getByLabelText("Saved strategy version"), { target: { value: "version-1" } });
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  fireEvent.change(screen.getByLabelText("Saved strategy version"), { target: { value: "version-2" } });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
  expect(screen.getAllByTestId("monitoring-assignment")).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Monitoring saved.");
  expect(replace.mock.calls[0]![0].assignments[1].parameters).toEqual(initial.available_versions[1]!.parameters);
});

it.each([new TypeError("Lost response"), new ApiError("Invalid monitoring response", 0, {})])("retains original body/key after ambiguous reply, reloads without a retry, and recovers only explicitly (%s)", async error => {
  replace.mockRejectedValueOnce(error);
  const first = render(<WatcherAssignmentsSection />);
  await screen.findByText("BTCUSDT · Nested"); toggleFirst();
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByRole("button", { name: "Recover saved request" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Recover saved request" })).not.toBeDisabled());
  const original = structuredClone(replace.mock.calls[0]![0]);
  first.unmount();
  render(<WatcherAssignmentsSection />);
  await screen.findByText("BTCUSDT · Nested");
  expect(replace).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Save monitoring" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Recover saved request" }));
  await screen.findByText("Monitoring saved.");
  expect(replace).toHaveBeenCalledTimes(2); expect(replace.mock.calls[1]![0]).toEqual(original);
});

it("definite first-attempt rejection preserves editable rows and releases recovery", async () => {
  replace.mockRejectedValueOnce(new ApiError("Saved configuration changed", 409, {}));
  await loaded(); toggleFirst(); fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Saved configuration changed");
  expect(screen.queryByRole("button", { name: "Recover saved request" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Monitor BTCUSDT Nested")).not.toBeChecked();
  expect(screen.getByRole("button", { name: "Save monitoring" })).not.toBeDisabled();
});

it("keeps restored recovery disabled until the initial configuration is ready", async () => {
  replace.mockRejectedValueOnce(new TypeError("Lost reply"));
  const first = render(<WatcherAssignmentsSection />);
  await screen.findByText("BTCUSDT · Nested"); toggleFirst();
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Lost reply");
  const original = structuredClone(replace.mock.calls[0]![0]);
  first.unmount();
  let finish!: (value: AssignmentsConfiguration) => void;
  read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  render(<WatcherAssignmentsSection />);
  const recovery = await screen.findByRole("button", { name: "Recover saved request" });
  expect(screen.getByText("Loading monitoring…")).toBeInTheDocument();
  expect(recovery).toBeDisabled();
  fireEvent.click(recovery);
  expect(replace).toHaveBeenCalledTimes(1);
  finish(configuration());
  await screen.findByText("BTCUSDT · Nested");
  await waitFor(() => expect(recovery).not.toBeDisabled());
  expect(replace).toHaveBeenCalledTimes(1);
  fireEvent.click(recovery);
  await screen.findByText("Monitoring saved.");
  expect(replace).toHaveBeenCalledTimes(2);
  expect(replace.mock.calls[1]![0]).toEqual(original);
});

it("validates before storing recovery and lets a locally rejected edit be corrected normally", async () => {
  await loaded(); toggleFirst();
  validateRequest.mockImplementationOnce(() => { throw new ApiError("Invalid monitoring request", 422, {}); });
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Invalid monitoring request");
  expect(replace).not.toHaveBeenCalled();
  expect(Array.from({ length: sessionStorage.length }, (_, index) => sessionStorage.key(index)).some(key => key?.startsWith("alphatrade:watcher-assignments:"))).toBe(false);
  expect(screen.queryByRole("button", { name: "Recover saved request" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Save monitoring" })).not.toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Monitoring saved.");
  expect(replace).toHaveBeenCalledTimes(1);
});

it("validates a restored original request again before explicit recovery transport", async () => {
  replace.mockRejectedValueOnce(new TypeError("Lost reply"));
  const first = render(<WatcherAssignmentsSection />);
  await screen.findByText("BTCUSDT · Nested"); toggleFirst();
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Lost reply");
  first.unmount(); render(<WatcherAssignmentsSection />);
  await screen.findByText("BTCUSDT · Nested");
  expect(validateRequest).toHaveBeenCalledTimes(2);
  validateRequest.mockImplementationOnce(() => { throw new Error("Saved request validation unavailable"); });
  fireEvent.click(screen.getByRole("button", { name: "Recover saved request" }));
  await screen.findByText("Saved request validation unavailable");
  expect(replace).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("button", { name: "Recover saved request" })).not.toBeDisabled();
});

it("explains and enforces the five-version coin limit", async () => {
  const many = configuration();
  many.assignments = Array.from({ length: 5 }, (_, index) => ({ ...many.assignments[0]!, id: `five-${index}`, strategy_version_id: `many-${index}` }));
  read.mockResolvedValue(many);
  render(<WatcherAssignmentsSection />);
  await screen.findByText("Five saved versions per coin.");
  fireEvent.change(screen.getByLabelText("Saved strategy version"), { target: { value: "version-2" } });
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
});

it.each(["ETHUSDT", "ZECUSDT"])("explains why %s has no saved version without cloning another coin's strategy", async coin => {
  const initial = configuration(); initial.symbols.push("ZECUSDT"); read.mockResolvedValue(initial);
  await loaded();
  fireEvent.change(screen.getByLabelText("Assignment coin"), { target: { value: coin } });
  expect(screen.getByText(`No saved version for ${coin}. Capture one in Agent.`)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
  expect(screen.getByRole("link", { name: "Capture a strategy" })).toHaveAttribute("href", "/agent");
  expect(screen.getByLabelText("Saved strategy version").querySelectorAll("option")).toHaveLength(1);
  expect(replace).not.toHaveBeenCalled();
});

it("storage denial prevents transport and cannot stop session cancellation cleanup", async () => {
  await loaded(); toggleFirst();
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("denied"); });
  fireEvent.click(screen.getByRole("button", { name: "Save monitoring" }));
  await screen.findByText("Request recovery storage unavailable. Nothing was sent.");
  expect(replace).not.toHaveBeenCalled();
  expect(() => sessionCleared()).not.toThrow();
  await waitFor(() => expect(screen.queryByText("BTCUSDT · Nested")).not.toBeInTheDocument());
});

it("account changes discard old data and cancel late reads", async () => {
  let finish!: (value: AssignmentsConfiguration) => void;
  read.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
  const view = render(<WatcherAssignmentsSection />);
  auth.user = { id: "user-b" }; auth.organization = { id: "org-b" }; bindRecoverySession("org-b", "user-b");
  const other = { ...configuration(), assignments: [], available_versions: [] };
  read.mockResolvedValue(other); view.rerender(<WatcherAssignmentsSection />);
  await screen.findByText("No saved strategy assignments.");
  finish(configuration());
  await waitFor(() => expect(screen.queryByText("BTCUSDT · Nested")).not.toBeInTheDocument());
});
