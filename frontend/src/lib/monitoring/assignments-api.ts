import { ApiError } from "@/lib/api/client";
import { watcherAssignments, watcherAssignmentsReplace } from "@/lib/api/generated/client";
import { watcherAssignmentsReplaceRequest } from "@/lib/api/generated/validators";
import type { components } from "@/lib/api/generated/types";

export type AssignmentWrite = components["schemas"]["WatcherAssignmentWrite"];
export type AssignmentVersion = components["schemas"]["WatcherAssignmentVersion"];
export type AssignmentRead = components["schemas"]["WatcherAssignmentRead"];
export type AssignmentsConfiguration = components["schemas"]["WatcherAssignmentsRead"];
export type AssignmentsReplace = components["schemas"]["WatcherAssignmentReplace"];

function validateRequest(value: unknown): void {
  if (!watcherAssignmentsReplaceRequest(value)) {
    throw new ApiError("Invalid monitoring request", 422, null);
  }
  // After-model guards are not emitted in OpenAPI. Check the same bounded JSON
  // and binding uniqueness before remembering a request as possibly transported.
  const ids = new Set<string>();
  const bindings = new Set<string>();
  for (const row of value.assignments) {
    const binding = `${row.symbol}:${row.strategy_version_id}`;
    if (ids.has(row.id) || bindings.has(binding)) throw new ApiError("Invalid monitoring request", 422, null);
    ids.add(row.id); bindings.add(binding);
    try {
      const encoded = JSON.stringify(row.parameters, (_key, parameter: unknown) => {
        if (typeof parameter === "number" && !Number.isFinite(parameter)) throw new Error("Non-finite parameter");
        if (["undefined", "function", "symbol", "bigint"].includes(typeof parameter)) throw new Error("Non-JSON parameter");
        return parameter;
      });
      if (encoded.length > 20000) throw new Error("Parameter snapshot too large");
    } catch { throw new ApiError("Invalid monitoring request", 422, null); }
  }
}

export const monitoringAssignments = {
  validateRequest,
  read: (signal?: AbortSignal) => watcherAssignments({ signal }),
  replace: async (body: AssignmentsReplace, signal?: AbortSignal) => {
    validateRequest(body);
    return watcherAssignmentsReplace(body, { signal });
  },
};
