"""Legacy (pre-73765026) market reservation basis for manual demo release.

Numbers are the staging 8 Oct manual demo entry (command 80958141): plan
f49a9fac, reservation c00e37ce, one native fill 0.1 contracts @ 82234.4.
"""

import re
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.errors import TradingPolicyError
from app.db.models import AccountRiskAccountingState, RiskReservation
from app.db.models import TradePlanRevision as PlanRow
from app.schemas.execution_protocol import RiskReservationReleaseReason
from app.services import manual_demo_recovery as recovery
from app.services.demo_account_history import has_demo_entry_history
from app.services.execution_fills import adjust_symbol_map
from tests.support.postgres_persistence import requires_postgres
from tests.test_manual_blofin_demo import world as world
from tests.test_manual_demo_lifecycle_workflow import set_exit
from tests.test_manual_demo_reconciliation import _service, _submit
from tests.test_manual_demo_reconciliation import native as native


def _money(v):
    return SimpleNamespace(value=Decimal(v), unit="USDT")


def _plan(*, limit_price=None):
    return SimpleNamespace(
        quantity=SimpleNamespace(value=Decimal("0.1"), unit="CONTRACTS"),
        instrument_rules=SimpleNamespace(
            contract_multiplier=Decimal("0.001"),
            contract_type=SimpleNamespace(value="LINEAR"),
        ),
        limit_price=limit_price,
        basis_policy=SimpleNamespace(execution_price=_money("82288.3")),
        entry_zone=SimpleNamespace(upper=Decimal("82370.5883")),
        risk_and_exits=SimpleNamespace(
            fee_allowance=_money("0.00823705883"),
            slippage_allowance=_money("0.00823705883"),
            maximum_loss=_money("0.05353294766"),
        ),
        execution_instrument="BTC-USDT",
    )


@pytest.fixture
def _intent(monkeypatch):
    def current(plan):
        price = max(plan.basis_policy.execution_price.value, plan.entry_zone.upper)
        if plan.limit_price is not None:
            price = plan.limit_price.value
        notional = (
            plan.quantity.value * plan.instrument_rules.contract_multiplier * price
            + plan.risk_and_exits.fee_allowance.value
            + plan.risk_and_exits.slippage_allowance.value
        )
        return SimpleNamespace(pending_notional=notional)

    monkeypatch.setattr(recovery, "conservative_reservation", current)


@pytest.mark.usefixtures("_intent")
def test_staging_legacy_reservation_resolves_to_execution_price_basis():
    reservation = SimpleNamespace(pending_order_exposure=Decimal("8.24530412"))
    basis = recovery.claimed_reservation_notional(_plan(), reservation)
    assert basis == Decimal("8.24530411766")
    exact_unused = basis - Decimal("0.1") * Decimal("0.001") * Decimal("82234.4")
    # Stored remainder after fill conversion on staging.
    assert abs(exact_unused - Decimal("0.02186412")) <= Decimal("0.00000001")


@pytest.mark.usefixtures("_intent")
def test_current_formula_reservation_still_matches():
    reservation = SimpleNamespace(pending_order_exposure=Decimal("8.25353295"))
    assert recovery.claimed_reservation_notional(_plan(), reservation) == Decimal("8.25353294766")


@pytest.mark.parametrize("stored", ["8.24530413", "8.25353294", "8.30000000", "0"])
@pytest.mark.usefixtures("_intent")
def test_unexplained_claim_is_refused(stored):
    reservation = SimpleNamespace(pending_order_exposure=Decimal(stored))
    with pytest.raises(TradingPolicyError, match="differs from its immutable plan"):
        recovery.claimed_reservation_notional(_plan(), reservation)


@pytest.mark.usefixtures("_intent")
def test_limit_plans_have_no_legacy_basis():
    plan = _plan(limit_price=_money("82300"))
    reservation = SimpleNamespace(pending_order_exposure=Decimal("8.24530412"))
    with pytest.raises(TradingPolicyError):
        recovery.claimed_reservation_notional(plan, reservation)


