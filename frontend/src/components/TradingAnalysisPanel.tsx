import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { RiskBadge } from "@/components/RiskBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { TradingAnalysisDetail } from "@/lib/api/types";

export function TradingAnalysisPanel({ analysis }: { analysis: TradingAnalysisDetail }) {
  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">Deterministic analysis (source of truth)</CardTitle>
          <RiskBadge level={analysis.risk_level} />
          <ConfidenceBadge value={analysis.confidence} />
          <StatusBadge label={analysis.approval_status} tone="pending" />
          <StatusBadge
            label={
              analysis.market_data_quality === "live"
                ? "Live market data"
                : analysis.market_data_quality === "stale"
                  ? "Stale market data"
                  : "Mock market data"
            }
            tone={
              analysis.market_data_quality === "live"
                ? "healthy"
                : analysis.market_data_quality === "stale"
                  ? "warn"
                  : "paper"
            }
          />
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-zinc-300">
        <div>
          <p className="mb-1 font-medium text-zinc-200">Summary</p>
          <p>{analysis.summary}</p>
        </div>
        {analysis.setup_type ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Setup type</p>
            <p>{analysis.setup_type}</p>
          </div>
        ) : null}
        {(analysis.evidence ?? []).length ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Evidence</p>
            <ul className="list-disc space-y-1 pl-5 text-zinc-400">
              {(analysis.evidence ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {analysis.invalidation ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Invalidation</p>
            <p className="text-zinc-400">{analysis.invalidation}</p>
          </div>
        ) : null}
        <div>
          <p className="mb-1 font-medium text-zinc-200">Stop loss / no-trade reason</p>
          <p className="text-zinc-400">{analysis.stop_loss_or_no_trade_reason}</p>
        </div>
        {analysis.next_decision_point ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Next decision point</p>
            <p className="text-zinc-400">{analysis.next_decision_point}</p>
          </div>
        ) : null}
        {analysis.paper_mode_disclaimer ? (
          <p className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 text-amber-200">
            {analysis.paper_mode_disclaimer}
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
