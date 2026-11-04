"use client";

import { LeverageSettingsSlot } from "@/components/autonomous/LeverageSettingsSlot";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Row } from "@/components/ui/row";
import type { AutonomousTradingSettingsView, RiskPolicyView } from "@/lib/api/types";

import { type Bounds, hasDemoLeverageConfig } from "./bounds";
import { PROFILE_LABEL } from "./profile-labels";

const leverage = (value: string | undefined) => (value ? `${value}×` : "Not set");

type Props = {
  profiles: RiskPolicyView[];
  settings: AutonomousTradingSettingsView | null;
  bounds: Bounds;
};

/**
 * The leverage section at `#leverage`. When GET /autonomous/settings carries
 * the demo leverage fields, `LeverageSettingsSlot` (the demo leverage editor)
 * renders. If the settings could not load, this shows profile leverage and the
 * ceilings read-only.
 */
export function LeverageSection({ profiles, settings, bounds }: Props) {
  if (hasDemoLeverageConfig(settings)) return <LeverageSettingsSlot profiles={profiles} />;
  return (
    <Card id="leverage" data-testid="leverage-settings-slot" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Leverage</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        <div className="divide-y divide-border-subtle">
          {profiles.map((p) => (
            <Row
              key={p.kind}
              label={PROFILE_LABEL[p.kind] ?? p.kind}
              value={`Default ${leverage(p.profile.default_leverage)} · max ${leverage(p.profile.max_leverage)}`}
            />
          ))}
          {bounds ? (
            <Row label="BloFin demo ceiling" value={`${bounds.leverage.demo_effective_max}×`} meta={`Hard cap ${bounds.leverage.demo_hard_cap}×`} />
          ) : null}
        </div>
        {profiles.length === 0 ? <p className="text-text-muted">Leverage profile unavailable.</p> : null}
        <p className="text-xs text-text-muted" data-testid="leverage-unavailable">
          Read-only. Leverage settings unavailable.
        </p>
      </CardContent>
    </Card>
  );
}
