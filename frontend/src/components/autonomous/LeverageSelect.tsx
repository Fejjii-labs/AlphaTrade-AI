"use client";

const selectClass = "min-h-11 rounded-control border border-border-subtle bg-surface-1 px-3 text-sm";

/** Demo leverage is the owner's pick from 1x to 10x; the risk profile does not cap it. */
export const DEMO_LEVERAGE_MAX = 10;

export type LeverageSelectProps = {
  /** Saved leverage, or null when unknown. */
  value: number | null;
  onChange: (leverage: number) => void;
  disabled?: boolean;
  label?: string;
};

/** Whole-number choices 1..10, always. */
export function leverageChoices(): number[] {
  return Array.from({ length: DEMO_LEVERAGE_MAX }, (_, i) => i + 1);
}

/** Leverage dropdown. Presentational: the caller owns saving. */
export function LeverageSelect({ value, onChange, disabled, label = "Leverage" }: LeverageSelectProps) {
  const choices = leverageChoices();
  // A saved value outside 1..10 (older settings) shows as unset until a new pick.
  const selected = value != null && choices.includes(value) ? value : null;
  return (
    <label className="flex items-center justify-between gap-3 text-sm">
      <span className="text-text-secondary">{label}</span>
      <select
        aria-label={label}
        data-testid="leverage-select"
        className={selectClass}
        value={selected == null ? "" : String(selected)}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {selected == null ? <option value="">—</option> : null}
        {choices.map((choice) => (
          <option key={choice} value={choice}>
            {choice}×
          </option>
        ))}
      </select>
    </label>
  );
}
