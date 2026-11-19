"use client";

import { useState } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { api, ApiError } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";

import { DEMO_LEVERAGE_MAX, LeverageSelect } from "./LeverageSelect";
import { leverageValidationMessage } from "./LeverageSettingsSlot";

type Props = {
  view: AutonomousTradingSettingsView;
  owner: boolean;
  onSaved: (view: AutonomousTradingSettingsView) => void;
};

/**
 * BloFin demo leverage bound to `demo_leverage` on /autonomous/settings.
 * Always 1x to 10x; the risk profile does not cap it. Raising asks first.
 */
export function DemoLeverageControl({ view, owner, onSaved }: Props) {
  const [pending, setPending] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const current = view.demo_leverage ?? null;

  async function save(leverage: number) {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.autonomous.updateSettings({ demo_leverage: leverage }));
      setPending(null);
    } catch (err) {
      setError(err instanceof ApiError ? leverageValidationMessage(err.message, DEMO_LEVERAGE_MAX) : "Could not save leverage.");
      setPending(null);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-1" data-testid="demo-leverage-control">
      <LeverageSelect
        value={current}
        disabled={!owner || saving}
        onChange={(next) => {
          if (current != null && next > current) setPending(next);
          else void save(next);
        }}
      />
      {error ? (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      ) : null}
      <ConfirmDialog
        open={pending != null}
        title={`Raise leverage to ${pending}×?`}
        description="Liquidation moves closer to entry. Trade size does not change."
        confirmLabel="Raise leverage"
        busy={saving}
        error={null}
        onConfirm={() => pending != null && void save(pending)}
        onCancel={() => setPending(null)}
        testId="leverage-raise-confirm"
      />
    </div>
  );
}
