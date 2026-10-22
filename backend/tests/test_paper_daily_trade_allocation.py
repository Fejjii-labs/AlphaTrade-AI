"""Internal paper trade slots reset at the user's local trading day.

Completed trades from an earlier day stop blocking a claim. Reservations that
are still open keep their slots. Demo claims do not start the roll.
"""

from __future__ import annotations

import os
import threading
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.db.models import AccountRiskAccountingState, RiskReservation, UserRiskSettings
from app.schemas.execution_protocol import (
    ExecutionCommandOutcome,
    RiskReservationReleaseReason,
    RiskReservationReleaseState,
)
from app.services.audit_service import AuditService
from app.services.execution_claim import ReservationIntent, evaluate_claim_predicate
from app.services.execution_fills import convert_reservation_for_fill
from app.services.risk.settings_service import RiskSettingsService
from app.services.safety_epoch import SafetyEpochService
from tests.support.phase1_plan_fixtures import (
    approve_plan,
    execute_request,
    execution_service,
    paper_settings,
    persist_plan,
    plan_request,
    seed_support,
    sqlite_session,
)
from tests.support.postgres_template import refresh_database

DAY = datetime(2026, 10, 22, 15, 0, tzinfo=UTC)
NEXT_DAY = DAY + timedelta(days=1)
BERLIN_BOUNDARY = datetime(2026, 10, 22, 22, 30, tzinfo=UTC)


@pytest.fixture
def session() -> Iterator[Session]:
    yield from sqlite_session()


def test_claim_predicate_still_reports_insufficient_daily_trade_allocation() -> None:
    accounting = _predicate_accounting(max_slots=1, reserved=0, actual=1)
    assert (
        evaluate_claim_predicate(
            blocking_epoch=False,
            kill_active=False,
            accounting=accounting,  # type: ignore[arg-type]
            intent=_slot_intent(),
        )
        == "insufficient_daily_trade_allocation"
    )


def test_yesterday_completed_slots_do_not_block_today(session: Session) -> None:
    ids, plan, authorization = _prepare(session, venue="PAPER_INTERNAL", when=DAY)
    _ledger(
        session,
        ids,
        plan,
        business_day=DAY.date() - timedelta(days=1),
        max_slots=1,
        configured_max=1,
        actual=5,
        reserved=0,
        timezone_name="UTC",
    )
    session.commit()

    result = _claim(session, ids, plan, authorization, when=DAY, key="yesterday-free")

    assert result.outcome is ExecutionCommandOutcome.ALLOW
    row = _ledger_row(session)
    assert row.actual_trade_count == 0
    assert row.reserved_trade_slots == 1
    assert row.max_trade_slots == 1
    assert _stored_day(row) == DAY.date()
    assert row.actual_daily_loss == Decimal("12.50")


def test_same_day_exhaustion_still_blocks(session: Session) -> None:
    ids, plan, authorization = _prepare(session, venue="PAPER_INTERNAL", when=DAY)
    _ledger(
        session,
        ids,
        plan,
        business_day=DAY.date(),
        max_slots=1,
        configured_max=4,
        actual=1,
        reserved=0,
        timezone_name="UTC",
    )
    session.commit()

    result = _claim(session, ids, plan, authorization, when=DAY, key="same-day-full")

    assert result.outcome is ExecutionCommandOutcome.BLOCKED
    assert result.blocked_reason_code == "insufficient_daily_trade_allocation"
    row = _ledger_row(session)
    assert row.actual_trade_count == 1
    assert row.reserved_trade_slots == 0
    assert row.max_trade_slots == 1
    assert _stored_day(row) == DAY.date()