# --- Postgres: the legacy claim releases once through the real resolve path ---


def _legacy_rewrite(world, command_id, *, extra=Decimal("0")):
    """Rewrite this claim as a pre-73765026 claim (execution_price basis)."""
    with world[0]() as session:
        reservation = session.scalars(
            select(RiskReservation).where(RiskReservation.command_id == command_id)
        ).one()
        payload = session.get(PlanRow, reservation.plan_revision_id).semantic_payload
        qty = Decimal(payload["quantity"]["value"])
        mult = Decimal(payload["instrument_rules"]["contract_multiplier"])
        upper = Decimal(payload["entry_zone"]["upper"])
        exec_price = Decimal(payload["basis_policy"]["execution_price"]["value"])
        delta = qty * mult * (max(upper, exec_price) - exec_price)
        assert delta > 0, "fixture must have entry_zone.upper above execution_price"
        shift = delta.quantize(Decimal("0.00000001")) - extra
        reservation.pending_order_exposure -= shift
        reservation.remaining_reserved_notional -= shift
        accounting = session.scalars(select(AccountRiskAccountingState)).one()
        accounting.reserved_notional -= delta - extra
        accounting.symbol_reserved = adjust_symbol_map(
            accounting.symbol_reserved, reservation.instrument, -(delta - extra)
        )
        session.commit()
        return reservation.account_id


@requires_postgres
def test_legacy_market_claim_resolves_after_verified_exit(native):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    account_id = _legacy_rewrite(world, first.command_id)
    with world[0]() as session:
        resolved = _service(world, session).resolve(world[1], first.command_id)
        assert resolved.recovery_status == "resolved"
        reservation = session.scalars(select(RiskReservation)).one()
        assert reservation.release_state.value == "RELEASED"
        assert reservation.remaining_reserved_notional == 0
        assert not has_demo_entry_history(
            session, organization_id=world[1].organization_id, account_id=account_id
        )


@requires_postgres
def test_claim_matching_neither_formula_is_refused(native):
    world, _ = native
    _, first = _submit(native)
    set_exit(native, first)
    _legacy_rewrite(world, first.command_id, extra=Decimal("0.00000002"))
    with world[0]() as session, pytest.raises(TradingPolicyError):
        _service(world, session).resolve(world[1], first.command_id)
    with world[0]() as session:
        reservation = session.scalars(select(RiskReservation)).one()
        assert reservation.release_state.value != "RELEASED"


@requires_postgres
@pytest.mark.parametrize(
    ("extra", "released"), [(Decimal("0"), True), (Decimal("0.00000002"), False)]
)
def test_dispatcher_cancel_release_uses_claimed_basis(native, extra, released):
    """C1: the cancel/expiry release path shares the resolve path's claim basis."""
    world, _ = native
    _, first = _submit(native)
    _legacy_rewrite(world, first.command_id, extra=extra)
    with world[0]() as session:
        dispatcher = _service(world, session)._dispatcher()
        release = lambda: dispatcher._release_unused_reservation(  # noqa: E731
            command_id=first.command_id,
            reason=RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_CANCELLATION,
            now=world[4]._clock(),
        )
        if released:
            release()
            session.commit()
        else:
            with pytest.raises(TradingPolicyError):
                release()
            session.rollback()
    with world[0]() as session:
        reservation = session.scalars(select(RiskReservation)).one()
        assert (reservation.release_state.value == "RELEASED") is released
        if not released:
            assert reservation.remaining_reserved_notional > 0


def test_pending_order_exposure_is_written_only_at_claim():
    """C2: the stored claim is the write-once basis for remainder checks."""
    root = Path(recovery.__file__).resolve().parents[1]
    writers = sorted(
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if "migrations" not in path.parts
        and re.search(r"pending_order_exposure\s*(=|\+=|-=)(?!=)", path.read_text())
    )
    assert writers == ["services/execution_claim.py"], writers
    claim = (root / "services/execution_claim.py").read_text()
    assert len(re.findall(r"pending_order_exposure\s*=(?!=)", claim)) == 2
