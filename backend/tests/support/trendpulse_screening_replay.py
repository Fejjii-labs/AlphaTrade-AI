"""Fixed-seed synthetic regime replay. Zero real market/performance coverage claims.

Run explicitly with a disposable loopback EXPERIMENT_TEST_POSTGRES_URL. The
bounded harness creates/drops its own UUID schema and never calls a provider.
"""

import argparse
import json
import os
import random
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.base import Base
from app.experiments.models import ExperimentSampleRow
from app.market_contracts.enums import FreshnessState
from app.market_contracts.first_slice import first_slice_identity
from app.market_contracts.observation import observation_from_ohlcv
from app.market_contracts.ohlcv import build_ohlcv_bar
from app.schemas.common import Timeframe
from app.schemas.experiments import ExperimentConfiguration
from app.schemas.trendpulse_screening import TrendPulseScreeningCreate, TrendPulseScreeningEvidence
from app.strategy_brain.trendpulse_1r.contracts import TRENDPULSE_ADAPTER_VERSION, exact_hash
from app.strategy_brain.trendpulse_screening.recorded import (
    RecordedScreeningAcquirer,
    RecordedScreeningWindow,
)
from app.strategy_brain.trendpulse_screening.service import TrendPulseScreeningService
from tests.support.experiment_fixtures import World
from tests.test_trendpulse_1r_adapter import rules, spec
from tests.test_trendpulse_1r_domain import configure

START = datetime(2026, 10, 1, tzinfo=UTC)
REGIMES = ("uptrend", "downtrend", "range", "volatile")


def receipt(bar, timeframe):
    obs = observation_from_ohlcv(
        bar,
        identity=first_slice_identity(timeframe=timeframe, replay=True),
        observed_at=bar.interval_end + timedelta(seconds=2),
        receive_time=bar.interval_end + timedelta(seconds=3),
        freshness_state=FreshnessState.FRESH,
    )
    return obs.model_copy(update={"recorded_at": bar.interval_end + timedelta(seconds=4)})


def regime(regime_name):
    # Fixed inputs chosen before observing counts, not optimized to force setups.
    seed = 24300 + REGIMES.index(regime_name)
    generator = random.Random(seed)
    drift = {"uptrend": 25, "downtrend": -25, "range": 0, "volatile": 0}[regime_name]
    noise = 200 if regime_name == "volatile" else 60
    price = Decimal("65000")
    m5 = []
    identity = first_slice_identity(timeframe=Timeframe.M5, replay=True)
    for i in range(900):
        opening = price
        price += Decimal(drift + generator.randint(-noise, noise))
        high = max(opening, price) + generator.randint(10, 100)
        low = min(opening, price) - generator.randint(10, 100)
        start = START + timedelta(minutes=5 * i)
        m5.append(
            build_ohlcv_bar(
                instrument=identity.instrument,
                timeframe=Timeframe.M5,
                interval_start=start,
                open_=opening,
                high=high,
                low=low,
                close=price,
                base_volume=Decimal("100"),
                quote_volume=price * 100,
                evaluated_at=start + timedelta(minutes=5, seconds=2),
                grace=timedelta(0),
                adapter_version=identity.source.adapter_version,
            )
        )
    m15 = []
    for i in range(0, 900, 3):
        group = m5[i : i + 3]
        m15.append(
            build_ohlcv_bar(
                instrument=identity.instrument,
                timeframe=Timeframe.M15,
                interval_start=group[0].interval_start,
                open_=group[0].open,
                high=max(b.high for b in group),
                low=min(b.low for b in group),
                close=group[-1].close,
                base_volume=sum((b.base_volume for b in group), Decimal(0)),
                quote_volume=sum((b.quote_volume for b in group), Decimal(0)),
                evaluated_at=group[-1].interval_end + timedelta(seconds=2),
                grace=timedelta(0),
                adapter_version=identity.source.adapter_version,
            )
        )
    return m5, m15


