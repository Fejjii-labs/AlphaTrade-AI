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

export function DemoLeverageControl({ view, owner, onSaved }: Props) {
  const [status, setStatus] = useState<Status>("idle");
  const v2 = view.risk_model_v2_enabled;
  const current = v2 === true
    ? view.demo_risk_v2_leverage ?? null
    : v2 === false ? view.demo_leverage ?? null : null;

  async function save(leverage: number) {
    if (current === null || v2 == null) return;
    setStatus("saving");
    try {
      if (v2) {
        const currentSettings = await api.riskPolicy.demoBotSettings();
        const saved = await api.riskPolicy.updateDemoBotSettings({ ...currentSettings, leverage });
        onSaved({ ...view, demo_risk_v2_leverage: saved.leverage });
      } else {
        onSaved(await api.autonomous.updateSettings({ demo_leverage: leverage }));
      }
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
          disabled={!owner || current === null || status === "saving"}
          onChange={(next) => {
            if (next !== current) void save(next);
          }}
        />
      </div>
      {status === "idle" ? (
        current === null ? <span className="text-xs text-text-muted">Leverage unavailable.</span> : null
      ) : (
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