def test_open_reservation_survives_the_day_roll(session: Session) -> None:
    ids, first_plan, first_authorization = _prepare(session, venue="PAPER_INTERNAL", when=DAY)
    _ledger(
        session,
        ids,
        first_plan,
        business_day=DAY.date(),
        max_slots=1,
        configured_max=1,
        actual=0,
        reserved=0,
        timezone_name="UTC",
    )
    session.commit()
    opened = _claim(session, ids, first_plan, first_authorization, when=DAY, key="open-day-1")
    assert opened.outcome is ExecutionCommandOutcome.ALLOW
    open_reservation = session.scalar(select(RiskReservation))
    assert open_reservation is not None
    assert open_reservation.release_state is RiskReservationReleaseState.CHARGED

    second_plan = persist_plan(
        session,
        ids,
        plan_request(
            ids,
            execution_venue="PAPER_INTERNAL",
            quantity={"value": "3", "unit": "CONTRACTS"},
            **_window(NEXT_DAY),
        ),
    )
    second_authorization = approve_plan(
        session, ids, second_plan, clock=NEXT_DAY - timedelta(minutes=1)
    )
    session.commit()
    blocked = _claim(
        session, ids, second_plan, second_authorization, when=NEXT_DAY, key="open-day-2"
    )

    assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
    assert blocked.blocked_reason_code == "insufficient_daily_trade_allocation"
    row = _ledger_row(session)
    assert row.reserved_trade_slots == 1
    assert row.actual_trade_count == 0
    assert _stored_day(row) == NEXT_DAY.date()
    assert session.get(RiskReservation, open_reservation.id) is not None
    survived = session.get(RiskReservation, open_reservation.id)
    assert survived is not None
    assert survived.release_state is RiskReservationReleaseState.CHARGED
    assert survived.daily_trade_allocation == 1


def test_new_max_trades_per_day_applies_on_the_next_day(session: Session) -> None:
    ids, blocked_plan, blocked_authorization = _prepare(session, venue="PAPER_INTERNAL", when=DAY)
    _ledger(
        session,
        ids,
        blocked_plan,
        business_day=DAY.date(),
        max_slots=1,
        configured_max=4,
        actual=1,
        reserved=0,
        timezone_name="UTC",
    )
    session.commit()
    blocked = _claim(session, ids, blocked_plan, blocked_authorization, when=DAY, key="cap-today")
    assert blocked.outcome is ExecutionCommandOutcome.BLOCKED
    assert blocked.blocked_reason_code == "insufficient_daily_trade_allocation"
    assert _ledger_row(session).max_trade_slots == 1

    next_plan = persist_plan(
        session,
        ids,
        plan_request(
            ids,
            execution_venue="PAPER_INTERNAL",
            quantity={"value": "4", "unit": "CONTRACTS"},
            **_window(NEXT_DAY),
        ),
    )
    next_authorization = approve_plan(
        session, ids, next_plan, clock=NEXT_DAY - timedelta(minutes=1)
    )
    session.commit()
    allowed = _claim(session, ids, next_plan, next_authorization, when=NEXT_DAY, key="cap-tomorrow")

    assert allowed.outcome is ExecutionCommandOutcome.ALLOW
    row = _ledger_row(session)
    assert row.max_trade_slots == 4
    assert row.actual_trade_count == 0
    assert row.reserved_trade_slots == 1
    assert _stored_day(row) == NEXT_DAY.date()


def test_day_boundary_uses_the_user_timezone(session: Session) -> None:
    berlin_day = BERLIN_BOUNDARY.astimezone(ZoneInfo("Europe/Berlin")).date()
    utc_day = BERLIN_BOUNDARY.astimezone(UTC).date()
    assert berlin_day == date(2026, 10, 23)
    assert utc_day == date(2026, 10, 22)

    ids, plan, authorization = _prepare(session, venue="PAPER_INTERNAL", when=BERLIN_BOUNDARY)
    _ledger(
        session,
        ids,
        plan,
        business_day=utc_day,
        max_slots=1,
        configured_max=1,
        actual=1,
        reserved=0,
        timezone_name="Europe/Berlin",
    )
    session.commit()
    result = _claim(session, ids, plan, authorization, when=BERLIN_BOUNDARY, key="berlin-midnight")
    assert result.outcome is ExecutionCommandOutcome.ALLOW
    assert _stored_day(_ledger_row(session)) == berlin_day


