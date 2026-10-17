import { ConfidenceBadge } from "@/components/ConfidenceBadge";
import { RiskBadge } from "@/components/RiskBadge";
import { StatusBadge } from "@/components/StatusBadge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  NarrativeMetadata,
  TradingAnalysisDetail,
  TradingNarrativeDetail,
} from "@/lib/api/types";

export function NarrativePanel({
  narrative,
  narrativeMeta,
  analysis,
}: {
  narrative: TradingNarrativeDetail;
  narrativeMeta?: NarrativeMetadata | null;
  analysis?: TradingAnalysisDetail | null;
}) {
  const source = narrativeMeta?.source ?? "deterministic_fallback";
  const isLlm = source === "llm" && narrativeMeta?.validation_passed;
  const tone = isLlm ? "healthy" : narrativeMeta?.fallback_used ? "warn" : "muted";

  return (
    <Card>
      <CardHeader>
        <div className="flex flex-wrap items-center gap-2">
          <CardTitle className="text-base">Narrative explanation</CardTitle>
          <StatusBadge
            label={isLlm ? "LLM polish" : "Deterministic fallback"}
            tone={tone}
          />
          {narrativeMeta ? (
            <StatusBadge
              label={`${narrativeMeta.provider} · ${narrativeMeta.model}`}
              tone="muted"
            />
          ) : null}
          {analysis ? <RiskBadge level={analysis.risk_level} /> : null}
          {analysis ? <ConfidenceBadge value={analysis.confidence} /> : null}
        </div>
        <p className="text-xs text-zinc-500">
          Explanation only — deterministic analysis and risk engine make decisions, not the LLM.
        </p>
      </CardHeader>
      <CardContent className="space-y-4 text-sm text-zinc-300">
        <div>
          <p className="mb-1 font-medium text-zinc-200">Summary</p>
          <p>{narrative.summary}</p>
        </div>
        <div>
          <p className="mb-1 font-medium text-zinc-200">Setup interpretation</p>
          <p className="text-zinc-400">{narrative.setup_interpretation}</p>
        </div>
        <div>
          <p className="mb-1 font-medium text-zinc-200">Evidence explanation</p>
          <p className="text-zinc-400">{narrative.evidence_explanation}</p>
        </div>
        <div>
          <p className="mb-1 font-medium text-zinc-200">Risk explanation</p>
          <p className="text-zinc-400">{narrative.risk_explanation}</p>
        </div>
        <div>
          <p className="mb-1 font-medium text-zinc-200">Invalidation</p>
          <p className="text-zinc-400">{narrative.invalidation_explanation}</p>
        </div>
        <div>
          <p className="mb-1 font-medium text-zinc-200">Next decision point</p>
          <p className="text-zinc-400">{narrative.next_decision_point}</p>
        </div>
        {(narrative.caution_notes ?? []).length ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Caution</p>
            <ul className="list-disc space-y-1 pl-5 text-zinc-400">
              {(narrative.caution_notes ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {(narrative.limitations ?? []).length ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Limitations</p>
            <ul className="list-disc space-y-1 pl-5 text-zinc-400">
              {(narrative.limitations ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        {(narrative.citations_used ?? []).length ? (
          <div>
            <p className="mb-1 font-medium text-zinc-200">Citations used</p>
            <ul className="list-disc space-y-1 pl-5 text-zinc-400">
              {(narrative.citations_used ?? []).map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="rounded-lg border border-amber-900/50 bg-amber-950/20 p-3 text-amber-200">
          {narrative.paper_mode_disclaimer}
        </p>
      </CardContent>
    </Card>
  );
}
