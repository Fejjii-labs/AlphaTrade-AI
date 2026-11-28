"use client";

import type { StatusTone } from "@/components/ui/status-tile";
import { useAppContext } from "@/contexts/AppContext";
import { scannerStatus } from "@/lib/scanner-status";
import { cn } from "@/lib/utils";

const DOT: Record<StatusTone, string> = {
  ok: "bg-success",
  warn: "bg-warning",
  down: "bg-danger",
  unknown: "bg-text-muted",
};

/** One plain line from the worker's own heartbeat, e.g. "Scanning · last scan 2 min ago". */
export function ScannerStatusLine({ className }: { className?: string }) {
  const { health } = useAppContext();
  const status = scannerStatus(health?.worker_runtime);
  return (
    <div className={cn("space-y-0.5", className)} data-testid="scanner-status" data-tone={status.tone} role="status">
      <p className="flex items-center gap-2 text-sm text-text-primary">
        <span aria-hidden className={cn("size-2 shrink-0 rounded-full", DOT[status.tone])} />
        {status.text}
      </p>
      {status.detail ? <p className="pl-4 text-xs text-text-muted">{status.detail}</p> : null}
    </div>
  );
}
