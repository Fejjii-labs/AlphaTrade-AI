"use client";

const selectClass = "min-h-11 rounded-control border border-border-subtle bg-surface-1 px-3 text-sm";

/** Preset demo leverage choices, always listed. The risk profile does not cap them. */
export const LEVERAGE_PRESETS = [2, 3, 5, 10] as const;

export type LeverageSelectProps = {
  /** Saved leverage, or null when unknown. */
  value: number | null;
  onChange: (leverage: number) => void;
  disabled?: boolean;
  label?: string;
};

/** The presets, plus the saved value when it is not one of them (so it stays selected). */
export function leverageChoices(value: number | null): number[] {
  const choices: number[] = [...LEVERAGE_PRESETS];
  if (value != null && Number.isFinite(value) && value > 0 && !choices.includes(value)) choices.push(value);
  return choices.sort((a, b) => a - b);
}

/** Leverage dropdown. Presentational: the caller owns saving. */
export function LeverageSelect({ value, onChange, disabled, label = "Leverage" }: LeverageSelectProps) {
  const choices = leverageChoices(value);
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
