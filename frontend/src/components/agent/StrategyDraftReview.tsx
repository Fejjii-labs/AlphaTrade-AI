"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { StrategyProposalRecord } from "@/lib/api/types";
import { Button } from "@/components/ui/button";
import { strategyRuleLines } from "./strategy-rules-text";

export function StrategyDraftReview({ conversationId, proposalId }: { conversationId: string; proposalId: string }) {
  const [proposal, setProposal] = useState<StrategyProposalRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [compiled, setCompiled] = useState<string | null>(null);
  const [lifecycle, setLifecycle] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void api.conversations.getProposal(conversationId, proposalId).then(value => {
      if (current && value.conversation_id === conversationId && value.id === proposalId) setProposal(value);
    }).catch(() => { if (current) setError("Strategy draft could not be loaded."); });
    return () => { current = false; };
  }, [conversationId, proposalId]);

  async function apply(action: "save" | "compile" | "approve") {
    if (!proposal || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (action === "save" && proposal.content_hash) {
        const saved = await api.conversations.confirmProposal(conversationId, proposalId, {
          confirm: "I confirm", request_id: proposalId,
          expected_content_hash: proposal.content_hash,
          expected_parent_version_id: proposal.parent_version_id ?? null,
          expected_target_strategy_id: proposal.target_strategy_id ?? null,
          expected_organization_id: proposal.organization_id,
          expected_user_id: proposal.user_id, expected_conversation_id: conversationId,
        });
        setProposal(saved);
      } else if (proposal.resulting_strategy_id && proposal.resulting_version_id) {
        if (action === "compile") {
          const result = await api.strategies.compileVersion(proposal.resulting_strategy_id, proposal.resulting_version_id);
          setCompiled(result.status);
        } else if (action === "approve" && compiled === "executable") {
          const result = await api.strategies.approveVersion(proposal.resulting_strategy_id, proposal.resulting_version_id, { confirm: "I confirm" });
          setLifecycle(result.new_state);
        }
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : "Strategy action failed; recover the same draft before making another.");
    } finally { setBusy(false); }
  }

  const rules = strategyRuleLines(proposal?.proposed_structured_rules);
  return <div className="mt-3 space-y-2 border-t border-border-subtle pt-3" data-testid="agent-strategy-draft">
    {error ? <p role="alert">{error}</p> : null}
    {proposal ? <>
      <p className="text-sm font-medium">Strategy draft · {proposal.status.replaceAll("_", " ")}</p>
      {(proposal.challenge_notes ?? []).slice(0, 3).map(note => <p key={note} className="text-sm">{note}</p>)}
      <details><summary className="cursor-pointer text-sm">Review rules</summary>
        {rules.length ? <dl className="mt-2 space-y-2 text-sm" data-testid="agent-strategy-rules">
          {rules.map(({ group, lines }) => <div key={group}>
            <dt className="text-xs text-text-muted">{group}</dt>
            {lines.map((line, index) => <dd key={index}>{line}</dd>)}
          </div>)}
        </dl> : <p className="mt-2 text-sm text-text-muted">No structured rules in this draft.</p>}
      </details>
      <details data-testid="agent-strategy-raw"><summary className="cursor-pointer text-sm">Raw draft (rules, pattern, card)</summary>
        <pre className="whitespace-pre-wrap break-words text-sm">{JSON.stringify({
          rules: proposal.proposed_structured_rules, pattern: proposal.proposed_pattern_spec, card: proposal.proposed_card,
        }, null, 2)}</pre>
      </details>
      <p className="text-xs">Saving creates a version. Compilation and approval remain separate.</p>
      {proposal.status === "draft" ? <Button disabled={busy || !proposal.content_hash || !proposal.validation.valid}
        onClick={() => void apply("save")}>Confirm and save strategy version</Button> : null}
      {proposal.resulting_strategy_id ? <>
        <Link className="underline" href={`/strategy-lab/${encodeURIComponent(proposal.resulting_strategy_id)}`}>Open saved strategy</Link>
        <Button variant="outline" disabled={busy} onClick={() => void apply("compile")}>Compile saved version</Button>
        {compiled ? <p data-testid="agent-strategy-compile-status">Compilation: {compiled}</p> : null}
        <Button variant="outline" disabled={busy || compiled !== "executable" || lifecycle === "approved"} onClick={() => void apply("approve")}>Approve compiled policy</Button>
        {lifecycle ? <p data-testid="agent-strategy-lifecycle">{lifecycle}</p> : null}
      </> : null}
    </> : <p>Loading strategy draft…</p>}
  </div>;
}
