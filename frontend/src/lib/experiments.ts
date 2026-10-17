import type { components } from "@/lib/api/generated/types";

export type Experiment = components["schemas"]["ExperimentDetail"];
export type ExperimentPage = components["schemas"]["ExperimentPage"];
export type ExperimentVersion = components["schemas"]["ExperimentVersion"];
export type ExperimentAction = components["schemas"]["ExperimentTransition"]["action"];

export const lifecycleLabel: Record<ExperimentVersion["state"], string> = {
  draft: "Draft", pending_approval: "Awaiting approval", approved: "Approved",
  running: "Running", paused: "Paused", completed: "Completed", promoted: "Promoted",
};
export const experimentFamily: Record<ExperimentVersion["configuration"]["family"], string> = {
  "operational_nested_continuation/v1": "Nested Continuation",
  "swing_failure_pattern/v1": "SFP", "trendpulse_1r/v1": "TrendPulse 1R",
};
export function latestVersion(experiment: Experiment): ExperimentVersion | null {
  return experiment.versions.reduce<ExperimentVersion | null>((latest, version) =>
    !latest || version.version > latest.version ? version : latest, null);
}
export function experimentActivity(version: ExperimentVersion) {
  // These are recorded lifecycle timestamps, not a fabricated signal/trade feed.
  const records = [
    ["Created", version.created_at], ["Submitted", version.submitted_at],
    ["Approved", version.approved_at], ["Started", version.started_at],
    ["Paused", version.paused_at], ["Completed", version.completed_at],
    ["Promoted", version.promoted_at],
  ] as const;
  return records.filter((item): item is readonly [typeof records[number][0], string] => item[1] != null)
    .sort((a, b) => Date.parse(b[1]) - Date.parse(a[1]));
}
export function actionsFor(version: ExperimentVersion): { action: ExperimentAction; label: string }[] {
  switch (version.state) {
    case "draft": return [{ action: "submit", label: "Request approval" }];
    case "approved": return [{ action: "start", label: "Start experiment" }];
    case "running": return [{ action: "pause", label: "Pause" }, { action: "complete", label: "Complete" }];
    case "paused": return [{ action: "start", label: "Resume experiment" }, { action: "complete", label: "Complete" }];
    default: return [];
  }
}
export const sampleCount = (version: ExperimentVersion) =>
  Object.values(version.sample_counts).reduce((sum, count) => sum + count, 0);
export function sampleSummary(version: ExperimentVersion) {
  const count = sampleCount(version);
  return `${count} recorded ${count === 1 ? "sample" : "samples"}`;
}