def test_utc_clock_does_not_roll_before_the_user_midnight(session: Session) -> None:
    ids, plan, authorization = _prepare(session, venue="PAPER_INTERNAL", when=BERLIN_BOUNDARY)
    _ledger(
        session,
        ids,
        plan,
        business_day=BERLIN_BOUNDARY.date(),
        max_slots=1,
        configured_max=1,
        actual=1,
        reserved=0,
        timezone_name="UTC",
    )
    session.commit()
    result = _claim(session, ids, plan, authorization, when=BERLIN_BOUNDARY, key="utc-same-day")
    assert result.outcome is ExecutionCommandOutcome.BLOCKED
    assert result.blocked_reason_code == "insufficient_daily_trade_allocation"
    row = _ledger_row(session)
    assert row.actual_trade_count == 1
    assert _stored_day(row) == BERLIN_BOUNDARY.date()
    assert row.max_trade_slots == 1


def test_demo_claim_does_not_roll_trade_slots(session: Session) -> None:
    settings = paper_settings(database_url="sqlite+pysqlite:///:memory:")
    assert settings.real_trading_enabled is False
    assert settings.enable_real_trading is False
    assert settings.execution_mode == "paper"
    assert settings.exchange_mode == "paper_internal"

    ids, plan, authorization = _prepare(session, venue="BLOFIN_DEMO", when=DAY)
    _ledger(
        session,
        ids,
        plan,
        business_day=DAY.date() - timedelta(days=1),
        max_slots=1,
        configured_max=9,
        actual=5,
        reserved=2,
        timezone_name="Europe/Berlin",
    )
    session.commit()
    result = _claim(session, ids, plan, authorization, when=DAY, key="demo-unchanged")

    assert result.outcome is ExecutionCommandOutcome.BLOCKED
    assert result.blocked_reason_code == "insufficient_daily_trade_allocation"
    row = _ledger_row(session)
    assert row.actual_trade_count == 5
    assert row.reserved_trade_slots == 2
    assert row.max_trade_slots == 1
    assert _stored_day(row) == DAY.date() - timedelta(days=1)
    assert row.actual_daily_loss == Decimal("12.50")
    assert row.version == 1


