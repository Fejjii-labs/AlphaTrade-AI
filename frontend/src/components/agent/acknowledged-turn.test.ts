import { describe, expect, it } from "vitest";
import { agentTurnFixture, FIXTURE_UUID } from "@/test/pilot-fixtures";
import { acknowledgedMessages, proposalsFromMessages, reconcileMessages } from "./acknowledged-turn";
import type { AgentStructuredProposal } from "@/lib/api/types";

const proposal: AgentStructuredProposal = {
  proposal_id: FIXTURE_UUID, conversation_id: FIXTURE_UUID, organization_id: FIXTURE_UUID,
  user_id: FIXTURE_UUID, kind: "propose_journal_entry", artifact_kind: "journal_entry",
  authority: "explicit_confirmation", provenance: "user_supplied", status: "applied",
  content_hash: "a".repeat(64), summary: "Acknowledged journal decision", payload: {},
  applied: true, authority_mutated: true,
};

describe("acknowledged turn reconciliation", () => {
  it("retains the original upload reference through history with missing source metadata", () => {
    const local = acknowledgedMessages("Organize this source", agentTurnFixture, FIXTURE_UUID);
    const stale = local.map(message => ({ ...message, payload: undefined }));
    expect(reconcileMessages(stale, local)[0].payload?.interactive_agent).toMatchObject({
      source_document_id: FIXTURE_UUID,
    });
  });
  it("deduplicates only exact IDs while retaining repeated text from separate turns", () => {
    const first = acknowledgedMessages("Repeated text", agentTurnFixture);
    const second = acknowledgedMessages("Repeated text", { ...agentTurnFixture,
      user_message_id: "44444444-4444-4444-8444-444444444444",
      assistant_message_id: "55555555-5555-4555-8555-555555555555" });
    expect(reconcileMessages(first, [...first, ...second])).toHaveLength(4);
  });
  it("retains full reply, receipts, evidence and decided proposal through incomplete stale metadata", () => {
    const local = acknowledgedMessages("Record", { ...agentTurnFixture, full_reply: "Full explanation",
      recorded_evidence: "Recorded evidence", proposals: [proposal], saved_entries: [], capture_status: "saved" });
    const stale = local.map(message => ({ ...message, payload: { interactive_agent: {
      full_reply: "Full", recorded_evidence: null, proposals: [{ ...proposal, status: "proposed" }],
      capture: { saved_entries: [], error: null },
    } } }));
    const merged = reconcileMessages(stale, local);
    expect(merged[1].payload?.interactive_agent).toMatchObject({ full_reply: "Full explanation",
      recorded_evidence: "Recorded evidence", capture: { status: "saved" } });
    expect(proposalsFromMessages(merged)).toEqual([proposal]);
  });
  it("does not create actionable proposals from malformed history metadata", () => {
    const messages = acknowledgedMessages("Record", agentTurnFixture);
    messages[1].payload = { interactive_agent: { proposals: [{ ...proposal, authority_mutated: "false" }] } };
    expect(proposalsFromMessages(messages)).toEqual([]);
  });
});


it.each(["user only", "assistant only", "second turn first", "repeated partial"])("preserves two acknowledged pairs through %s history", (mode) => {
  const first = acknowledgedMessages("First user", { ...agentTurnFixture, reply: "First reply" });
  const second = acknowledgedMessages("Second user", { ...agentTurnFixture, reply: "Second reply",
    user_message_id: "44444444-4444-4444-8444-444444444444", assistant_message_id: "55555555-5555-4555-8555-555555555555" });
  const retained = [...first, ...second];
  const partial = mode === "user only" ? [first[0]] : mode === "assistant only" ? [first[1]] : [second[1], first[1]];
  let merged = reconcileMessages(partial, retained);
  if (mode === "repeated partial") merged = reconcileMessages([second[0]], merged);
  expect(merged.map(message => message.id)).toEqual(retained.map(message => message.id));
  expect(merged.map(message => message.content)).toEqual(["First user", "First reply", "Second user", "Second reply"]);
});
