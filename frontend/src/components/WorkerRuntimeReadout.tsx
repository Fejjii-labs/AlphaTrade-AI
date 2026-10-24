import type { WorkerEffectiveObservation } from "@/lib/api/types";
import { formatDateTime, UNAVAILABLE } from "@/lib/format";
import { formatWorkerFlag, workerFlagFreshness } from "@/lib/worker-runtime";

function row(label: string, value: string) {
  return (
    <div>
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="break-words">{value}</dd>
    </div>
  );
}

export function WorkerRuntimeReadout({
  observation,
}: {
  observation?: WorkerEffectiveObservation | null;
}) {
  const reported = observation?.source === "worker";
  const freshness = workerFlagFreshness(observation);
  const watchlist = reported && observation?.watchlist.length ? observation.watchlist.join(", ") : UNAVAILABLE;
  const mode = reported && observation?.autonomy_mode ? observation.autonomy_mode : "Not reported";
  const lastCycle =
    reported && observation?.last_cycle_at ? formatDateTime(observation.last_cycle_at) : UNAVAILABLE;

  return (
    <dl
      className="grid min-w-0 gap-2 text-sm sm:grid-cols-2"
      data-testid="worker-runtime-readout"
    >
      {row("Worker report", freshness)}
      {row("Autonomy mode", mode)}
      {row(
        "Paper ceiling",
        formatWorkerFlag(observation?.autonomous_internal_paper_env_ceiling, reported),
      )}
      {row(
        "Paper switch",
        formatWorkerFlag(observation?.autonomous_internal_paper_db_enabled, reported),
      )}
      {row(
        "Paper effective",
        formatWorkerFlag(observation?.autonomous_internal_paper_effective, reported),
      )}
      {row(
        "Watcher orchestration",
        formatWorkerFlag(observation?.watcher_orchestration_enabled, reported),
      )}
      {row(
        "Watcher activation",
        formatWorkerFlag(observation?.watcher_paper_staging_activation, reported),
      )}
      {row(
        "Telegram armed",
        formatWorkerFlag(observation?.telegram_paper_activation_armed, reported),
      )}
      {row(
        "Telegram interaction",
        formatWorkerFlag(observation?.telegram_interaction_enabled, reported),
      )}
      {row("Telegram alerts", formatWorkerFlag(observation?.telegram_alerts_enabled, reported))}
      {row(
        "Automatic Telegram delivery",
        formatWorkerFlag(observation?.automatic_telegram_delivery_enabled, reported),
      )}
      {row("Watchlist", watchlist)}
      {row("Last cycle", lastCycle)}
      {row("Real trading", "Off")}
    </dl>
  );
}
