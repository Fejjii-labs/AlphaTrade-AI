"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { RiskPolicyView } from "@/lib/api/types";
import { formatDateTime } from "@/lib/format";

import { PROFILE_LABEL } from "./profile-labels";


const GRADES = [
  ["A+", "risk_percent_a_plus"],
  ["A", "risk_percent_a"],
  ["B+", "risk_percent_b_plus"],
  ["B", "risk_percent_b"],
  ["C", "risk_percent_c"],
] as const;

const CAPS = [
  ["Max notional per trade (per coin)", "max_notional_per_trade_percent", "%"],
  ["Max total exposure (notional)", "max_aggregate_notional_percent", "%"],
  ["Max open risk (all positions)", "max_aggregate_open_risk_percent", "%"],
  ["Max open positions", "max_open_positions", ""],
] as const;

const show = (value: string | undefined, unit: string) => (value ? `${value}${unit}` : "—");

/**
 * Risk per setup grade and position-size caps from the active risk profiles.
 * Read-only: profile changes go through the governed proposal/apply flow.
 */
export function GradeRiskCard({ profiles }: { profiles: RiskPolicyView[] }) {
  return (
    <Card id="grade-risk" data-testid="grade-risk-card" className="scroll-mt-4">
      <CardHeader>
        <CardTitle>Grade risk and position caps</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {profiles.length === 0 ? (
          <p className="text-text-muted">Risk profile unavailable.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left" data-testid="grade-risk-table">
              <thead>
                <tr className="text-xs uppercase text-text-muted">
                  <th scope="col" className="py-1 pr-3 font-medium">Risk per trade</th>
                  {profiles.map((p) => (
                    <th key={p.kind} scope="col" className="py-1 pr-3 text-end font-medium">
                      {PROFILE_LABEL[p.kind] ?? p.kind}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {GRADES.map(([grade, key]) => (
                  <tr key={key}>
                    <th scope="row" className="py-1.5 pr-3 font-normal text-text-secondary">Grade {grade}</th>
                    {profiles.map((p) => (
                      <td key={p.kind} className="py-1.5 pr-3 text-end font-data">{show(p.profile[key], "%")}</td>
                    ))}
                  </tr>
                ))}
                {CAPS.map(([label, key, unit]) => (
                  <tr key={key}>
                    <th scope="row" className="py-1.5 pr-3 font-normal text-text-secondary">{label}</th>
                    {profiles.map((p) => (
                      <td key={p.kind} className="py-1.5 pr-3 text-end font-data">{show(p.profile[key], unit)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="text-xs text-text-muted" data-testid="grade-risk-note">
          The bot uses these when its risk per trade is empty. Changes go through a risk-policy proposal and human
          approval; they apply to new trades only.
          {profiles
            .filter((p) => p.version_number != null)
            .map((p) => ` ${PROFILE_LABEL[p.kind] ?? p.kind}: v${p.version_number} since ${formatDateTime(p.effective_at)}.`)
            .join("")}
        </p>
      </CardContent>
    </Card>
  );
}
