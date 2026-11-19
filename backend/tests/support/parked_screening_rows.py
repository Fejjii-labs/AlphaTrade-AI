"""Seed historical screening rows without the parked screening runtime."""

from uuid import uuid4

from sqlalchemy.orm import Session

from app.schemas.experiments import ExperimentVersion
from app.strategy_brain.trendpulse_screening.models import TrendPulseScreeningRow
from tests.test_trendpulse_1r_adapter import END


def seed_screening_history(session: Session, version: ExperimentVersion) -> TrendPulseScreeningRow:
    common = {
        "organization_id": version.organization_id,
        "user_id": version.user_id,
        "experiment_id": version.experiment_id,
        "version_id": version.id,
        "configuration_hash": version.configuration_hash,
        "variant_key": "baseline",
        "strategy_version_id": version.configuration.strategy_version_id,
        "strategy_content_hash": version.strategy_content_hashes[
            str(version.configuration.strategy_version_id)
        ],
        "trigger_end": END,
        "acquisition_started_at": END,
        "decision_at": END,
        "evidence_mode": "replay",
        "receipt_provenance": "synthetic_fixture",
        "trend_receipts": 250,
        "entry_receipts": 60,
        "evidence": {"fixture": True},
        "evidence_hash": "a" * 64,
    }
    first = TrendPulseScreeningRow(
        **common,
        request_id=uuid4(),
        request_hash="b" * 64,
        status="qualified_research_signal",
        reason="fixture_qualified",
        signal_id=uuid4(),
        signal={"fixture": True},
        dedupe_key="c" * 64,
    )
    session.add(first)
    session.flush()
    session.add_all(
        [
            TrendPulseScreeningRow(
                **common,
                request_id=uuid4(),
                request_hash="d" * 64,
                status="duplicate",
                reason="fixture_duplicate",
                signal_id=first.signal_id,
                duplicate_of=first.id,
            ),
            TrendPulseScreeningRow(
                **common,
                request_id=uuid4(),
                request_hash="e" * 64,
                status="refused",
                reason="trigger_expired",
            ),
        ]
    )
    session.flush()
    return first
