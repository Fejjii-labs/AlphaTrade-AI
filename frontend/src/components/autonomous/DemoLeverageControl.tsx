"use client";

import { useCallback, useState } from "react";

import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { AutonomousTradingSettingsView } from "@/lib/api/types";

import type { Bounds } from "./bounds";
import { LeverageSelect } from "./LeverageSelect";
import { demoLeverageCeiling, leverageValidationMessage } from "./LeverageSettingsSlot";

/** Lowest of the risk profile maximum and the server ceiling; null when neither is known. */
export function leverageCap(profileMax: string | null | undefined, ceiling: number | null): number | null {
  const fromProfile = Number(profileMax);
  const candidates = [Number.isFinite(fromProfile) && fromProfile >= 1 ? Math.floor(fromProfile) : null, ceiling]
    .filter((n): n is number => n != null);
  return candidates.length ? Math.min(...candidates) : null;
}

type Props = {
  view: AutonomousTradingSettingsView;
  bounds: Bounds;
  owner: boolean;
  onSaved: (view: AutonomousTradingSettingsView) => void;
};

/**
 * BloFin demo leverage bound to `demo_leverage` on /autonomous/settings,
 * offered up to the demo risk profile's max leverage. Raising asks first.
 */
export function DemoLeverageControl({ view, bounds, owner, onSaved }: Props) {
  const loader = useCallback(() => api.riskPolicy.profile("demo").catch(() => null), []);
  const { data: profile, loading } = useAsyncData(loader, []);
  const [pending, setPending] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ceiling = demoLeverageCeiling(bounds, view);
  const cap = leverageCap(profile?.profile.max_leverage, ceiling);
  const current = view.demo_leverage ?? null;

  async function save(leverage: number) {
    setSaving(true);
    setError(null);
    try {
      onSaved(await api.autonomous.updateSettings({ demo_leverage: leverage }));
      setPending(null);
    } catch (err) {
      setError(err instanceof ApiError ? leverageValidationMessage(err.message, cap) : "Could not save leverage.");
      setPending(null);
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="text-sm text-text-muted">Loading leverage…</p>;
  return (
    <div className="space-y-1" data-testid="demo-leverage-control">
      <LeverageSelect
        value={current}
        max={cap ?? current ?? 1}
        disabled={!owner || saving || cap == null}
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
