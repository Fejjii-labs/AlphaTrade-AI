# Governed BloFin demo SFP planning

`GOVERNED_DEMO_SFP_ENABLED` defaults to `false`. Only `true` (case insensitive,
with surrounding whitespace allowed) opts in; empty and `false` disable it.
Other values reject Settings construction. This capability does not arm a worker.
Changing staging configuration requires separate operator authorization.

With the flag off, SFP retains `sfp_execution_plan_not_authorized` before planning,
including reuse of an existing plan. Other strategy families are unchanged.

With the flag on, the demo loop reads the confirmed structural stop/target using
`SFP_MIN_STOP_ATR_MULTIPLE` (default 0.5), preserves the evidence confirmation
entry without a haircut, and uses the executable demo book for the execution
entry. Existing tick and lot rules and minimum notional 5 apply. Rounded prices
must preserve valid geometry and at least gross 1R; fees and slippage stay in
sizing and the reserved maximum loss. A refused new SFP plan is audited with its
reason code and is never dispatched.

Unprofiled SFP sizing caps stop-distance risk at the paper default 1% of equity
or the explicit owner risk percentage. The existing demo loss band, fee/slippage
reserve, available balance, leverage and exposure limits also apply; an
incompatible combination refuses rather than raising a limit. Active demo risk
profiles keep their existing sizing and explicit owner-risk behavior.

All existing staging arms, principal/account pins, approved policy/eligibility,
account and bot caps, ledger verification, kill switches, worker pause, expiry,
durable intent and dispatch fencing, demo host restrictions and reconciliation
remain required. `EXECUTION_MODE=paper` and `ENABLE_REAL_TRADING=false` remain
mandatory. No live execution capability is added.

The November 16 staging duplicate cause remains unproven. Repeated identical
receipts already preserve their original payload and clocks, while conflicting
payloads and revisions refuse. Undiagnosed evidence warnings now add a sanitized
symbol, timeframe and allowlisted exception class; they contain no exception
message or provider payload. These diagnostics are needed before selecting any
further duplicate fix. Serial SQLite regressions do not establish PostgreSQL
concurrent locking behavior; the PostgreSQL merge gate remains required.
