# Security and trading boundaries

This guide describes mechanisms in main `b58beda`, inspected October 7, 2026. It is not a penetration test, certification or claim of comprehensive protection. [Runtime evidence and unknowns](current_status.md) are separate from implementation.

## Authentication and browser sessions

The API validates bearer access JWTs and resolves the current persisted user/membership into `TenantContext`. Passwords are bcrypt-hashed; refresh tokens are stored hashed, rotate on refresh and support reuse detection/revocation. Account services implement verification, password reset and owner-managed invitations.

| Context | Refresh storage | Access storage |
| --- | --- | --- |
| Default local bearer flow | Returned in the auth flow for client storage. | Short-lived JWT in browser session storage; sent as `Authorization: Bearer`. |
| Configured cookie flow | httpOnly `alphatrade_refresh` cookie; current default omits refresh from the response body. | Access JWT remains client-readable/session-stored; cookie mode does not eliminate XSS risk. |

Default access/refresh lifetimes are 15 minutes/seven days. Hosted cookie settings require Secure cookies and correct SameSite/CORS origins. Logout revokes refresh and uses the access-token denylist; current hosted policy requires shared Redis and fail-closed revocation/rate-limit behavior.

[Frontend middleware](../frontend/src/middleware.ts) uses a session marker for navigation, not authorization. A forged marker does not grant API access. [Security headers](../frontend/src/lib/security-headers.ts) include CSP and framing restrictions; the API origin and browser speech permissions must match the intended deployment. See [auth implementation](../backend/src/app/services/auth_service.py), [auth dependency](../backend/src/app/core/auth.py), [settings](../backend/src/app/core/config.py) and [account lifecycle](account_management.md).

## Tenant isolation and roles

Organization/user identity comes from authenticated context, not a client's proposed account IDs. Services and repositories scope domain records; ownership is checked again when exposing knowledge chunks and Agent evidence. This is an application enforcement model, not a claim of database row-level security or a completed audit of every route.

| Role | Intended API boundary |
| --- | --- |
| OWNER | Organization administration plus trader operations. |
| TRADER | Authorized domain mutations supported by the endpoint. |
| VIEWER | Reader endpoints; not trader mutation authority. |

[RBAC dependencies](../backend/src/app/security/rbac.py) enforce endpoint-specific role requirements. The current Agent turn routes use trader-level membership, so not every read-like Agent request is available to VIEWER. User-owned knowledge and conversations also retain user scope within an organization.

## Secrets, providers and network boundaries

Secrets are server environment/Settings values. Use environment templates for **names and defaults**, never credentials. Frontend `NEXT_PUBLIC_*` values are public build configuration and must not contain keys, passwords, tokens or connection strings. Logs/audit use redaction and status surfaces expose booleans/health rather than secrets; redaction is not permission to copy raw sensitive records into evidence or screenshots.

Current staging/production provider policy requires configured OpenAI, authoritative Qdrant and Redis security backends. Local mocks and process-memory fallbacks are development options, not hosted-success behavior. Canonical market evidence uses read-only public perpetual connectors; exchange credentials must not enter those connectors. Demo exchange credentials are separately scoped and do not permit production venue hosts, withdrawal or transfer operations.

The BloFin demo credentials are one shared venue account. The `/exchange/*` routes that read or act on that account (balances, positions, position mode, leverage info, order status, order cancel) are restricted to owners of `GOVERNED_BLOFIN_DEMO_ORGANIZATION_ID`; when that pin is unset they fail closed outside `local` ([demo access guard](../backend/src/app/core/exchange_demo_access.py)). Instrument metadata stays available to any owner.

Sources: [provider policy](../backend/src/app/core/provider_policy.py), [deployment safety](../backend/src/app/core/deployment_safety.py), [exchange safety](../backend/src/app/core/exchange_safety.py), [market activation](../backend/src/app/market_activation/profile.py).

