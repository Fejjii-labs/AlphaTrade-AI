"""M2 / I3: the stop's realized figure deliberately spans paper and manual demo.

Pins the documented design (docs/risk_management.md, "Paper and BloFin demo share
one equity reading"). Splitting it per venue must be a deliberate, reviewed change.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from app.services.risk.daily_risk_accounting import DailyRiskAccounting


class _CapturingSession:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def scalar(self, statement: object) -> Decimal:
        self.statements.append(str(statement.compile(compile_kwargs={"literal_binds": True})))  # type: ignore[attr-defined]
        return Decimal("0")

    def execute(self, statement: object):  # pragma: no cover - not reached
        raise AssertionError("unexpected execute")


def test_canonical_realized_scope_includes_paper_and_manual_demo_sources() -> None:
    session = _CapturingSession()
    accounting = DailyRiskAccounting(session, risk_settings=None)  # type: ignore[arg-type]
    accounting._canonical_portfolio_totals(
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        start_utc=datetime(2026, 11, 7, tzinfo=UTC),
        end_utc=datetime(2026, 11, 8, tzinfo=UTC),
    )
    realized_sql = session.statements[0].upper()
    assert "PAPER_EXECUTION" in realized_sql
    assert "MANUAL_DEMO_TEST" in realized_sql
    # Position-linked mirrors are excluded so nothing counts twice.
    assert "LINKED_POSITION_ID IS NULL" in realized_sql
