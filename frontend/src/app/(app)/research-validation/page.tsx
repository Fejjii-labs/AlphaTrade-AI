"use client";

import Link from "next/link";
import { useCallback, useState } from "react";

import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api, ApiError, PROMOTE_RESEARCH_VALIDATION_CANDIDATE } from "@/lib/api";
import type {
  ResearchValidationEvidenceItem,
  ResearchValidationLinks,
  ResearchValidationPromoteResult,
  SetupEvidenceTier,
} from "@/lib/api/types";

const WARNING_LABELS: Record<string, string> = {
  insufficient_confirm_sample: "Confirm trade sample is below the recommended threshold.",
  in_sample_only: "Evidence relies on in-sample results only.",
  missing_oos: "Out-of-sample metrics are missing.",
};

type EvidenceLoadResult =
  | { forbidden: true; data: null }
  | { forbidden: false; data: Awaited<ReturnType<typeof api.researchValidation.evidence>> };

function tierLabel(tier: SetupEvidenceTier): string {
  switch (tier) {
    case "tier1":
      return "Tier 1";
    case "tier2":
      return "Tier 2";
    case "tier3":
      return "Tier 3";
    default:
      return tier;
  }
}

function tierBadgeVariant(tier: SetupEvidenceTier): "success" | "warning" | "muted" {
  switch (tier) {
    case "tier1":
      return "success";
    case "tier2":
      return "warning";
    default:
      return "muted";
  }
}

function warningLabel(code: string): string {
  return WARNING_LABELS[code] ?? code.replaceAll("_", " ");
}

function buildJournalLinks(item: ResearchValidationEvidenceItem): ResearchValidationLinks {
  const comparisonParams = new URLSearchParams({ strategy_id: item.strategy_id });
  if (item.strategy_version_id) {
    comparisonParams.set("strategy_version_id", item.strategy_version_id);
  }
  const statsParams = new URLSearchParams();
  if (item.strategy_version_id) {
    statsParams.set("strategy_version_id", item.strategy_version_id);
  }
  statsParams.set("user_strategy_id", item.strategy_id);

  return {
    backtest_run_id: item.backtest_run_id,
    strategy_id: item.strategy_id,
    strategy_version_id: item.strategy_version_id ?? null,
    candidate_id: item.existing_candidate_id ?? null,
    run_plan_id: item.existing_run_plan_id ?? null,
    journal_comparison_path: `/journal/comparison?${comparisonParams}`,
    setup_evidence_path: `/journal/setup-evidence?${comparisonParams}`,
    journal_statistics_path: `/journal/statistics?${statsParams}`,
  };
}

function JournalLinks({ links }: { links: ResearchValidationLinks }) {
  const entries = [
    { href: links.journal_comparison_path, label: "Journal comparison" },
    { href: links.setup_evidence_path, label: "Setup evidence" },
    { href: links.journal_statistics_path, label: "Journal statistics" },
  ].filter((entry): entry is { href: string; label: string } => Boolean(entry.href));

  if (!entries.length) return null;

  return (
    <div className="flex flex-wrap gap-3 text-xs">
      {entries.map((entry) => (
        <Link key={entry.label} href={entry.href} className="text-sky-400 underline">
          {entry.label}
        </Link>
      ))}
    </div>
  );
}

function EvidenceWarnings({ item }: { item: ResearchValidationEvidenceItem }) {
  const banners: string[] = [];

  for (const code of item.warnings) {
    if (code in WARNING_LABELS || ["insufficient_confirm_sample", "in_sample_only", "missing_oos"].includes(code)) {
      banners.push(warningLabel(code));
    }
  }
  if (item.promotion_blocked_reason) {
    banners.push(item.promotion_blocked_reason);
  }

  if (!banners.length) return null;

  return (
    <div className="space-y-2" data-testid={`research-validation-warnings-${item.backtest_run_id}`}>
      {banners.map((message) => (
        <p
          key={message}
          className="rounded border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-200"
        >
          {message}
        </p>
      ))}
    </div>
  );
}

