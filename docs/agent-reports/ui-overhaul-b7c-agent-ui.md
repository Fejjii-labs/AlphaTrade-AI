# UI overhaul B7c: Agent page (Track V)

Agent: Claude Code · Date: 2026-10-27 · Status: Done (PR open, not merged)
Plan: `docs/plans/2026-10-26-ui-overhaul-plan.md`, item **B7c** (plan §1.8).
Branch from `main`; independent of B7a/B7b. Scope: frontend only.
#350's files (`AgentMessageContent.tsx`, `AgentWorkspace.test.tsx`,
`technical-details.tsx`, `format.ts`, `source-names.ts`) are **not touched**.

## What changed

- **Proposals are cards** (`ProposalCard.tsx`). Each card shows the summary and
  one plain status line, colour-coded by the card border:
  - Proposed: "Agent proposed this. Waiting for your decision." (amber)
  - Confirmed, not applied: "Confirmed, not applied yet." (amber)
  - Applied: "Confirmed and applied." (green)
  - Rejected: "You rejected this." (neutral)
  - Refused: "Refused by safety rules." (red)

  Before, the card printed the raw token ("Status proposed. The reply did not
  confirm this proposal.") in amber for every state.
- **Confirm / Reject** only while the proposal waits. The buttons read
  "Confirm" and "Reject"; their accessible names stay "Confirm proposal" and
  "Reject proposal". One short note per kind ("Confirm writes one journal
  entry.") shows only while waiting.
- **Strategy draft as readable rules** (`strategy-rules-text.ts`). The draft's
  "Review rules" disclosure lists Timeframe, Entry, Exit and No trade as
  sentences, e.g. "Long on liquidity sweep when RSI < 30 on 15m". Before, it
  dumped the rules, pattern spec and card as JSON. Empty drafts say "No
  structured rules in this draft." The status token reads "pending review", not
  "pending_review".
- **Header copy cut.** The desktop header is just "Agent" (no eyebrow, no
  caption).

## Decisions taken without asking

- **"Stored evidence" is not removed here.** On current `main` it lives in
  `AgentMessageContent.tsx` (via `TechnicalDetails`), which #350 owns. Removing
  it there would collide with #350, so it is left for #350 or a follow-up.
- **Button names kept for tests and e2e.** `AgentWorkspace.test.tsx` (#350's,
  unchanged) and `e2e/strategy-conversation.spec.ts` find the buttons by name
  and the draft by `agent-strategy-draft`; both still match.
- **Pattern spec and card JSON are not shown at all.** They duplicate the rules
  for a trader and carry internal tokens. Save / compile / approve buttons are
  unchanged.
- **Unknown future statuses** show "Status unknown." in neutral, never the raw
  token.

## Acceptance criteria (plan B7c)

- [x] Proposals as cards with a human status and Confirm/Reject (vitest for
      every status, tone, buttons only while proposed, disabled while deciding).
- [x] Strategy draft rendered as readable rules (vitest: sentences, no braces
      or underscores, empty drafts).
- [x] Header copy cut.
- [x] `AgentWorkspace` tests pass unchanged.
- [x] Screenshots (below).
- [ ] "Stored evidence" removal: out of scope here (#350's file), see above.

## Test evidence

From `frontend/`:

- `npx vitest run src/components/agent/ProposalCard.test.tsx src/components/agent/strategy-rules-text.test.ts`
  → 9 passed.
- `npx vitest run src/components/agent` → 11 files, **128 passed**, including
  the untouched `AgentWorkspace.test.tsx` (52 passed). One run under
  heavy load (4 backend pytest shards in parallel) timed out a single
  long-tail test in `AgentWorkspace.test.tsx`; it passed on an immediate rerun,
  so it is load timing, not this change.
- Mutation check: showing the buttons for every status fails 4 ProposalCard
  tests.
- `tsc --noEmit` → clean. eslint on `src/components/agent` → clean.

## Screenshots

Production builds of `main` (before) and this branch (after), mock backend, a
real strategy-draft proposal created through "Review strategy draft".

- Proposal card, desktop: `ui-overhaul-b7c-agent-ui/before-agent-proposal-1440.png` →
  `ui-overhaul-b7c-agent-ui/after-agent-proposal-1440.png`
- Agent page, phone: `ui-overhaul-b7c-agent-ui/before-agent-390.png` →
  `ui-overhaul-b7c-agent-ui/after-agent-390.png`

## Follow-ups (not in this PR)

- The proposal summary itself ("strategy.create proposal. Ready for explicit
  review.") is written by the backend. A plain summary needs a backend change;
  left for C1a-style backend polish.
- "Stored evidence" in agent messages: #350's file, see above.
