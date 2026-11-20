"use client";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";
import {
  displayedBrainSetup,
  useBrainSetupClock,
} from "@/hooks/useBrainSetupClock";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";

export default function BrainSetupPage() {
  const params = useSearchParams();
  const candidateReturn = params.get("returnTo");
  const returnTo = candidateReturn?.match(
    /^\/strategies(?:\/detections)?(?:\?|$)/,
  )
    ? candidateReturn
    : "/strategies";
  const { id } = useParams<{ id: string }>();
  const loader = useCallback(() => api.strategyBrain.setup(id), [id]);
  const { data: stored, loading, error } = useAsyncData(loader, [id]);
  const setups = useMemo(() => (stored ? [stored] : []), [stored]);
  const now = useBrainSetupClock(setups);
  const data = stored ? displayedBrainSetup(stored, now) : null;
  if (loading && !data) return <p>Loading setup history…</p>;
  if (error || !data) return <p role="alert">{error || "Setup unavailable"}</p>;
  return (
    <div className="space-y-4 p-4">
      <Link
        href={returnTo}
        className="inline-flex min-h-11 items-center text-accent"
      >
        ← Back to Strategies
      </Link>
      <h1 className="text-xl">
        {data.family === "sfp"
          ? `SFP ${data.condition}`
          : data.family === "d_line" ? "D-line" : `Nested ${data.stage}`}{" "}
        · {data.state}
      </h1>
      <p>
        {data.instrument} · {data.direction} · {data.timeframe} · evidence{" "}
        {data.freshness}
      </p>

      <p>
        Observed {data.observed_at} · expires {data.expires_at}
      </p>
      <p>
        Risk: {data.risk_state}. {data.reason_codes.join(", ")}
      </p>
      {data.family === "sfp" ? (
        <p>Structural research only. No SFP execution plan is authorized.</p>
      ) : (
        <p>
          Entry concept {data.entry ?? "unknown"} · stop{" "}
          {data.stop ?? "unknown"} · structural targets{" "}
          {data.targets.join(", ") || "none supported"}
        </p>
      )}
      {data.family === "d_line" && (
        <section className="space-y-2" aria-label="D-line research plan">
          <p>Draft research only. No execution or alerts are authorized. Entry is a reference, not a fill.</p>
          {data.research_plan && <>
            <p>Gross R:R {data.research_plan.gross_rr} · after estimated costs {data.research_plan.net_rr}</p>
            <ul>{data.research_plan.targets.map((target) => (
              <li key={target.price}>{target.price} · {Number(target.fraction) * 100}% · {target.sources.join(", ")}</li>
            ))}</ul>
            <p>Runner concept: {Number(data.research_plan.runner_fraction) * 100}% · aggressive TP: {data.research_plan.aggressive_tp ? "Yes" : "No"}</p>
            <dl>{Object.entries(data.research_plan.confluences).map(([key, value]) => (
              <div key={key}><dt>{key.replaceAll("_", " ")}</dt><dd>{value}</dd></div>
            ))}</dl>
            <p>Unavailable: {data.research_plan.unavailable.map((v) => v.replaceAll("_", " ")).join(", ")}</p>
          </>}
          <h2>Manual reflection · optional</h2>
          <ul>{data.manual_checklist?.map((question) => <li key={question}>{question}</li>)}</ul>
        </section>
      )}
      <details>
        <summary className="min-h-11 cursor-pointer">
          Technical evidence
        </summary>
        <ul>
          {Object.entries(data.evidence).map(([name, state]) => (
            <li key={name}>
              {name}: {state}
            </li>
          ))}
        </ul>
      </details>
      {data.candidate_id && (
        <Link
          href={`/decision/candidates/${data.candidate_id}`}
          className="block underline"
        >
          Governed Candidate
        </Link>
      )}
      {data.journal && (
        <p>
          Journal{" "}
          <Link
            href={`/journal?trade_id=${data.journal.id}`}
            className="underline"
          >
            Open exact trade
          </Link>{" "}
          · {data.journal.status} · realized PnL{" "}
          {data.journal.net_pnl ?? "not available"}
        </p>
      )}
      <h2>Stored lifecycle</h2>
      <ul className="space-y-3">
        {data.history?.map((event) => (
          <li
            key={event.record_id}
            className="break-words rounded border border-border p-3"
          >
            {event.occurred_at} · {event.kind}
            <p className="text-sm">
              {String(
                event.payload.state ||
                  event.payload.observation ||
                  event.payload.provenance ||
                  "Stored record",
              )}
            </p>
          </li>
        ))}
      </ul>
      <p>
        Insufficient history for expectancy. User observations, system
        detections and actual outcomes remain distinct.
      </p>
    </div>
  );
}
