# Security and trading boundaries

This guide describes mechanisms in main `b58beda`, inspected October 7, 2026. It is not a penetration test, certification or claim of comprehensive protection. [Runtime evidence and unknowns](current_status.md) are separate from implementation. The [November 2026 hardening](#hardening-november-2026) section below was checked against main `485c54d0` on November 8, 2026.

## Authentication and browser sessions

The API validates bearer access JWTs and resolves the current persisted user/membership into `TenantContext`. Passwords are bcrypt-hashed; refresh tokens are stored hashed, rotate on refresh and support reuse detection/revocation. Account services implement verification, password reset and owner-managed invitations.

| Context | Refresh storage | Access storage |
| --- | --- | --- |
| Default local bearer flow | Returned in the auth flow for client storage. | Short-lived JWT in browser session storage; sent as `Authorization: Bearer`. |
| Configured cookie flow | httpOnly `alphatrade_refresh` cookie; current default omits refresh from the response body. | Access JWT remains client-readable/session-stored; cookie mode does not eliminate XSS risk. |

Access JWTs are pinned to HS256 (`JWT_ALGORITHM` must be `HS256`). Outside `local`, `JWT_SECRET` must be at least 32 bytes and must not be a known weak value or a template placeholder (anything containing `REPLACE`, `placeholder` or `change-me`); placeholders are refused even for disarmed workers. The code default signing key is local-only: the API and armed workers refuse it at settings load, and token issue/verify refuses it outside `local`. `DEBUG=true` is refused in staging and production. Default access/refresh lifetimes are 15 minutes/seven days. Hosted cookie settings require Secure cookies and correct SameSite/CORS origins. Logout revokes refresh and uses the access-token denylist; current hosted policy requires shared Redis and fail-closed revocation/rate-limit behavior.

[Frontend middleware](../frontend/src/middleware.ts) uses a session marker for navigation, not authorization. A forged marker does not grant API access. [Security headers](../frontend/src/lib/security-headers.ts) include CSP and framing restrictions; the API origin and browser speech permissions must match the intended deployment. See [auth implementation](../backend/src/app/services/auth_service.py), [auth dependency](../backend/src/app/core/auth.py), [settings](../backend/src/app/core/config.py) and [account lifecycle](account_management.md).

## Tenant isolation and roles

Organization/user identity comes from authenticated context, not a client's proposed account IDs. Services and repositories scope domain records; ownership is checked again when exposing knowledge chunks and Agent evidence. This is an application enforcement model, not a claim of database row-level security or a completed audit of every route.

| Role | Intended API boundary |
| --- | --- |
| OWNER | Organization administration plus trader operations. |
| TRADER | Authorized domain mutations supported by the endpoint. |
| VIEWER | Reader endpoints; not trader mutation authority. |

Within an organization, some records are per-user rather than org-shared. Decision (audit M15, November 2026): paper positions (`/positions/{id}` get/update/close), legacy approvals (`/approvals/{id}` decide routes and `/workflow`) and backtest cancel are scoped to the record's own user, matching the already user-scoped list/get endpoints; another member (including an OWNER) gets 404. Backtest run results stay readable org-wide. Plan-bound approvals already required the exact principal.

[RBAC dependencies](../backend/src/app/security/rbac.py) enforce endpoint-specific role requirements. The current Agent turn routes use trader-level membership, so not every read-like Agent request is available to VIEWER. User-owned knowledge and conversations also retain user scope within an organization.

## Hardening (November 2026)

These changes merged to main on November 8, 2026 (#443 to #448). Each item names the code that enforces it.

| Area | Behavior | Source |
| --- | --- | --- |
| Login throttle (#444) | Failed logins are counted per submitted email (sha256-hashed key, shared Redis limiter) across all client IPs. After `AUTH_LOGIN_EMAIL_FAILURE_LIMIT` failures (default 10) in `AUTH_LOGIN_EMAIL_WINDOW_SECONDS` (default 900), that email gets 429 before the password is checked. Known and unknown emails are treated the same, and successful logins are not counted. Staging and production refuse a limit above 10. The existing 20/h per-IP login limit still applies. | [rate limit](../backend/src/app/security/rate_limit.py), [auth route](../backend/src/app/api/routes/auth.py) |
| Log redaction (#445) | The `uvicorn.access` and `uvicorn.error` loggers have their own filter, because they do not propagate to the root redaction filter. Every query-string value is replaced with `***REDACTED***` (keys and path are kept), and the standard secret patterns run on the line. | [logging](../backend/src/app/core/logging.py) |
| Telegram enrollment (#446) | `POST /telegram-paper/enrollment/start` needs OWNER or TRADER, so a VIEWER gets 403 and no challenge. It uses the shared app limiter: 10 per user and 30 per IP per 10 minutes. | [enrollment route](../backend/src/app/api/routes/telegram_enrollment.py) |
| Org filters fail closed (#447) | The audit, approvals, documents, journal, positions, usage, orders and proposals repositories take `organization_id` as a required keyword on their org-scoped list and get methods. A `None` raises `ValueError` (`require_organization_id`) before any query runs. Before this change, a `None` dropped the tenant filter. | [repository base](../backend/src/app/repositories/base.py) |
| Existence oracle (#448) | `ensure_same_organization` raises `NotFoundError` with the route's own not-found message. Another organization's proposal, order, position or approval therefore looks exactly like a missing id (status, code and text). Cross-org strategy version compile and approve return the same 404 as a missing version. | [tenant guard](../backend/src/app/security/tenant.py), [compiled setup service](../backend/src/app/services/compiled_setup_service.py) |
| Correlation ids (#448) | `X-Request-ID` and `X-Trace-ID` are kept only if they match `[A-Za-z0-9._:-]{1,64}`. Any other value (too long, CR/LF, spaces, markup) is replaced with a fresh UUID4, which closes header and log injection. | [middleware](../backend/src/app/core/middleware.py) |
| Smoke credentials (#443) | Smoke scripts read `SMOKE_PASSWORD` from the environment and exit if it is unset; there is no literal fallback. Playwright generates a fresh password for each account it registers. | [`scripts/*-smoke.sh`](../scripts/), [e2e password helper](../frontend/e2e/helpers/e2e-password.ts) |

Known gaps from the Griller review of #447 and #448, none reachable over HTTP today:

- `documents.get_by_source_uri` and `get_by_hash` still treat a `None` organization as "global documents only". Knowledge routes always pass the tenant's organization.
- `chunks.list_chunks` and `PerformanceSnapshotRepository.latest` still filter by organization only when one is passed. Their HTTP callers always pass one.
- Two internal errors, `CrossTenantLevelError` and `CrossTenantAttributionError`, still map to 403. No HTTP route reaches either one.

Follow-up work on these items is tracked in its own pull request.

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
