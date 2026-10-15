import { beforeEach, expect, it, vi } from "vitest";
import { monitoringAssignments } from "./assignments-api";

const { fetchMock } = vi.hoisted(() => ({ fetchMock: vi.fn() }));
vi.mock("@/lib/api/client", async importOriginal => ({ ...await importOriginal<typeof import("@/lib/api/client")>(), apiFetch: (...args: unknown[]) => fetchMock(...args) }));
const valid = () => ({ revision: 1, configured: true, assignments: [], available_versions: [], symbols: ["BTCUSDT"], max_assignments: 25, monitoring_only: true });
beforeEach(() => { vi.clearAllMocks(); fetchMock.mockResolvedValue(valid()); });

it("uses the actual scoped route and exact serialized original request key/body", async () => {
  const body = { revision: 0, request_key: "original-key", assignments: [] };
  await monitoringAssignments.replace(body);
  expect(fetchMock).toHaveBeenCalledWith("/watcher/assignments", expect.objectContaining({ method: "PUT", auth: true, body: JSON.stringify(body) }));
  await monitoringAssignments.read();
  expect(fetchMock).toHaveBeenLastCalledWith("/watcher/assignments", expect.objectContaining({ auth: true }));
});
it.each([{}, { ...valid(), monitoring_only: false }, { ...valid(), revision: -1 }, { ...valid(), assignments: [{}] }])("rejects malformed successful response as ambiguous", async malformed => {
  fetchMock.mockResolvedValue(malformed);
  await expect(monitoringAssignments.replace({ revision: 0, request_key: "original-key", assignments: [] })).rejects.toMatchObject({ status: 0 });
});

it.each([
  { revision: -1, request_key: "original", assignments: [] },
  { revision: 0, request_key: "x".repeat(129), assignments: [] },
  { revision: 0, request_key: "original", assignments: [{ id: "bad", symbol: "BTCUSDT", strategy_version_id: "bad", monitoring_enabled: true, timeframes: [], parameters: {} }] },
])("rejects local request shape without starting transport", async body => {
  expect(() => monitoringAssignments.validateRequest(body)).toThrow("Invalid monitoring request");
  await expect(monitoringAssignments.replace(body)).rejects.toMatchObject({ status: 422 });
  expect(fetchMock).not.toHaveBeenCalled();
});

it("rejects a malformed observation as an ambiguous committed reply", async () => {
  fetchMock.mockResolvedValue({ ...valid(), assignments: [{ id: "record", symbol: "BTCUSDT", strategy_id: "strategy", strategy_version_id: "version", strategy_name: "Nested", version: 1, version_content_hash: "a".repeat(64), monitoring_enabled: true, timeframes: ["15m"], parameters: {}, eligible: true, reason_code: "ready", latest: { observed_at: "not a date" } }] });
  await expect(monitoringAssignments.replace({ revision: 0, request_key: "original-key", assignments: [] })).rejects.toMatchObject({ status: 0 });
  expect(fetchMock).toHaveBeenCalledTimes(1);
});
