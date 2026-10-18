"""Bind an active paper or demo profile onto the claim ledger.

The cash-balance fallback cap is the unverified epoch default. A profile-sized
plan uses the profile notional and daily-loss amounts instead, so a grade that
cleared ``decide_trade`` is not refused by a tighter cash ceiling. This does
not call an exchange.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import AccountRiskAccountingState
from app.services.risk_policy.errors import RiskPolicyRejected
from app.services.risk_policy.ledger import caps_for_ledger

PROFILE_MARGIN_ASSUMPTIONS = frozenset(
    {
        "paper-profile-isolated",
        "verified-demo-read-leverage",
    }
)


def bind_profile_claim_caps(
    session: Session,
    accounting: AccountRiskAccountingState,
    plan: object,
    *,
    organization_id: UUID,
    user_id: UUID,
    now: datetime,
) -> str | None:
    """Set claim ceilings from the active profile. None when this plan is not profile-sized."""

    exits = getattr(plan, "risk_and_exits", None)
    assumption = getattr(exits, "margin_assumption_id", None)
    if assumption not in PROFILE_MARGIN_ASSUMPTIONS:
        return None
    from app.services.audit_service import AuditService
    from app.services.risk.daily_risk_accounting import DailyRiskAccounting
    from app.services.risk.settings_service import RiskSettingsService
    from app.services.risk_policy.persistence import RiskPolicyService
    from app.services.risk_policy.profile import PolicyKind

    venue = getattr(plan, "execution_venue", "")
    kind = PolicyKind.DEMO if venue == "BLOFIN_DEMO" else PolicyKind.INTERNAL_PAPER
    profile, row, _scheduled = RiskPolicyService(session, AuditService(session)).read(
        organization_id=organization_id,
        kind=kind,
        now=now,
    )
    if row is None:
        return "risk_profile_unavailable"
    snapshot = DailyRiskAccounting(
        session,
        RiskSettingsService(session, AuditService(session)),
        clock=lambda: now,
    ).sync_from_portfolio(organization_id=organization_id, user_id=user_id)
    try:
        caps = caps_for_ledger(profile, snapshot.account_equity)
    except RiskPolicyRejected as exc:
        return exc.code
    accounting.max_notional = caps.max_notional
    accounting.max_symbol_notional = caps.max_symbol_notional
    accounting.max_daily_loss = caps.max_daily_loss
    session.flush()
    return None