## Uploads and untrusted knowledge

Knowledge import supports TXT, Markdown, DOCX and selectable-text PDF. A preview saves nothing; explicit save checks a signed ten-minute receipt bound to the authenticated principal, exact bytes/text, filename, category and parser version. The request/file/parser have time and resource bounds: a 5 MiB file limit, 100,000-character text limit and isolated parsing. Raw source binaries are not persisted by Knowledge import. There is no OCR, arbitrary URL fetch or automatic interpretation of chart images in this path.

Journal attachments are a distinct, scoped binary-storage feature with MIME/size/quota checks; attaching a file does not analyze it or create trading authority. See [file import contract](knowledge_file_import.md), [bounded upload route](../backend/src/app/api/knowledge_file_route.py) and [Journal routes](../backend/src/app/api/routes/journal.py).

A stored document may contain errors or hostile instructions. Agent context labels it as reference data and retains source provenance; it cannot authorize strategy/risk/execution changes. Scoped retrieval, typed actions and separate confirmation constrain authority independently of conversational text. The compatibility LangGraph path also has input/output guardrails; do not infer that every route runs the same guardrail pipeline. These measures do not prove prompt injection is solved. [Guardrails](../backend/src/app/guardrails/) and [Agent authority](agent_workflow.md) provide the relevant implementation boundaries.

Browser dictation may use the browser vendor's speech service. Its transcript is reviewed before sending through the ordinary Agent flow. Server audio/image analysis contracts remain unimplemented; [voice limits](voice_agent_v1_handoff.md) are explicit.

## Evidence, approvals and deterministic risk

Canonical evidence binds instrument/source/policy identity, content hashes and receipt clocks. Reuse checks reject conflicting same-policy observations. PR209's acquisition policy v2 appends alongside immutable v1 history; it does not rewrite receipts or fabricate provider revisions. Quote freshness, trade coverage, candle finality and setup lifetime are separate gates.

Only the Candidate lifecycle authority can publish an eligible confirmed Candidate. A research label, model explanation or Telegram alert cannot do so. Deterministic eligibility/sizing/risk runs before canonical execution; risk `BLOCK`, kill switch, stale evidence or missing permissions cannot be overridden by model prose or a UI click.

Supported user execution binds approval to an immutable plan revision/hash. An explicitly armed worker may continue an approved strategy without asking for a new conversational confirmation at every setup. That operator authority is scoped; it is not general permission for the Agent or Telegram to trade. Idempotent claims, worker fences and durable execution receipts support restart/duplicate handling.

[Architecture flow](architecture.md) · [Agent paper command](agent_paper_execution_v4.md) · [governed demo dispatch](governed_blofin_demo_execution.md) · [SFP immutable history](sfp_candle_finalization_recovery.md).

The [manual BloFin demo capability](manual_blofin_demo_acceptance.md) adds staging-only owner scope and exact preview/confirmation. It does not fabricate strategy/Candidate authority or grant Agent trading permissions. New entry authority also enforces the deterministic [gross allocation-weighted 1R floor](../backend/src/app/services/planned_reward_risk.py); historical plans remain readable.

## Audit and real-money restrictions

Typed audit events, request IDs, strategy lifecycle events, evidence receipts, plan authorizations and fill/Journal lineage support review. Not every log is a durable authority record, and usage estimates are not billing-grade evidence. See [monitoring](observability.md).

The inspected permanent-paper policy refuses live/trade mode and `ENABLE_REAL_TRADING=true` in every environment. Real execution cannot be enabled by an ordinary configuration change or Agent request. Internal simulation and an explicitly governed BloFin **demo** option are separate. The demo kill switch stops new risk; it does not automatically close or repair existing external demo exposure.

No deployment, activation, shared migration, exchange order or security scan was performed for this documentation task. Outstanding operational acceptance, hardware and statistical limits are in [current status](current_status.md) and [limitations](limitations_roadmap.md).
