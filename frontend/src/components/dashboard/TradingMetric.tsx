import { DataNumber } from "@/components/ui/data-number";
import { UNAVAILABLE } from "@/lib/format";

/** Shared presentation for recorded trading metrics; never supplies a fallback value. */
export function TradingMetric({
  label,
  value,
  note,
  testId,
  amount,
}: {
  label: string;
  value: string;
  note?: string | null;
  testId?: string;
  amount?: string | number | null;
}) {
  const numeric = amount == null || amount === "" ? null : Number(amount);
  const tone =
    value === UNAVAILABLE
      ? "muted"
      : numeric != null && Number.isFinite(numeric) && numeric !== 0
        ? numeric > 0
          ? "positive"
          : "negative"
        : "default";

  return (
    <div
      data-testid={testId}
      className="min-w-0 rounded-card border border-border-subtle bg-surface-1 p-4"
    >
      <p className="text-[0.8125rem] font-medium uppercase tracking-wide text-text-muted">{label}</p>
      <DataNumber
        value={value}
        tone={tone}
        className="mt-2 block break-words text-[length:var(--text-title)] font-bold leading-[1.25] tracking-[-0.015em]"
      />
      {note ? (
        <p className="mt-2 text-[length:var(--text-caption)] leading-[1.4] text-text-muted">
          {note}
        </p>
      ) : null}
    </div>
  );
}