def test_roll_is_idempotent_and_keeps_reserved_slots_for_a_later_fill(session: Session) -> None:
    ids = seed_support(session)
    account = ids["account_one"]
    session.add(
        UserRiskSettings(
            organization_id=ids["organization"].id,
            user_id=ids["user"].id,
            max_trades_per_day=3,
            timezone="UTC",
            default_account_balance=Decimal("100000"),
            daily_loss_limit=Decimal("100000"),
        )
    )
    session.add(
        AccountRiskAccountingState(
            organization_id=ids["organization"].id,
            account_id=account.id,
            reserved_notional=Decimal("10"),
            reserved_daily_loss=Decimal("1"),
            reserved_trade_slots=1,
            actual_notional=Decimal("0"),
            actual_daily_loss=Decimal("12.50"),
            actual_trade_count=4,
            symbol_reserved={},
            symbol_actual={},
            max_notional=Decimal("100000"),
            max_daily_loss=Decimal("100000"),
            max_trade_slots=1,
            max_symbol_notional=Decimal("100000"),
            daily_locked=False,
            exposure_unit="USDT",
            trade_slot_business_date=DAY.date() - timedelta(days=1),
            version=1,
        )
    )
    session.commit()
    epochs = SafetyEpochService(
        session,
        paper_settings(database_url="sqlite+pysqlite:///:memory:"),
        RiskSettingsService(session, AuditService(session)),
    )
    rolled = epochs.lock_risk_accounting(
        organization_id=ids["organization"].id,
        account_id=account.id,
        user_id=ids["user"].id,
        exposure_unit="USDT",
        now=DAY,
        roll_daily_trade_slots=True,
    )
    assert rolled.actual_trade_count == 0
    assert rolled.reserved_trade_slots == 1
    assert rolled.max_trade_slots == 3
    assert rolled.version == 2
    assert rolled.actual_daily_loss == Decimal("12.50")
    again = epochs.lock_risk_accounting(
        organization_id=ids["organization"].id,
        account_id=account.id,
        user_id=ids["user"].id,
        exposure_unit="USDT",
        now=DAY,
        roll_daily_trade_slots=True,
    )
    assert again.version == 2
    assert again.reserved_trade_slots == 1
    assert again.actual_trade_count == 0

    reservation = RiskReservation(
        organization_id=ids["organization"].id,
        account_id=account.id,
        plan_revision_id=uuid.uuid4(),
        command_id=uuid.uuid4(),
        receipt_id=uuid.uuid4(),
        instrument="BTC-USDT",
        risk_policy_version="test",
        risk_snapshot_version="test",
        pending_order_exposure=Decimal("10"),
        submitting_or_ambiguous_exposure=Decimal("10"),
        open_order_notional=Decimal("10"),
        daily_trade_allocation=1,
        daily_loss_allocation=Decimal("1"),
        total_exposure=Decimal("10"),
        symbol_exposure=Decimal("10"),
        remaining_reserved_notional=Decimal("10"),
        converted_trade_slots=0,
        contract_multiplier=Decimal("1"),
        contract_type="LINEAR",
        quantity_unit="BASE",
        exposure_unit="USDT",
        safety_epoch=1,
        release_state=RiskReservationReleaseState.CHARGED,
    )
    convert_reservation_for_fill(
        reservation=reservation,
        accounting=again,
        fill_quantity=Decimal("1"),
        fill_price=Decimal("10"),
        now=DAY,
    )
    assert again.reserved_trade_slots == 0
    assert again.actual_trade_count == 1
    assert reservation.converted_trade_slots == 1
    assert reservation.release_reason is RiskReservationReleaseReason.FILL_CONVERSION


