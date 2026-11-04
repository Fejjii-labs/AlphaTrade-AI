"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Row } from "@/components/ui/row";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError } from "@/lib/api";
import type { AutonomousTradingSettingsView, DemoMarginMode, RiskPolicyView } from "@/lib/api/types";

import { PROFILE_LABEL } from "./profile-labels";

const leverage = (value: string | undefined) => (value ? `${value}×` : "Not set");

type OverrideRow = { symbol: string; leverage: string; margin_mode: DemoMarginMode | "" };

type LeverageForm = {
  demo_leverage: string;
  demo_margin_mode: DemoMarginMode;
  demo_overrides: OverrideRow[];
};

// Mirrors the server defaults; the server enforces the same 1–20x range.
const DEFAULT_DEMO_LEVERAGE = 10;
const DEFAULT_DEMO_LEVERAGE_CEILING = 20;
const selectClass = "min-h-11 w-full rounded-control border border-border-subtle bg-surface-1 px-3";

function toLeverageForm(data: AutonomousTradingSettingsView): LeverageForm {
  return {
    demo_leverage: String(data.demo_leverage ?? DEFAULT_DEMO_LEVERAGE),
    demo_margin_mode: data.demo_margin_mode ?? "isolated",
    demo_overrides: Object.entries(data.demo_leverage_overrides ?? {}).map(([symbol, value]) => ({
      symbol,
      leverage: value.leverage == null ? "" : String(value.leverage),
      margin_mode: value.margin_mode ?? "",
    })),
  };
}

/** Whole number within 1..ceiling, or null. Blank is allowed only where `optional`. */
function leverageError(text: string, ceiling: number, optional = false): string | null {
  const trimmed = text.trim();
  if (!trimmed) return optional ? null : "Leverage is required.";
  if (!/^\d+$/.test(trimmed)) return "Leverage must be a whole number.";
  const value = Number(trimmed);
  if (value < 1) return "Leverage must be at least 1x.";
  if (value > ceiling) return `Leverage cannot exceed ${ceiling}x.`;
  return null;
}

export function leverageValidationMessage(code: string, ceiling: number): string {
  const map: Record<string, string> = {
    demo_leverage_exceeds_ceiling: `Leverage cannot exceed ${ceiling}x (code ceiling).`,
    demo_leverage_below_one: "Leverage must be at least 1x.",
    demo_leverage_not_whole: "Leverage must be a whole number.",
    demo_leverage_invalid: "Leverage must be a number.",
    demo_margin_mode_invalid: "Margin mode must be isolated or cross.",
    demo_leverage_override_symbol_invalid: "Each per-coin override needs a unique USDT coin.",
    demo_leverage_overrides_invalid: "Each per-coin override must set leverage or margin mode.",
    demo_leverage_overrides_too_many: "Too many per-coin overrides.",
  };
  return map[code] ?? `Could not save leverage (${code}).`;
}

function toLeveragePayload(form: LeverageForm): Record<string, unknown> {
  const rows = form.demo_overrides.filter((row) => row.symbol);
  return {
    demo_leverage: Number(form.demo_leverage.trim()),
    demo_margin_mode: form.demo_margin_mode,
    ...(rows.length === 0
      ? { clear_demo_leverage_overrides: true }
      : {
          demo_leverage_overrides: Object.fromEntries(
            rows.map((row) => [
              row.symbol,
              {
                ...(row.leverage.trim() ? { leverage: Number(row.leverage.trim()) } : {}),
                ...(row.margin_mode ? { margin_mode: row.margin_mode } : {}),
              },
            ]),
          ),
        }),
  };
}

/**
 * BloFin demo leverage, margin mode and per-coin overrides. Owner-only writes
 * through PATCH /autonomous/settings (only the demo leverage fields are sent).
 */