def windows(regime_name):
    m5, m15 = regime(regime_name)
    for index in range(750, 774):
        trigger_end = m5[index].interval_end
        opening = m5[index].interval_start
        trend_end = opening.replace(minute=opening.minute // 15 * 15, second=0, microsecond=0)
        trend = tuple(b for b in m15 if b.interval_end <= trend_end)[-250:]
        entry = tuple(m5[index - 59 : index + 1])
        evidence = TrendPulseScreeningEvidence(
            trend_bars=trend,
            entry_bars=entry,
            trend_observations=tuple(receipt(b, Timeframe.M15) for b in trend),
            entry_observations=tuple(receipt(b, Timeframe.M5) for b in entry),
            instrument_rules=rules(tick_size="0.10"),
        )
        for short in (False, True):
            yield (
                "short" if short else "long",
                RecordedScreeningWindow(
                    provenance="synthetic_fixture",
                    spec_hash=exact_hash(spec(short)),
                    trigger_end=trigger_end,
                    decision_at=trigger_end + timedelta(seconds=8),
                    evidence=evidence,
                ),
            )


def run(output):
    url = os.environ["EXPERIMENT_TEST_POSTGRES_URL"]
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost"} or "test" not in (parsed.database or ""):
        raise ValueError("Synthetic replay requires a disposable loopback test database")
    admin = create_engine(url)
    schema = "screening_regime_replay_" + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(parsed.update_query_dict({"options": "-csearch_path=" + schema}))
    report = {
        "contract_version": "trendpulse-synthetic-replay-report/v1",
        "adapter_version": TRENDPULSE_ADAPTER_VERSION,
        "provenance": "synthetic_fixture",
        "symbol": "BTCUSDT",
        "seed_base": 24300,
        "parameter_thresholds_changed": False,
        "receipt_delay_seconds": {"observed": 2, "received": 3, "recorded": 4, "decision": 8},
        "source_start": START.isoformat(),
        "source_end": (START + timedelta(minutes=4500)).isoformat(),
        "raw_m5_bars_per_regime": 900,
        "aggregated_m15_bars_per_regime": 300,
        "warmup_m15_bars": 250,
        "entry_m5_bars": 60,
        "real_market_decisions": 0,
        "native_trades": 0,
        "performance": None,
        "regimes": {},
    }
    snapshots = []
    try:
        Base.metadata.create_all(engine)
        with Session(engine, expire_on_commit=False) as session:
            w = World(session)
            config = configure(w)
            short_version = w.add_strategy_version(spec(True), 2)
            long_variant = config.variants[0].model_copy(update={"key": "long"})
            short_variant = long_variant.model_copy(
                update={"key": "short", "strategy_version_id": short_version.id}
            )
            config = ExperimentConfiguration.model_validate(
                {**config.model_dump(), "variants": (long_variant, short_variant)}
            )
            w.version = w.create(config)
            session.commit()
            settings = w.settings.model_copy(update={"trendpulse_screening_enabled": True})
            for name in REGIMES:
                counts, reasons, direction_counts, positions = (
                    Counter(),
                    Counter(),
                    Counter(),
                    Counter(),
                )
                for variant, window in windows(name):
                    body = TrendPulseScreeningCreate(
                        request_id=uuid5(
                            NAMESPACE_URL,
                            f"trendpulse-regime/v1/{name}/{variant}/{window.trigger_end.isoformat()}",
                        ),
                        variant_key=variant,
                        trigger_end=window.trigger_end,
                    )
                    svc = TrendPulseScreeningService(
                        session,
                        settings,
                        acquirer=RecordedScreeningAcquirer(window),
                        clock=lambda captured=window: captured.decision_at,
                    )
                    result = svc.screen(w.tenant, w.version.experiment_id, w.version.id, body)
                    session.commit()
                    counts[result.status.value] += 1
                    reasons[result.reason] += 1
                    direction_counts[variant] += 1
                    positions[str(window.trigger_end.minute % 15)] += 1
                    snapshots.append((body, result.id, result.evidence_hash))
                report["regimes"][name] = {
                    "decisions": sum(counts.values()),
                    "statuses": dict(counts),
                    "reasons": dict(reasons),
                    "directions": dict(direction_counts),
                    "close_positions": dict(positions),
                }
            tenant, root_id, version_id = w.tenant, w.version.experiment_id, w.version.id
        # A fresh session/connection verifies every immutable snapshot after restart.
        with Session(engine) as restarted:
            reader = TrendPulseScreeningService(restarted, settings)
            for body, record_id, digest in snapshots:
                stored = reader.detail(tenant, record_id)
                assert stored.evidence_hash == digest
                assert reader.screen(tenant, root_id, version_id, body).id == record_id
            report["restart_idempotent_records"] = len(snapshots)
            report["experiment_samples"] = restarted.scalar(
                select(func.count()).select_from(ExperimentSampleRow)
            )
            assert report["experiment_samples"] == 0
            report["decisions"] = len(snapshots)
            report["signals"] = sum(
                r["statuses"].get("qualified_research_signal", 0)
                for r in report["regimes"].values()
            )
            report["evidence_manifest_hash"] = exact_hash(
                {"evidence_hashes": [s[2] for s in snapshots]}
            )
        Path(output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        print(json.dumps(report, indent=2, sort_keys=True))
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    run(parser.parse_args().output)