def test_two_claims_at_the_boundary_roll_once() -> None:
    url = _postgres_url()
    if url is None:
        pytest.skip("Set EXPERIMENT_TEST_POSTGRES_URL to disposable loopback PostgreSQL.")
    engine = refresh_database(url, kind="full")
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as setup:
        ids, first_plan, first_authorization = _prepare(setup, venue="PAPER_INTERNAL", when=DAY)
        _ledger(
            setup,
            ids,
            first_plan,
            business_day=DAY.date() - timedelta(days=1),
            max_slots=1,
            configured_max=1,
            actual=5,
            reserved=0,
            timezone_name="UTC",
        )
        second_plan = persist_plan(
            setup,
            ids,
            plan_request(
                ids,
                execution_venue="PAPER_INTERNAL",
                quantity={"value": "3", "unit": "CONTRACTS"},
                **_window(DAY),
            ),
        )
        second_authorization = approve_plan(
            setup, ids, second_plan, clock=DAY - timedelta(minutes=1)
        )
        setup.commit()
        first_request = execute_request(ids, first_plan, first_authorization, key="race-a")
        second_request = execute_request(ids, second_plan, second_authorization, key="race-b")

    barrier = threading.Barrier(2)
    results: list[object] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(request: object) -> None:
        with factory() as session:
            try:
                barrier.wait(timeout=20)
                result = execution_service(session, database_url=url).execute_paper_plan(
                    request,  # type: ignore[arg-type]
                    clock=lambda: DAY,
                )
                session.commit()
                with lock:
                    results.append(result)
            except BaseException as exc:
                session.rollback()
                with lock:
                    errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=(first_request,)),
        threading.Thread(target=worker, args=(second_request,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert len(results) == 2
    outcomes = sorted(result.outcome.value for result in results)  # type: ignore[attr-defined]
    assert outcomes == ["ALLOW", "BLOCKED"]
    blocked = next(
        result
        for result in results
        if result.outcome is ExecutionCommandOutcome.BLOCKED  # type: ignore[attr-defined]
    )
    assert blocked.blocked_reason_code == "insufficient_daily_trade_allocation"  # type: ignore[attr-defined]
    with factory() as check:
        row = check.scalar(select(AccountRiskAccountingState))
        assert row is not None
        assert row.actual_trade_count == 0
        assert row.reserved_trade_slots == 1
        assert row.max_trade_slots == 1
        assert _stored_day(row) == DAY.date()
        assert row.version == 3
    engine.dispose()


def _window(when: datetime) -> dict[str, object]:
    """Keep evidence and basis inside the plan's freshness window."""

    return {
        "valid_from": when - timedelta(seconds=10),
        "valid_until": when + timedelta(minutes=30),
        "evidence_observed_at": when - timedelta(seconds=30),
        "basis_policy": {
            "policy_id": "cross-venue-basis",
            "policy_version": "1",
            "evidence_price": {"value": "100.00", "unit": "USDT"},
            "execution_price": {"value": "100.10", "unit": "USDT"},
            "formula": "(execution_price-evidence_price)/evidence_price",
            "timestamp": when - timedelta(seconds=20),
            "tolerance_bps": "20",
            "freshness_seconds": 60,
        },
    }


def _prepare(
    session: Session,
    *,
    venue: str,
    when: datetime,
) -> tuple[dict[str, object], object, object]:
    ids = seed_support(session)
    plan = persist_plan(
        session,
        ids,
        plan_request(ids, execution_venue=venue, **_window(when)),
    )
    authorization = approve_plan(session, ids, plan, clock=when - timedelta(minutes=1))
    return ids, plan, authorization


def _ledger(
    session: Session,
    ids: dict[str, object],
    plan: object,
    *,
    business_day: date,
    max_slots: int,
    configured_max: int,
    actual: int,
    reserved: int,
    timezone_name: str,
) -> None:
    organization = ids["organization"]
    user = ids["user"]
    session.add(
        UserRiskSettings(
            organization_id=organization.id,  # type: ignore[attr-defined]
            user_id=user.id,  # type: ignore[attr-defined]
            max_trades_per_day=configured_max,
            timezone=timezone_name,
            default_account_balance=Decimal("100000"),
            daily_loss_limit=Decimal("100000"),
        )
    )
    session.add(
        AccountRiskAccountingState(
            organization_id=organization.id,  # type: ignore[attr-defined]
            account_id=plan.account_id,  # type: ignore[attr-defined]
            reserved_notional=Decimal("0"),
            reserved_daily_loss=Decimal("0"),
            reserved_trade_slots=reserved,
            actual_notional=Decimal("0"),
            actual_daily_loss=Decimal("12.50"),
            actual_trade_count=actual,
            symbol_reserved={},
            symbol_actual={},
            max_notional=Decimal("100000"),
            max_daily_loss=Decimal("100000"),
            max_trade_slots=max_slots,
            max_symbol_notional=Decimal("100000"),
            daily_locked=False,
            exposure_unit="USDT",
            trade_slot_business_date=business_day,
            version=1,
        )
    )


def _claim(
    session: Session,
    ids: dict[str, object],
    plan: object,
    authorization: object,
    *,
    when: datetime,
    key: str,
) -> object:
    result = execution_service(session).execute_paper_plan(
        execute_request(ids, plan, authorization, key=key),  # type: ignore[arg-type]
        clock=lambda: when,
    )
    session.commit()
    return result


def _ledger_row(session: Session) -> AccountRiskAccountingState:
    session.expire_all()
    row = session.scalar(select(AccountRiskAccountingState))
    assert row is not None
    return row


def _stored_day(row: AccountRiskAccountingState) -> date | None:
    value = row.trade_slot_business_date
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value[:10])
    return None


def _slot_intent() -> ReservationIntent:
    return ReservationIntent(
        pending_notional=Decimal("1"),
        daily_loss=Decimal("1"),
        trade_slots=1,
        instrument="BTC-USDT",
        exposure_unit="USDT",
        remaining_quantity=Decimal("1"),
        quantity_unit="BASE",
        contract_multiplier=Decimal("1"),
        contract_type="LINEAR",
    )


def _predicate_accounting(*, max_slots: int, reserved: int, actual: int) -> object:
    return type(
        "Ledger",
        (),
        {
            "daily_locked": False,
            "max_notional": Decimal("100"),
            "reserved_notional": Decimal("0"),
            "actual_notional": Decimal("0"),
            "max_daily_loss": Decimal("100"),
            "reserved_daily_loss": Decimal("0"),
            "actual_daily_loss": Decimal("0"),
            "max_trade_slots": max_slots,
            "reserved_trade_slots": reserved,
            "actual_trade_count": actual,
            "max_symbol_notional": Decimal("100"),
            "symbol_reserved": {},
            "symbol_actual": {},
        },
    )()


def _postgres_url() -> str | None:
    raw = os.environ.get("EXPERIMENT_TEST_POSTGRES_URL") or os.environ.get("PHASE1_POSTGRES_URL")
    if not raw:
        return None
    parsed = make_url(raw)
    if parsed.get_backend_name() != "postgresql" or parsed.host not in {"127.0.0.1", "localhost"}:
        return None
    try:
        engine = create_engine(raw, poolclass=NullPool, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        engine.dispose()
    except Exception:
        return None
    return raw


def test_trade_slot_day_revision_is_the_only_head() -> None:
    from tests.support.alembic_heads import alembic_script_directory, single_alembic_head

    script = alembic_script_directory()
    revision = script.get_revision("a22papertradeday001")
    assert revision is not None
    assert revision.down_revision == "a21killswitchkey001"
    assert single_alembic_head(script) == "a22papertradeday001"


def test_trade_slot_day_column_upgrades_downgrades_and_upgrades() -> None:
    raw = _postgres_url()
    if raw is None:
        pytest.skip("Set EXPERIMENT_TEST_POSTGRES_URL to disposable loopback PostgreSQL.")
    import importlib.util
    from pathlib import Path

    from alembic.operations import Operations
    from alembic.runtime.migration import MigrationContext

    path = (
        Path(__file__).resolve().parents[1]
        / "src/app/db/migrations/versions/a22papertradeday001_paper_trade_slot_day.py"
    )
    spec = importlib.util.spec_from_file_location("a22papertradeday001", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    schema = "paper_trade_day_" + uuid.uuid4().hex
    url = make_url(raw)
    admin = create_engine(url, poolclass=NullPool)
    isolated = url.update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(isolated, poolclass=NullPool)
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE account_risk_accounting_states (id INTEGER PRIMARY KEY)")
            )
            context = MigrationContext.configure(connection)
            with Operations.context(context):
                module.upgrade()
            assert _column_present(connection) is True
            with Operations.context(context):
                module.downgrade()
            assert _column_present(connection) is False
            with Operations.context(context):
                module.upgrade()
            assert _column_present(connection) is True
            nullable = connection.scalar(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema = current_schema() "
                    "AND table_name = 'account_risk_accounting_states' "
                    "AND column_name = 'trade_slot_business_date'"
                )
            )
            assert nullable == "YES"
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def _column_present(connection: object) -> bool:
    from sqlalchemy import text as sql_text

    found = connection.scalar(  # type: ignore[attr-defined]
        sql_text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema = current_schema() "
            "AND table_name = 'account_risk_accounting_states' "
            "AND column_name = 'trade_slot_business_date'"
        )
    )
    return found == 1
