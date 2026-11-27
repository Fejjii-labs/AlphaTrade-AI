# Technical interview walkthrough

Read [the README](../README.md) for the product overview and [the timed pitch](interview_pitch.md) for a spoken introduction. This guide supplies technical depth and vocabulary without replacing the authoritative architecture/security/Agent guides.

Baseline: main `b58beda`, October 7, 2026. [Current status](current_status.md) records what was source-verified, supervisor-reported and still pending.

[12-slide presentation](reviewer_presentation.md) · [Five-minute demo](demo_script.md) · [Evidence checklist](reviewer_evidence.md)

## Follow one decision

1. **Evidence:** public Binance/Bybit perpetual observations carry instrument, venue, contract, time, finality and policy identity. Canonical checks preserve stale/unavailable/conflicting states.
2. **Strategy:** an immutable version must be compiled and approved for supported canonical detection. Nested and SFP have different maturity/execution support; an arbitrary prompt is not a detector.
3. **Candidate:** only canonical lifecycle authority can persist a genuine confirmed setup. A Candidate is an opportunity to assess, not a fill.
4. **Risk and authority:** eligibility, deterministic sizing/risk, kill switch, permissions and exact plan authorization gate execution. The operator may explicitly arm an approved-strategy paper continuation; supported user actions have their own confirmation.
5. **Execution and Journal:** durable claims/receipts link the actual paper fill to the canonical Journal and learning attribution. Acknowledgment, planned price and model narration do not create fill facts.
6. **Review:** analytics and learning status explain the record. A proposed strategy improvement must meet its separate validation and human-promotion gates.

Use [the system/hosting and lifecycle diagrams](architecture.md) to explain the handoffs. Source identities, fences and plan hashes address different failure modes; none is a substitute for all the others.

## Explain the AI layer precisely

The current `/agent` workspace uses typed read/proposal tools and a conversational responder routed by purpose. `GENERAL_AGENT_SYNTHESIS` is Tier A (default `gpt-6.1-sol`); the base provider default `LLM_MODEL=gpt-6-luna` is not proof of a routed turn's selected/resolved model. Tier B defaults to `gpt-6-luna`. Canonical defaults live in `backend/src/app/core/config.py`. Actual deployed model resolution is UNKNOWN here.

Default Agent knowledge retrieval is lexical SQL with a bounded scan; optional vector hits require injection and scoped SQL reload. The knowledge service separately embeds/searches vectors. Conversation history, selected strategy context, Journal and learning records are stored application memory. They are not continuous training or unlimited model recall.

Model prose cannot confirm, save or activate an action. Explain the separate structured proposal, Confirm/Reject request, state/scope revalidation and domain service. [Agent diagram and tools](agent_workflow.md) · [retrieval](rag_system.md) · [security](security.md).

## Discuss tradeoffs and failure cases

| Choice | Benefit | Cost or limit to explain |
| --- | --- | --- |
| Modular backend plus separate worker | Shared authority services and durable lineage; bounded deployment surface. | Large domain/API surface and synchronous work need performance/ownership review. |
| Deterministic risk/eligibility | Repeatable decisions and inspectable blockers. | Correct implementation and appropriate policy still need tests and operational review. |
| Strict immutable market receipts | Preserves what was known at the time; rejects conflicting reuse. | Provider corrections can make a scan unavailable. Version an actual acquisition policy; do not rewrite history. |
| PostgreSQL plus Qdrant | Relational truth plus semantic retrieval. | External upserts and SQL commits are not atomic across both stores. |
| Local mock providers | Reproducible development without keys. | Mock tests do not establish hosted provider health or semantic quality. Hosted policy prohibits silent mock fallback. |
| Explicit proposals/authority | Makes mutation and approval visible. | More lifecycle states; “approved” must name the authority/version rather than a generic badge. |
| Internal paper and demo venue | Exercises lifecycle safely within product scope. | Simulation differs from venue fills; actual demo/protection acceptance and profitability are separate. |

## Evidence to cite responsibly

Repository code and manifests establish implemented paths and declarations. Prior handoffs establish recorded development results on their exact bases. Current CI on an exact SHA establishes only jobs actually run; ordinary PR backend CI is focused, while complete backend release acceptance is explicit dispatch.

The October 7 package records supplied API/worker verification on `b58beda`, migration `a6manualdemo001`, received Watcher notification, historical Nested/internal-paper/Journal record and successful BloFin demo account sync. GitHub Vercel status is successful; full CI #783 is in progress at check. Native demo order acceptance, fresh SFP recovery and Journal target repair remain pending. No complete MVP, device/voice or profitability claim follows.

[Testing/evaluation](evaluation.md) · [monitoring](observability.md) · [status record](current_status.md).

## Glossary

| Term | Plain-language meaning |
| --- | --- |
| Paper trading | Simulated trading with no real-money order authority. |
| Perpetual instrument | A crypto derivatives contract without a fixed expiry; its venue/contract identity matters. |
| Canonical | The designated application authority/contract for a fact or lifecycle, rather than a compatibility view. |
| Provenance | Where a value came from, under which source/policy and at what time. |
| Freshness / finality | Whether data is recent enough / whether a candle is admissibly complete; different checks. |
| Nested Continuation | An implemented structural strategy family with versioned rules and supported timeframe contracts. |
| SFP | Swing Failure Pattern: a separate structural research/detection family with its own evidence constraints. |
| Candidate | A persisted confirmed setup awaiting the next governed decision; not an order. |
| TradePlanRevision | An immutable version of the exact plan used for approval/authorization. |
| Hash binding | Checking that the approved content is the same content later acted on. |
| Deterministic | Given the same facts and policy, code produces the same result. |
| RAG | Retrieval-augmented generation: bringing sourced reference passages into a model's context. |
| Tenant / RBAC | An organization's scope / role-based access control within that scope. |
| Lease / fencing | Temporary worker ownership / a token that rejects actions from an obsolete owner. |
| Idempotency | Repeating the same operation identity converges on its original result rather than duplicating it. |
| Learning attribution | Linking review/outcome facts back to strategy, setup and execution lineage. |
| Promotion | Explicitly selecting a validated strategy version for paper use; not live-trading permission. |
| Fail closed | Missing or invalid authority/evidence prevents the affected action rather than fabricating success. |

For short answers, see [technical Q&A](technical_qa.md). For planned capability and acceptance boundaries, see [limitations and roadmap](limitations_roadmap.md).
