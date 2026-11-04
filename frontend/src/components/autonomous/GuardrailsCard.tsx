"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Row } from "@/components/ui/row";

import type { Bounds } from "./bounds";
import { PROFILE_LABEL } from "./profile-labels";

/**
 * Stop-loss band and the hard ceilings every limit on this page is checked
 * against. Read-only: the band is deployment config and the ceilings are code.
 * Values come from GET /autonomous/settings/bounds.
 */
export function GuardrailsCard({ bounds }: { bounds: Bounds }) {
  return (
    <Card id="guardrails" data-testid="guardrails-card" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Stop-loss band and hard ceilings</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {bounds == null ? (
          <p className="text-text-muted" data-testid="guardrails-unavailable">
            Ceilings unavailable. The server still enforces them on every save.
          </p>
        ) : (
          <>
            <div className="divide-y divide-border-subtle" data-testid="guardrails-stop-loss-band">
              <Row
                label="Stop-loss band (BloFin demo)"
                value={`${bounds.stop_loss_band.configured_min_percent}%–${bounds.stop_loss_band.configured_max_percent}% of equity per trade`}
                meta={`Allowed ${bounds.stop_loss_band.floor_percent}%–${bounds.stop_loss_band.ceiling_percent}%`}
              />
              {bounds.stop_loss_band.configured_valid ? null : (
                <p className="py-1.5 text-danger" role="alert" data-testid="guardrails-band-invalid">
                  The configured band is outside the allowed range, so demo plans are refused.
                </p>
              )}
              <Row label="Bot risk per trade" value={`Max ${bounds.bot_risk_per_trade_percent.max}%`} />
              <Row label="Bot daily loss limit" value={`Max ${bounds.bot_daily_loss_limit_percent.max}%`} />
              <Row label="Account risk per trade" value={`Max ${bounds.account_max_risk_per_trade_percent.max}%`} />
              <Row
                label="Leverage"
                value={`Demo max ${bounds.leverage.demo_effective_max}×`}
                meta={`Hard cap ${bounds.leverage.demo_hard_cap}×`}
              />
              {bounds.profiles.map((p) => (
                <Row
                  key={p.kind}
                  label={`${PROFILE_LABEL[p.kind] ?? p.kind} profile`}
                  value={`Notional per coin and total ≤ ${p.notional_percent_ceiling}% of equity`}
                  meta={`Open risk ≤ ${p.aggregate_open_risk_percent_ceiling}%`}
                />
              ))}
            </div>
            <p className="text-xs text-text-muted" data-testid="guardrails-how-to-change">
              Read-only here. The stop-loss band is deployment config (GOVERNED_DEMO_STOP_LOSS_MIN/MAX_PERCENT). Per-coin
              notional and exposure caps are risk-profile values (see Grade risk) and change through a risk-policy
              proposal with human approval. Real-money trading stays blocked.
            </p>
          </>
        )}
      </CardContent>
    </Card>
  );
}