function EvidenceCard({
  item,
  onPromoted,
}: {
  item: ResearchValidationEvidenceItem;
  onPromoted: (result: ResearchValidationPromoteResult) => void;
}) {
  const links = buildJournalLinks(item);
  const [confirm, setConfirm] = useState("");
  const [promoting, setPromoting] = useState(false);
  const [promoteError, setPromoteError] = useState<string | null>(null);
  const [promoteResult, setPromoteResult] = useState<ResearchValidationPromoteResult | null>(null);

  const activeLinks = promoteResult?.links ?? links;
  const candidateId = promoteResult?.candidate.candidate_id ?? item.existing_candidate_id;
  const runPlanId = promoteResult?.links.run_plan_id ?? item.existing_run_plan_id;

  async function handlePromote() {
    setPromoting(true);
    setPromoteError(null);
    try {
      const result = await api.researchValidation.promote({
        confirm,
        backtest_run_id: item.backtest_run_id,
      });
      setPromoteResult(result);
      setConfirm("");
      onPromoted(result);
    } catch (err) {
      setPromoteError(err instanceof Error ? err.message : "Promotion failed.");
    } finally {
      setPromoting(false);
    }
  }

  const canPromote =
    item.eligible_for_promotion && !item.promotion_blocked_reason && !candidateId;

  return (
    <article
      className="space-y-4 rounded-lg border border-zinc-800 p-4"
      data-testid={`research-validation-item-${item.backtest_run_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant={tierBadgeVariant(item.evidence_tier)}>
              {tierLabel(item.evidence_tier)}
            </Badge>
            <span className="text-sm font-medium text-zinc-100">
              {item.strategy_name} v{item.version}
            </span>
            <Badge variant="muted">
              {item.symbol ?? "—"} · {item.timeframe ?? "—"}
            </Badge>
            {item.regime ? <Badge variant="info">{item.regime}</Badge> : null}
          </div>
          <p className="text-xs text-zinc-500">
            <Link href={`/backtests/${item.backtest_run_id}`} className="text-sky-400 underline">
              Open backtest
            </Link>
            {" · "}
            Sample {item.sample_size} · OOS trades {item.oos_trade_count}
          </p>
        </div>
        <Badge variant={item.eligible_for_promotion ? "success" : "muted"}>
          {item.eligible_for_promotion ? "Eligible" : "Not eligible"}
        </Badge>
      </div>

      <div className="grid gap-2 text-xs text-zinc-400 sm:grid-cols-3 lg:grid-cols-6">
        <p>OOS expectancy: {item.oos_expectancy ?? "—"}</p>
        <p>
          OOS PF:{" "}
          {item.oos_profit_factor !== null && item.oos_profit_factor !== undefined
            ? item.oos_profit_factor.toFixed(2)
            : "—"}
        </p>
        <p>Confirm trades: {item.confirm_trade_count}</p>
      </div>

      <EvidenceWarnings item={item} />

      <div className="space-y-2 text-xs">
        <p className="text-zinc-500">Journal progress</p>
        <JournalLinks links={activeLinks} />
      </div>

      {(candidateId || runPlanId) && (
        <div className="flex flex-wrap gap-3 text-xs">
          {candidateId ? (
            <Link
              href={`/paper-validation/candidates/${candidateId}`}
              className="text-sky-400 underline"
              data-testid={`research-validation-candidate-link-${item.backtest_run_id}`}
            >
              View paper validation candidate
            </Link>
          ) : null}
          {runPlanId ? (
            <Link
              href={`/paper-validation/run-plans/${runPlanId}`}
              className="text-sky-400 underline"
              data-testid={`research-validation-run-plan-link-${item.backtest_run_id}`}
            >
              View run plan
            </Link>
          ) : null}
        </div>
      )}

      {promoteResult ? (
        <p className="text-xs text-emerald-300" data-testid={`research-validation-promoted-${item.backtest_run_id}`}>
          {promoteResult.already_exists
            ? "Candidate already exists in the paper validation queue."
            : "Promoted to the paper validation queue."}
        </p>
      ) : null}

      {canPromote ? (
        <div
          className="space-y-3 rounded border border-zinc-700 p-3"
          data-testid={`research-validation-promote-${item.backtest_run_id}`}
        >
          <p className="text-xs text-zinc-400">
            Promote to paper validation — advisory queue only. No orders, no execution.
          </p>
          <p className="text-xs text-zinc-500">
            Type{" "}
            <span className="font-mono text-zinc-100">{PROMOTE_RESEARCH_VALIDATION_CANDIDATE}</span>{" "}
            to confirm.
          </p>
          <Input
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            placeholder="Confirmation phrase"
            data-testid={`research-validation-promote-confirm-${item.backtest_run_id}`}
            className="max-w-md font-mono text-xs"
          />
          <Button
            size="sm"
            disabled={promoting || confirm !== PROMOTE_RESEARCH_VALIDATION_CANDIDATE}
            onClick={() => void handlePromote()}
            data-testid={`research-validation-promote-submit-${item.backtest_run_id}`}
          >
            {promoting ? "Promoting…" : "Promote to paper validation"}
          </Button>
          {promoteError ? <p className="text-xs text-red-400">{promoteError}</p> : null}
        </div>
      ) : null}
    </article>
  );
}

export default function ResearchValidationPage() {
  const loader = useCallback(async (): Promise<EvidenceLoadResult> => {
    try {
      const data = await api.researchValidation.evidence();
      return { forbidden: false, data };
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        return { forbidden: true, data: null };
      }
      throw err;
    }
  }, []);

  const { data: loadResult, loading, error, reload } = useAsyncData(loader, []);
  const [, setRefreshKey] = useState(0);

  function handlePromoted() {
    setRefreshKey((value) => value + 1);
    void reload();
  }

  if (loading && !loadResult) {
    return <LoadingState label="Loading research validation evidence…" />;
  }

  if (error) {
    return <ErrorState message={error} onRetry={() => void reload()} />;
  }

  if (loadResult?.forbidden) {
    return (
      <div className="space-y-4" data-testid="research-validation-forbidden">
        <h1 className="text-2xl font-semibold">Research Validation</h1>
        <div className="rounded-xl border border-amber-500/20 bg-amber-500/5 px-6 py-8 text-center">
          <p className="text-sm text-amber-200">
            You do not have permission to view research validation evidence. Trader or reader access
            is required.
          </p>
        </div>
      </div>
    );
  }

  const evidence = loadResult?.data;

  return (
    <div className="space-y-6" data-testid="research-validation-page">
      <div>
        <h1 className="text-2xl font-semibold">Research Validation</h1>
        <p className="text-sm text-zinc-400">
          Review backtest evidence and optionally promote eligible runs into the paper validation
          queue. Advisory only — never feeds execution or risk decisions.
        </p>
        {evidence?.note ? (
          <p className="mt-2 text-xs text-amber-400/80">{evidence.note}</p>
        ) : null}
      </div>

      {evidence?.items.length ? (
        <div className="space-y-4" data-testid="research-validation-list">
          {evidence.items.map((item) => (
            <EvidenceCard key={item.backtest_run_id} item={item} onPromoted={handlePromoted} />
          ))}
        </div>
      ) : (
        <EmptyState
          title="No research validation evidence"
          description="Complete backtests with strategy versions to see promotion-ready evidence here."
        />
      )}
    </div>
  );
}