function DemoLeverageEditor() {
  const { membershipRole } = useAuth();
  const loader = useCallback(() => api.autonomous.settings(), []);
  const { data, loading, error } = useAsyncData(loader, []);
  const [view, setView] = useState<AutonomousTradingSettingsView | null>(null);
  const [form, setForm] = useState<LeverageForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (data) {
      setView(data);
      setForm(toLeverageForm(data));
    }
  }, [data]);

  if (loading) return <p className="text-text-muted">Loading demo leverage…</p>;
  if (error || !view || !form) return <p className="text-text-muted">Demo leverage settings unavailable.</p>;

  const owner = membershipRole === "owner";
  const ceiling = view.max_demo_leverage_ceiling ?? DEFAULT_DEMO_LEVERAGE_CEILING;
  const setRow = (index: number, patch: Partial<OverrideRow>) => {
    const rows = [...form.demo_overrides];
    rows[index] = { ...rows[index], ...patch };
    setForm({ ...form, demo_overrides: rows });
  };

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!form || saving) return;
    setSaveError(null);
    setSaved(false);
    const rows = form.demo_overrides.filter((row) => row.symbol);
    const localError =
      leverageError(form.demo_leverage, ceiling) ??
      rows.map((row) => leverageError(row.leverage, ceiling, true)).find(Boolean) ??
      (rows.some((row) => !row.leverage.trim() && !row.margin_mode)
        ? "Each per-coin override must set leverage or margin mode."
        : null);
    if (localError) {
      setSaveError(localError);
      return;
    }
    setSaving(true);
    try {
      const updated = await api.autonomous.updateSettings(toLeveragePayload(form));
      setView(updated);
      setForm(toLeverageForm(updated));
      setSaved(true);
    } catch (err) {
      setSaveError(
        err instanceof ApiError ? leverageValidationMessage(err.message, ceiling) : "Could not save leverage.",
      );
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="space-y-3" onSubmit={(event) => void handleSubmit(event)} data-testid="demo-leverage-settings">
      <p className="text-xs text-text-muted">
        BloFin demo only. Once a demo trade passes its checks, right before entry the app sets this leverage and margin
        mode on BloFin demo and checks it back; a mismatch or a liquidation price too close to the stop blocks the
        trade. Position size still comes from risk per trade and the limits above.
      </p>
      <p className="text-xs text-text-muted" data-testid="demo-leverage-size-hint">
        At the current total-exposure limit, higher leverage only lowers the margin used and moves liquidation closer.
        It does not increase trade size.
      </p>
      <fieldset disabled={!owner || saving} className="space-y-3">
        <div className="grid gap-4 md:grid-cols-3">
          <label className="space-y-1 text-sm">
            <span className="text-text-secondary">Leverage (x)</span>
            <Input
              data-testid="demo-leverage"
              inputMode="numeric"
              value={form.demo_leverage}
              onChange={(e) => setForm({ ...form, demo_leverage: e.target.value })}
            />
            <span className="block text-xs text-text-muted">
              1–{ceiling}x.{view.demo_leverage_using_default === false ? "" : " Default 10x isolated."}
            </span>
          </label>
          <label className="space-y-1 text-sm">
            <span className="text-text-secondary">Margin mode</span>
            <select
              data-testid="demo-margin-mode"
              className={selectClass}
              value={form.demo_margin_mode}
              onChange={(e) => setForm({ ...form, demo_margin_mode: e.target.value as DemoMarginMode })}
            >
              <option value="isolated">Isolated</option>
              <option value="cross">Cross</option>
            </select>
          </label>
        </div>
        <div className="space-y-2" data-testid="demo-leverage-overrides">
          <span className="text-sm text-text-secondary">Per-coin overrides</span>
          {form.demo_overrides.map((row, index) => (
            <div key={index} className="grid gap-2 md:grid-cols-4" data-testid="demo-leverage-override-row">
              <select
                aria-label="Override coin"
                className={selectClass}
                value={row.symbol}
                onChange={(e) => setRow(index, { symbol: e.target.value })}
              >
                <option value="">Select coin</option>
                {[...new Set([...view.symbol_options, row.symbol].filter(Boolean))].map((symbol) => (
                  <option key={symbol} value={symbol}>
                    {symbol}
                  </option>
                ))}
              </select>
              <Input
                aria-label="Override leverage"
                inputMode="numeric"
                placeholder="Leverage"
                value={row.leverage}
                onChange={(e) => setRow(index, { leverage: e.target.value })}
              />
              <select
                aria-label="Override margin mode"
                className={selectClass}
                value={row.margin_mode}
                onChange={(e) => setRow(index, { margin_mode: e.target.value as DemoMarginMode | "" })}
              >
                <option value="">Same mode</option>
                <option value="isolated">Isolated</option>
                <option value="cross">Cross</option>
              </select>
              <Button
                type="button"
                variant="outline"
                onClick={() => setForm({ ...form, demo_overrides: form.demo_overrides.filter((_, i) => i !== index) })}
              >
                Remove
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            data-testid="demo-leverage-add-override"
            onClick={() =>
              setForm({ ...form, demo_overrides: [...form.demo_overrides, { symbol: "", leverage: "", margin_mode: "" }] })
            }
          >
            Add per-coin override
          </Button>
        </div>
        <Button type="submit" data-testid="demo-leverage-save">
          {saving ? "Saving…" : "Save leverage"}
        </Button>
      </fieldset>
      {!owner ? (
        <p className="text-text-muted" data-testid="demo-leverage-owner-reason">
          Owner only.
        </p>
      ) : null}
      {saveError ? (
        <p className="text-sm text-danger" role="alert">
          {saveError}
        </p>
      ) : null}
      {saved ? (
        <p className="text-sm text-success" role="status">
          Leverage saved.
        </p>
      ) : null}
    </form>
  );
}

/**
 * Leverage on the Autonomous Trading page (`#leverage`): the BloFin demo
 * leverage editor, plus the leverage the active risk profiles carry
 * (read-only, from GET /policy/profiles).
 */
export function LeverageSettingsSlot({ profiles }: { profiles: RiskPolicyView[] }) {
  return (
    <Card id="leverage" data-testid="leverage-settings-slot" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Leverage</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <DemoLeverageEditor />
        {profiles.length === 0 ? (
          <p className="text-text-muted">Leverage profile unavailable.</p>
        ) : (
          <div className="divide-y divide-border-subtle" data-testid="leverage-profile-rows">
            {profiles.map((p) => (
              <Row
                key={p.kind}
                label={PROFILE_LABEL[p.kind] ?? p.kind}
                value={`Default ${leverage(p.profile.default_leverage)} · max ${leverage(p.profile.max_leverage)}`}
              />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
