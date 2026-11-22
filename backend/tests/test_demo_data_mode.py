"""Demo data mode: on BloFin DEMO, take every valid setup on every scanned coin.

GOVERNED_DEMO_DATA_MODE_ENABLED (default off, needs risk model v2, never with real
trading) lifts two data-limiting demo gates: the single autonomous coin, and
one open demo position per account. One position per coin, the kill switch and
the v2 daily stop stay.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

import httpx
import pytest

from app.core.config import Settings
from app.schemas.execution_protocol import ExecutionCommandOutcome
from app.schemas.trade_plan import EntrySide
from app.services.audit_service import AuditService
from app.services.autonomous_trading_guard import autonomous_precheck_refusal
from app.services.demo_account_history import has_demo_entry_history
from app.services.demo_data_mode import demo_data_mode_enabled
from app.services.execution_service import ExecutionService
from tests.support.phase1_plan_fixtures import (
    EXECUTE_AT,
    approve_plan,
    execute_request,
    paper_settings,
    persist_plan,
    plan_request,
    prepared_authorized_plan,
)
from tests.support.phase5_market import EVALUATED_AT
from tests.support.postgres_persistence import phase7_plan_session_factory, requires_postgres
from tests.test_autonomous_multi_strategy import SHORT, _resolved, _snapshot, _status, _target
from tests.test_governed_blofin_demo import Venue
from tests.test_governed_demo_readiness import provider_for

# --------------------------------------------------------------------------- #
# Flag
# --------------------------------------------------------------------------- #


def test_flag_defaults_off_and_parses_strictly() -> None:
    assert Settings(_env_file=None).governed_demo_data_mode_enabled is False
    assert Settings(
        _env_file=None, governed_demo_data_mode_enabled="true"
    ).governed_demo_data_mode_enabled
    with pytest.raises(ValueError):
        Settings(_env_file=None, governed_demo_data_mode_enabled="yes")


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "governed_demo_data_mode_enabled": True,
        "risk_model_v2_enabled": True,
        "enable_real_trading": False,
    }
    values.update(updates)
    return Settings(_env_file=None).model_copy(update=values)


def test_data_mode_needs_risk_v2_and_no_real_trading() -> None:
    assert demo_data_mode_enabled(_settings())
    assert not demo_data_mode_enabled(_settings(governed_demo_data_mode_enabled=False))
    # The v2 daily stop is the brake data mode relies on.
    assert not demo_data_mode_enabled(_settings(risk_model_v2_enabled=False))
    assert not demo_data_mode_enabled(_settings(enable_real_trading=True))
    assert not demo_data_mode_enabled(None)


def test_data_mode_refuses_when_derived_real_trading_flag_is_on(monkeypatch) -> None:
    # real_trading_enabled is a fail-closed property (always False today); guard on it too.
    monkeypatch.setattr(Settings, "real_trading_enabled", property(lambda self: True))
    assert not demo_data_mode_enabled(_settings())


# --------------------------------------------------------------------------- #
# Guard: any coin, same strategy/approval/cap checks
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("symbol", ["BTCUSDT", None])
def test_any_coin_is_accepted_in_data_mode(symbol) -> None:
    refusal = autonomous_precheck_refusal(
        _resolved(symbol=symbol),
        _target(symbol="ETHUSDT"),
        _snapshot(),
        strategy_status=_status(),
        require_paper_eligible=False,
        any_symbol=True,
    )
    assert refusal is None


def test_data_mode_keeps_strategy_approval_and_trade_cap() -> None:
    other = uuid4()
    eth = {"require_paper_eligible": False, "any_symbol": True}
    assert (
        autonomous_precheck_refusal(
            _resolved(),
            _target(other, symbol="ETHUSDT"),
            _snapshot(),
            strategy_status=_status(other),
            **eth,
        )
        == "autonomous_strategy_mismatch"
    )
    assert (
        autonomous_precheck_refusal(
            _resolved(),
            _target(symbol="ETHUSDT"),
            _snapshot(),
            strategy_status=_status(approved=False),
            **eth,
        )
        == "autonomous_strategy_not_approved"
    )
    assert (
        autonomous_precheck_refusal(
            _resolved(max_trades_per_day=2),
            _target(SHORT, symbol="ETHUSDT"),
            _snapshot(trade_count=2),
            strategy_status=_status(SHORT),
            **eth,
        )
        == "autonomous_max_trades_per_day"
    )
    assert (
        autonomous_precheck_refusal(
            _resolved(strategy_ids=frozenset()),
            _target(symbol="ETHUSDT"),
            _snapshot(),
            strategy_status=_status(),
            **eth,
        )
        == "autonomous_settings_incomplete"
    )


def test_without_data_mode_the_single_coin_still_applies() -> None:
    assert (
        autonomous_precheck_refusal(
            _resolved(),
            _target(symbol="ETHUSDT"),
            _snapshot(),
            strategy_status=_status(),
            require_paper_eligible=False,
        )
        == "autonomous_symbol_mismatch"
    )


# --------------------------------------------------------------------------- #
# Venue preflight: flat on this coin only
# --------------------------------------------------------------------------- #


def _venue_with(endpoint: str, rows: object):
    venue = Venue(Decimal("100000"))

    def handler(request):
        if request.url.path.endswith("/" + endpoint):
            return httpx.Response(200, json={"code": "0", "data": rows})
        return venue.handle(request)

    return venue, handler


@pytest.mark.parametrize("endpoint", ["positions", "orders-pending", "orders-tpsl-pending"])
def test_other_coin_exposure_does_not_block_in_data_mode(endpoint) -> None:
    venue, handler = _venue_with(endpoint, [{"instId": "ETH-USDT", "positions": "1"}])
    provider = provider_for(handler)
    provider.flat_per_instrument = True
    snapshot = provider.snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)
    assert snapshot.instrument == "BTC-USDT" and venue.post_count == 0


@pytest.mark.parametrize("endpoint", ["positions", "orders-pending", "orders-tpsl-pending"])
@pytest.mark.parametrize(
    "rows",
    [
        [{"instId": "BTC-USDT", "positions": "1"}],
        # Unattributable or unreadable rows cannot prove this coin is flat.
        [{"positions": "1"}],
        [None],
        None,
        {},
        # A full page may be truncated: absence on this coin is unproven.
        [{"instId": "ETH-USDT", "positions": "1"}] * 100,
    ],
)
def test_same_coin_or_unprovable_exposure_still_refuses(endpoint, rows) -> None:
    venue, handler = _venue_with(endpoint, rows)
    provider = provider_for(handler)
    provider.flat_per_instrument = True
    with pytest.raises((ValueError, ArithmeticError)):
        provider.snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)
    assert venue.post_count == 0


def test_default_provider_stays_account_wide() -> None:
    _, handler = _venue_with("positions", [{"instId": "ETH-USDT", "positions": "1"}])
    with pytest.raises(ValueError):
        provider_for(handler).snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)


# --------------------------------------------------------------------------- #
# Claim: one demo entry per coin instead of per account
# --------------------------------------------------------------------------- #


def _claim_both(monkeypatch, instruments: tuple[str, str], data_mode: bool):
    # These fixture plans are v1-margin plans; the v2 requirement of the helper
    # is covered above, so the claim reads the flag directly here.
    monkeypatch.setattr(
        "app.services.execution_claim.demo_data_mode_enabled",
        lambda settings: settings.governed_demo_data_mode_enabled,
    )
    factory = phase7_plan_session_factory()
    with factory() as session:
        ids, first, first_auth = prepared_authorized_plan(session)
        second = persist_plan(session, ids, plan_request(ids, execution_instrument=instruments[1]))
        second_auth = approve_plan(session, ids, second)
        assert first.execution_instrument == instruments[0]
        requests = [
            execute_request(ids, p, a, key=str(uuid4()))
            for p, a in ((first, first_auth), (second, second_auth))
        ]
        session.commit()
    settings = paper_settings(database_url="sqlite+pysqlite:///:memory:").model_copy(
        update={
            "governed_blofin_demo_enabled": True,
            "governed_demo_data_mode_enabled": data_mode,
        }
    )
    barrier = Barrier(2)

    def claim(request):
        with factory() as session:
            barrier.wait(timeout=20)
            result = ExecutionService(session, settings, AuditService(session)).execute_paper_plan(
                request, clock=lambda: EXECUTE_AT
            )
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        return factory, ids, list(pool.map(claim, requests))


@requires_postgres
def test_data_mode_claims_two_coins_concurrently(monkeypatch) -> None:
    factory, ids, results = _claim_both(monkeypatch, ("BTC-USDT", "ETH-USDT"), data_mode=True)
    assert [r.outcome for r in results] == [ExecutionCommandOutcome.ALLOW] * 2, [
        r.blocked_reason_code for r in results
    ]
    with factory() as session:
        scope = {"organization_id": ids["organization"].id, "account_id": ids["account_one"].id}
        assert has_demo_entry_history(session, **scope)
        assert has_demo_entry_history(session, **scope, instrument="BTC-USDT")
        assert has_demo_entry_history(session, **scope, instrument="ETH-USDT")
        assert not has_demo_entry_history(session, **scope, instrument="ZEC-USDT")


@requires_postgres
def test_data_mode_still_allows_one_entry_per_coin(monkeypatch) -> None:
    _, _, results = _claim_both(monkeypatch, ("BTC-USDT", "BTC-USDT"), data_mode=True)
    assert sum(r.outcome is ExecutionCommandOutcome.ALLOW for r in results) == 1
    assert [r.blocked_reason_code for r in results if r.blocked_reason_code] == [
        "demo_account_already_claimed"
    ]


@requires_postgres
def test_without_data_mode_one_entry_per_account_holds(monkeypatch) -> None:
    _, _, results = _claim_both(monkeypatch, ("BTC-USDT", "ETH-USDT"), data_mode=False)
    assert sum(r.outcome is ExecutionCommandOutcome.ALLOW for r in results) == 1


# --------------------------------------------------------------------------- #
# Leverage write: per-coin flat, account-wide flat only for a margin-mode change
# --------------------------------------------------------------------------- #


def _leverage_provider(margin_mode: str):
    venue, handler = _venue_with("positions", [{"instId": "ETH-USDT", "positions": "1"}])
    venue.margin_mode = margin_mode
    provider = provider_for(handler)
    provider.flat_per_instrument = True
    return venue, provider


def test_leverage_write_on_a_flat_coin_while_another_coin_is_open() -> None:
    venue, provider = _leverage_provider("isolated")
    provider.apply_configured_leverage(
        symbol="BTCUSDT", leverage=Decimal("10"), margin_mode="isolated"
    )
    assert [path for path, _ in venue.account_writes] == ["/api/v1/account/set-leverage"]


def test_margin_mode_change_still_needs_the_whole_account_flat() -> None:
    venue, provider = _leverage_provider("cross")
    with pytest.raises(ValueError):
        provider.apply_configured_leverage(
            symbol="BTCUSDT", leverage=Decimal("10"), margin_mode="isolated"
        )
    assert venue.account_writes == []
