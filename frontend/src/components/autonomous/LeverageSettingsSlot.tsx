"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Row } from "@/components/ui/row";
import type { RiskPolicyView } from "@/lib/api/types";

import { PROFILE_LABEL } from "./profile-labels";

const leverage = (value: string | undefined) => (value ? `${value}×` : "Not set");

/**
 * LEVERAGE INTEGRATION POINT (branch `claude/configurable-leverage`).
 *
 * Placeholder for the Autonomous Trading page. Today it shows the leverage the
 * active risk profiles already carry (read-only, from GET /policy/profiles).
 * The configurable-leverage work replaces this component's body with its own
 * editor and save path; the page renders it at `#leverage` and passes the
 * loaded profiles.
 */
export function LeverageSettingsSlot({ profiles }: { profiles: RiskPolicyView[] }) {
  return (
    <Card id="leverage" data-testid="leverage-settings-slot" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Leverage</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {profiles.length === 0 ? (
          <p className="text-text-muted">Leverage profile unavailable.</p>
        ) : (
          <div className="divide-y divide-border-subtle">
            {profiles.map((p) => (
              <Row
                key={p.kind}
                label={PROFILE_LABEL[p.kind] ?? p.kind}
                value={`Default ${leverage(p.profile.default_leverage)} · max ${leverage(p.profile.max_leverage)}`}
              />
            ))}
          </div>
        )}
        <p className="text-xs text-text-muted" data-testid="leverage-coming-soon">
          Read-only. Configurable leverage is coming soon.
        </p>
      </CardContent>
    </Card>
  );
}
