"""Two same-candle scans and a restart retain one canonical demo intent."""

from datetime import UTC, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import DateTime, func, select
from sqlalchemy.dialects.sqlite import DATETIME
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.canonical_candidates import CanonicalCandidateRow
from app.db.models import (
    AccountRiskAccountingState,
    ExecutionAccount,
    JournalTrade,
    VenueSubmitEffect,
)
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
from tests.test_governed_blofin_demo import Venue, demo_settings, seed_band_ledger_headroom
from tests.test_sfp_strategy_brain_runtime import (
    BEAR_TRADABLE,
    BULL_TRADABLE,
    runtime_world,
)
from tests.test_sfp_strategy_brain_runtime import store as store


@pytest.mark.parametrize("bearish", [False, True])
def test_governed_sfp_same_candle_rescan_and_restart_never_double_fires(
    store, monkeypatch, bearish
):
    session, org, user, _ = store

    # PostgreSQL returns aware timestamptz; SQLite does not. Normalize only
    # this isolated test engine's result values, retaining the real repositories.
    class AwareDateTime(DATETIME):
        cache_ok = True

        def result_processor(self, dialect, coltype):
            parse = super().result_processor(dialect, coltype)
            return lambda value: parse(value).replace(tzinfo=UTC) if value is not None else None

    engine = session.get_bind()
    # This serial harness uses one connection; production's concurrent lease
    # transactions require PostgreSQL and are covered by its separate gate.
    session.close()
    engine.dispose()
    engine.pool = StaticPool(creator=engine.pool._creator)
    monkeypatch.setattr(
        engine.dialect, "colspecs", {**engine.dialect.colspecs, DateTime: AwareDateTime}
    )
    engine.dialect._type_memos.clear()
    engine.clear_compiled_cache()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    runtime, restart, _policy, now = runtime_world(
        (session, org, user, factory),
        bearish=bearish,
        prices=BEAR_TRADABLE
        if bearish
        else [*BULL_TRADABLE[:3], (103, 114, 102, 104), *BULL_TRADABLE[4:]],
    )
    account = session.scalar(select(ExecutionAccount))
    settings = demo_settings(
        str(account.id),
        governed_demo_sfp_enabled=True,
        governed_demo_auto_leverage_write_enabled=False,
        governed_blofin_demo_organization_id=str(org),
        governed_blofin_demo_user_id=str(user),
    )
    seed_band_ledger_headroom(session, account.id)
    ledger = session.scalar(select(AccountRiskAccountingState))
    ledger.organization_id = org
    session.commit()
    price = Decimal("96" if bearish else "104")
    monkeypatch.setattr("tests.test_governed_blofin_demo._TS", str(int(now.timestamp() * 1000)))
    venue = Venue(price)
    original_handle = venue.handle

    def handle(request):
        response = original_handle(request)
        payload = response.json()
        if request.url.path.endswith("/books"):
            payload["data"][0]["asks"][0][0] = str(price + Decimal("0.01"))
        if request.url.path.endswith("/instruments"):
            payload["data"][0]["tickSize"] = "0.01"
        return httpx.Response(200, json=payload)

    client = BloFinClient(
        base_url=settings.blofin_demo_rest_base_url,
        api_key="simulated-key",
        api_secret="simulated-secret",
        api_passphrase="simulated-pass",
        transport=httpx.MockTransport(handle),
        sleeper=lambda _: None,
    )
    provider = GovernedBloFinDemoProvider(client, clock=lambda: now + timedelta(seconds=1))
    monkeypatch.setattr(GovernedBloFinDemoLoop, "_get_provider", lambda _: provider)
    runtime._settings = settings
    report = runtime.run_cycle()
    assert report.scans and venue.post_count == 1, report
    candidate = report.scans[0].candidate_ids[0]
    assert venue.order["side"] == ("sell" if bearish else "buy")
    stop = Decimal(venue.order["slTriggerPrice"])
    target = Decimal(venue.order["tpTriggerPrice"])
    assert (target < price < stop) if bearish else (stop < price < target)
    assert venue.order["slOrderPrice"] == venue.order["tpOrderPrice"] == "-1"
    # A second queue identity for the same approved episode must share its
    # canonical candidate key, even when the first scope has already fired.
    original_scope = report.scans[0].scan_scope
    monkeypatch.setattr(
        "app.workers.watcher_paper_targets.derive_scan_scope",
        lambda **kwargs: original_scope + ":second",
    )
    for runner in (runtime, restart()):
        runner._settings = settings
        original_key = runner._idempotency_key
        monkeypatch.setattr(
            runner, "_idempotency_key", lambda target, key=original_key: key(target) + ":second"
        )
        again = runner.run_cycle()
        assert again.scans and all(s.scan_scope == original_scope + ":second" for s in again.scans)
        assert all(s.status != "failed" for s in again.scans), again
        assert all(s.candidate_ids in ((), (candidate,)) for s in again.scans)
        assert venue.post_count == 1
    session.expire_all()
    assert session.scalar(select(func.count()).select_from(CanonicalCandidateRow)) == 1
    assert session.scalar(select(func.count()).select_from(VenueSubmitEffect)) == 1
    assert session.scalar(select(func.count()).select_from(JournalTrade)) == 1
