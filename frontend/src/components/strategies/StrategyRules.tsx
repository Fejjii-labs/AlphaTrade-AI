import { Row } from "@/components/ui/row";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HIDDEN = new Set(["kind", "version", "provisional", "paper_only"]);

function label(key: string): string {
  const text = key.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

function display(value: unknown): string | null {
  if (value == null || value === "") return null;
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return String(value);
  if (typeof value === "string") return UUID.test(value) ? null : value.replaceAll("_", " ");
  if (Array.isArray(value) && value.every((v) => typeof v !== "object"))
    return value.map(String).join(", ") || null;
  return null;
}

/** A strategy spec as readable rule rows. Nested objects, IDs and schema markers are left out. */
export function StrategyRules({ spec }: { spec: object }) {
  const { parameters, ...top } = spec as Record<string, unknown> & { parameters?: Record<string, unknown> };
  const rows = [...Object.entries(top), ...Object.entries(parameters ?? {})]
    .filter(([key]) => !HIDDEN.has(key) && !key.endsWith("_id"))
    .map(([key, value]) => [label(key), display(value)] as const)
    .filter((row): row is readonly [string, string] => row[1] !== null);
  if (rows.length === 0) return <p className="text-sm text-text-muted">No rules recorded.</p>;
  return (
    <dl className="divide-y divide-border-subtle" data-testid="strategy-rules">
      {rows.map(([key, value]) => (
        <Row key={key} definition label={key} value={value} />
      ))}
    </dl>
  );
}
