"use client";

const selectClass = "min-h-11 rounded-control border border-border-subtle bg-surface-1 px-3 text-sm";

const LEVERAGE_CHOICES = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];

export type LeverageSelectProps = {
  /** Saved leverage, or null when unknown. */
  value: number | null;
  onChange: (leverage: number) => void;
  disabled?: boolean;
  label?: string;
};

/** Keep an older saved value visible until the user selects a supported value. */
export function leverageChoices(value: number | null): number[] {
  if (value != null && Number.isFinite(value) && value > 10) return [...LEVERAGE_CHOICES, value];
  return LEVERAGE_CHOICES;
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
          <option key={choice} value={choice} disabled={choice > 10}>
            {choice}×
          </option>
        ))}
      </select>
    </label>
  );
}
