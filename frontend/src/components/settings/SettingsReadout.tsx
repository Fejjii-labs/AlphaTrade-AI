import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Row } from "@/components/ui/row";

export function SettingsReadout({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="divide-y divide-border-subtle">
      {rows.map(([label, value]) => (
        <Row key={label} definition label={label} value={value} />
      ))}
    </dl>
  );
}

export function SettingsUnavailable({
  label,
  loading,
  onRetry,
}: {
  label: string;
  loading: boolean;
  onRetry?: () => void;
}) {
  return (
    <div role="status" className="space-y-2 text-sm text-text-muted">
      <p>
        {loading ? `Loading ${label.toLowerCase()}…` : `${label} unavailable.`}
      </p>
      {!loading && onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Retry {label.toLowerCase()}
        </Button>
      ) : null}
    </div>
  );
}
