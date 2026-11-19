"use client";

import { useState } from "react";

import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";

import { LeverageSelect } from "./LeverageSelect";

type Props = {
  view: AutonomousTradingSettingsView;
  owner: boolean;
  onSaved: (view: AutonomousTradingSettingsView) => void;
};

type Status = "idle" | "saving" | "saved" | "error";

/**
 * BloFin demo leverage bound to `demo_leverage` on /autonomous/settings.
 * Presets 2x/3x/5x/10x, saved on change; the risk profile does not cap it here.
 */
export function DemoLeverageControl({ view, owner, onSaved }: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const current = view.demo_leverage ?? null;

  async function save(leverage: number) {
    setStatus("saving");
    try {
      onSaved(await api.autonomous.updateSettings({ demo_leverage: leverage }));
      setStatus("saved");
    } catch {
      setStatus("error");
    }
  }

  return (
    <div className="flex items-center justify-end gap-3" data-testid="demo-leverage-control">
      <div className="flex-1">
        <LeverageSelect
          value={current}
          disabled={!owner || status === "saving"}
          onChange={(next) => {
            if (next !== current) void save(next);
          }}
        />
      </div>
      {status === "idle" ? null : (
        <span
          role={status === "error" ? "alert" : "status"}
          data-testid="leverage-status"
          className={status === "error" ? "text-xs text-danger" : "text-xs text-text-muted"}
        >
          {status === "saving" ? "Saving…" : status === "saved" ? "Saved" : "Not saved. Try again."}
        </span>
      )}
    </div>
  );
}
