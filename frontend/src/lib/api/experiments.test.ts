import { beforeEach, describe, expect, it, vi } from "vitest";
import { experimentFixture, experimentId, experimentPage, experimentVersion, versionId } from "@/test/experiment-fixtures";
import { apiFetch } from "./client";
import { experimentCreate, experimentDetail, experiments, experimentTransition } from "./generated/client";
import * as validators from "./generated/validators";

vi.mock("./client", async original => ({ ...await original<typeof import("./client")>(), apiFetch: vi.fn() }));
beforeEach(() => { vi.mocked(apiFetch).mockReset(); });
describe("generated experiment boundary", () => {
  it("validates nullable performance and refuses fabricated execution/results", async () => {
    vi.mocked(apiFetch).mockResolvedValue(experimentPage());
    await expect(experiments({ limit: 12, offset: 0 })).resolves.toEqual(experimentPage());
    expect(validators.experimentDetailResponse(experimentFixture())).toBe(true);
    expect(validators.experimentTransitionResponse(experimentVersion({ runtime_activated: true as never }))).toBe(false);
    expect(validators.experimentTransitionResponse({ ...experimentVersion(), performance: { returns: 0 } })).toBe(false);
  });
  it("passes both distinct path identities, revision, auth and cancellation", async () => {
    const signal = new AbortController().signal;
    vi.mocked(apiFetch).mockResolvedValue(experimentVersion({ state: "running", revision: 3 }));
    await experimentTransition(experimentId, versionId, { action: "start", expected_revision: 2 }, { signal });
    expect(apiFetch).toHaveBeenCalledExactlyOnceWith(`/experiments/${experimentId}/versions/${versionId}/transition`, expect.objectContaining({ auth: true, signal, method: "POST", body: JSON.stringify({ action: "start", expected_revision: 2 }) }));
  });
  it("rejects negative revisions, execution shortcuts and unknown fields before transport", async () => {
    for (const body of [{ action: "start", expected_revision: -1 }, { action: "approve", expected_revision: 2 }, { action: "start", expected_revision: 2, activate: true }]) {
      await expect(experimentTransition(experimentId, versionId, body as never)).rejects.toMatchObject({ status: 0 });
    }
    expect(apiFetch).not.toHaveBeenCalled();
  });
  it("handles the generated 201 creation response and preserves decimal strings", async () => {
    const version = experimentVersion();
    vi.mocked(apiFetch).mockResolvedValue(version);
    await expect(experimentCreate({ name: "Nested comparison", idempotency_key: "fixture-1", configuration: version.configuration })).resolves.toEqual(version);
    expect(validators.experimentCreateRequest({ name: "fixture", idempotency_key: "fixture", configuration: { ...version.configuration, risk_limits: { ...version.configuration.risk_limits, max_daily_loss: "50.123456789123" } } })).toBe(true);
  });
  it("does not retry a malformed transition reply or expose its payload", async () => {
    vi.mocked(apiFetch).mockResolvedValue({ private_data: "secret" });
    const result = await experimentTransition(experimentId, versionId, { action: "start", expected_revision: 2 })
      .then(() => null, cause => ({ message: cause.message, status: cause.status }));
    expect(result).toEqual({ message: "API response did not match its contract. Refresh or try again later.", status: 0 });
    expect(apiFetch).toHaveBeenCalledOnce();
  });
  it("propagates an interrupted read without retry", async () => {
    const interrupted = new TypeError("interrupted");
    vi.mocked(apiFetch).mockRejectedValue(interrupted);
    const result = await experimentDetail(experimentId).then(() => null, cause => cause.message);
    expect(result).toBe("interrupted");
    expect(apiFetch).toHaveBeenCalledOnce();
  });
});
