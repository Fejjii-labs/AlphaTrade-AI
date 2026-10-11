import { apiFetch, ApiError } from "@/lib/api/client";

export type AssignmentWrite = {
  id: string; symbol: string; strategy_version_id: string; monitoring_enabled: boolean;
  timeframes: string[]; parameters: Record<string, unknown>;
};
export type AssignmentVersion = {
  strategy_id: string; strategy_version_id: string; strategy_name: string; version: number;
  version_content_hash: string; symbol: string | null; timeframes: string[]; parameters: Record<string, unknown>;
};
export type AssignmentRead = AssignmentWrite & {
  strategy_id: string; strategy_name: string; version: number; version_content_hash: string;
  eligible: boolean; reason_code: string;
  latest: { assignment_id: string; configuration_revision: number; observed_at: string; status: string;
    reason_code: string; replayed: boolean; candidate_count: number } | null;
};
export type AssignmentsConfiguration = {
  revision: number; configured: boolean; assignments: AssignmentRead[]; available_versions: AssignmentVersion[];
  symbols: string[]; max_assignments: 25; monitoring_only: true;
};
export type AssignmentsReplace = { revision: number; request_key: string; assignments: AssignmentWrite[] };

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === "object" && !Array.isArray(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(v => typeof v === "string");
const uuid = (value: unknown): value is string => typeof value === "string" && /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
function validateRequest(value: unknown): void {
  if (!object(value) || !Number.isInteger(value.revision) || Number(value.revision) < 0
    || typeof value.request_key !== "string" || !/^[A-Za-z0-9_.:-]{1,128}$/.test(value.request_key)
    || !Array.isArray(value.assignments) || value.assignments.length > 25
    || !value.assignments.every(row => object(row) && uuid(row.id) && uuid(row.strategy_version_id)
      && typeof row.symbol === "string" && row.symbol.length <= 20 && /^[A-Z0-9]+USDT$/.test(row.symbol) && row.symbol.length >= 6
      && typeof row.monitoring_enabled === "boolean" && strings(row.timeframes) && row.timeframes.length >= 1
      && row.timeframes.length <= 5 && object(row.parameters))) {
    throw new ApiError("Invalid monitoring request", 422, value);
  }
}
function latestValid(value: unknown): boolean {
  return value === null || (object(value) && uuid(value.assignment_id)
    && Number.isInteger(value.configuration_revision) && typeof value.observed_at === "string"
    && Number.isFinite(Date.parse(value.observed_at)) && typeof value.status === "string"
    && typeof value.reason_code === "string" && typeof value.replayed === "boolean"
    && Number.isInteger(value.candidate_count) && Number(value.candidate_count) >= 0);
}
function versionValid(value: unknown): boolean {
  return object(value) && typeof value.strategy_id === "string" && typeof value.strategy_version_id === "string"
    && typeof value.strategy_name === "string" && Number.isInteger(value.version)
    && typeof value.version_content_hash === "string" && /^[a-f0-9]{64}$/.test(value.version_content_hash)
    && strings(value.timeframes) && object(value.parameters);
}
function configuration(value: unknown): AssignmentsConfiguration {
  if (!object(value) || !Number.isInteger(value.revision) || Number(value.revision) < 0
    || typeof value.configured !== "boolean" || value.monitoring_only !== true || value.max_assignments !== 25
    || !strings(value.symbols) || !Array.isArray(value.available_versions) || !value.available_versions.every(versionValid)
    || !Array.isArray(value.assignments) || !value.assignments.every(a => object(a) && versionValid(a)
      && typeof a.id === "string" && typeof a.symbol === "string" && typeof a.monitoring_enabled === "boolean"
      && typeof a.eligible === "boolean" && typeof a.reason_code === "string" && "latest" in a && latestValid(a.latest))) {
    // A malformed success may follow a committed mutation; retain request identity.
    throw new ApiError("Invalid monitoring response", 0, value);
  }
  return value as AssignmentsConfiguration;
}
export const monitoringAssignments = {
  validateRequest,
  read: async (signal?: AbortSignal) => configuration(await apiFetch<unknown>("/watcher/assignments", { auth: true, signal })),
  replace: async (body: AssignmentsReplace, signal?: AbortSignal) => {
    validateRequest(body);
    return configuration(await apiFetch<unknown>("/watcher/assignments", {
      method: "PUT", auth: true, signal, body: JSON.stringify(body),
    }));
  },
};
