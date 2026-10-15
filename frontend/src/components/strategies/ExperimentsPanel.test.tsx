import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { experimentFixture, experimentId, experimentPage, experimentVersion, versionId } from "@/test/experiment-fixtures";
import { experiments, experimentDetail, experimentTransition } from "@/lib/api/generated/client";
import { sessionCleared } from "@/lib/auth/session-events";
import { ExperimentsPanel } from "./ExperimentsPanel";

let identity: { user: { id: string }; organization: { id: string } };
vi.mock("@/contexts/AuthContext", () => ({ useAuth: () => identity }));
vi.mock("@/lib/api/generated/client", () => ({ experiments: vi.fn(), experimentDetail: vi.fn(), experimentTransition: vi.fn() }));
beforeEach(() => {
  identity = { user: { id: "reader" }, organization: { id: "tenant" } };
  vi.mocked(experiments).mockReset().mockResolvedValue(experimentPage());
  vi.mocked(experimentDetail).mockReset().mockResolvedValue(experimentFixture());
  vi.mocked(experimentTransition).mockReset().mockResolvedValue(experimentVersion({ state: "running", revision: 3, started_at: "2026-10-10T10:03:00Z" }));
});
afterEach(cleanup);
async function open() {
  fireEvent.click(await screen.findByRole("button", { name: "Open Nested comparison" }));
  await screen.findByRole("button", { name: "Start experiment" });
}
describe("compact experiment presentation", () => {
  it("shows mode, lifecycle, real lifecycle activity and honest missing performance; hides identities on cards", async () => {
    render(<ExperimentsPanel />);
    const card = await screen.findByTestId("experiment-card");
    expect(card).toHaveTextContent("Exploration"); expect(card).toHaveTextContent("Approved");
    expect(card).toHaveTextContent("0 recorded samples"); expect(card).toHaveTextContent("Performance unavailable");
    expect(card).not.toHaveTextContent(experimentId); expect(card).not.toHaveTextContent(versionId);
    expect(card).not.toHaveTextContent("configuration_hash"); expect(card).not.toHaveTextContent("http");
    await open();
    expect(screen.getByText(/Signal and fill activity is not available/)).toBeInTheDocument();
    const evidence = screen.getByText("Configuration & evidence").closest("details")!;
    expect(evidence).not.toHaveAttribute("open"); expect(evidence).toHaveTextContent(experimentId);
    fireEvent.click(screen.getByRole("button", { name: /Back to experiments/ }));
    expect(await screen.findByTestId("experiment-card")).toBeInTheDocument();
  });
  it("chooses highest version, distinguishes Validation and keeps native samples separate", async () => {
    const latest = experimentVersion({ version: 2, state: "paused", configuration: { ...experimentVersion().configuration, mode: "validation", account: { ...experimentVersion().configuration.account, source: "blofin_demo", native_uid: "synthetic-native", execution_identity_audit_id: experimentId } }, sample_counts: { baseline: 4 } });
    const fixture = { ...experimentFixture(), versions: [latest, experimentVersion()] };
    vi.mocked(experiments).mockResolvedValue(experimentPage([fixture])); vi.mocked(experimentDetail).mockResolvedValue(fixture);
    render(<ExperimentsPanel />);
    const card = await screen.findByTestId("experiment-card");
    expect(card).toHaveTextContent("Validation"); expect(card).toHaveTextContent("Paused"); expect(card).toHaveTextContent("v2");
    fireEvent.click(within(card).getByRole("button"));
    expect(await screen.findByText(/BloFin demo samples remain separate/)).toBeInTheDocument();
    expect(screen.getByText("baseline: 4 / 30 minimum")).toBeInTheDocument();
  });
  it("starts only the domain experiment with its exact version/revision and never approves or activates execution", async () => {
    render(<ExperimentsPanel />); await open();
    fireEvent.click(screen.getByRole("button", { name: "Start experiment" }));
    await screen.findByText("Experiment started. Trading remains inactive.");
    expect(experimentTransition).toHaveBeenCalledExactlyOnceWith(experimentId, versionId, { action: "start", expected_revision: 2 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });
  it("keeps pending approval read-only and authoring in Agent/documents", async () => {
    const fixture = experimentFixture(experimentVersion({ state: "pending_approval" }));
    vi.mocked(experiments).mockResolvedValue(experimentPage([fixture])); vi.mocked(experimentDetail).mockResolvedValue(fixture);
    render(<ExperimentsPanel />);
    fireEvent.click(await screen.findByRole("button", { name: "Open Nested comparison" }));
    await screen.findByText(/Owner approval of the exact configuration/);
    expect(screen.queryByRole("button", { name: "Start experiment" })).not.toBeInTheDocument();
    cleanup(); vi.mocked(experiments).mockResolvedValue(experimentPage([])); render(<ExperimentsPanel />);
    expect(await screen.findByRole("link", { name: "Discuss a strategy" })).toHaveAttribute("href", "/agent?discuss=strategy");
    expect(screen.getByRole("link", { name: "capture a document" })).toHaveAttribute("href", "/knowledge");
  });
  it("requires an explicit read after ambiguous mutation and never automatically retries", async () => {
    vi.mocked(experimentTransition).mockRejectedValue(new TypeError("timeout"));
    render(<ExperimentsPanel />); await open(); fireEvent.click(screen.getByRole("button", { name: "Start experiment" }));
    await screen.findByText("Change could not be confirmed. Refresh before retrying.");
    expect(screen.getByRole("button", { name: "Start experiment" })).toBeDisabled();
    expect(experimentTransition).toHaveBeenCalledOnce(); expect(experimentDetail).toHaveBeenCalledOnce();
    vi.mocked(experimentDetail).mockResolvedValue(experimentFixture(experimentVersion({ state: "running", revision: 3 })));
    fireEvent.click(screen.getByRole("button", { name: "Refresh experiment" }));
    await screen.findByRole("button", { name: "Pause" }); expect(experimentTransition).toHaveBeenCalledOnce();
  });
  it("drops old tenant responses, selection and session-cleared data", async () => {
    let resolve!: (value: ReturnType<typeof experimentPage>) => void;
    vi.mocked(experiments).mockImplementationOnce(() => new Promise(result => { resolve = result; })).mockResolvedValue(experimentPage([]));
    const view = render(<ExperimentsPanel />);
    await waitFor(() => expect(experiments).toHaveBeenCalledOnce());
    const oldSignal = vi.mocked(experiments).mock.calls[0][1]!.signal!;
    identity = { user: { id: "other-reader" }, organization: { id: "other-tenant" } }; view.rerender(<ExperimentsPanel />);
    await screen.findByText(/No experiments yet/); expect(oldSignal.aborted).toBe(true);
    await act(async () => resolve(experimentPage())); expect(screen.queryByTestId("experiment-card")).not.toBeInTheDocument();
    vi.mocked(experiments).mockResolvedValue(experimentPage()); act(sessionCleared);
    await screen.findByTestId("experiment-card");
  });
  it("reports failed reads as unavailable and implements real offset paging", async () => {
    vi.mocked(experiments).mockRejectedValueOnce(new Error("offline")).mockResolvedValue({ ...experimentPage(), total: 13 });
    render(<ExperimentsPanel />); await screen.findByText(/Experiments unavailable/);
    expect(screen.queryByText(/No experiments yet/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Refresh experiments" })); await screen.findByTestId("experiment-card");
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() => expect(experiments).toHaveBeenLastCalledWith({ limit: 12, offset: 12 }, expect.objectContaining({ signal: expect.any(AbortSignal) })));
  });
});
