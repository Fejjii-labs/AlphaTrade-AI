"use client";

const selectClass = "min-h-11 rounded-control border border-border-subtle bg-surface-1 px-3 text-sm";

export type LeverageSelectProps = {
  /** Saved leverage, or null when unknown. */
  value: number | null;
  /** Highest choice offered (for example the risk profile maximum). */
  max: number;
  onChange: (leverage: number) => void;
  disabled?: boolean;
  label?: string;
};

/** Whole-number choices 1..max, plus the saved value when it sits above max. */
export function leverageChoices(value: number | null, max: number): number[] {
  const top = Math.max(1, Math.floor(max));
  const choices = Array.from({ length: top }, (_, i) => i + 1);
  if (value != null && value > top) choices.push(value);
  return choices;
}

/** Leverage dropdown. Presentational: the caller owns saving. */
export function LeverageSelect({ value, max, onChange, disabled, label = "Leverage" }: LeverageSelectProps) {
  const choices = leverageChoices(value, max);
  return (
    <label className="flex items-center justify-between gap-3 text-sm">
      <span className="text-text-secondary">{label}</span>
      <select
        aria-label={label}
        data-testid="leverage-select"
        className={selectClass}
        value={value == null ? "" : String(value)}
        disabled={disabled}
        onChange={(event) => onChange(Number(event.target.value))}
      >
        {value == null ? <option value="">—</option> : null}
        {choices.map((choice) => (
          <option key={choice} value={choice} disabled={choice > max}>
            {choice}×{choice > max ? " (above max)" : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
