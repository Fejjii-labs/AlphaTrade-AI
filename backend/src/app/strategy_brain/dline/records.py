"""Explicit offline research persistence in existing Brain rows. Never mint Candidates."""

from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserStrategy, UserStrategyVersion
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.services.canonical_serialization import canonical_sha256
from app.services.strategy_versioning import StrategyVersioningService
from app.strategy_brain.dline.contracts import DLINE_KIND, DlineSpec
from app.strategy_brain.dline.detector import DlineScan
from app.strategy_brain.records import _insert_once, scoped_setup_id, transition


def record_dline_scan(
    session: Session, *, organization_id: UUID, user_id: UUID, version_id: UUID, scan: DlineScan
) -> tuple[UUID, ...]:
    version = session.get(UserStrategyVersion, version_id)
    strategy = session.scalar(
        select(UserStrategy).where(
            UserStrategy.id == version.strategy_id if version else False,
            UserStrategy.organization_id == organization_id,
            UserStrategy.user_id == user_id,
        )
    )
    if (
        version is None
        or strategy is None
        or not version.pattern_spec
        or version.pattern_spec.get("kind") != DLINE_KIND
    ):
        raise ValueError("D-line research requires an owned immutable draft version")
    latest = StrategyVersioningService(session).latest_lifecycle_event_for_version(version_id)
    if latest and latest.new_state.value != "draft":
        raise ValueError("D-line research persistence is draft-only")
    spec = DlineSpec.model_validate(version.pattern_spec)
    if scan.spec_hash != canonical_sha256(spec) or any(
        c.spec_hash != scan.spec_hash for c in scan.candidates
    ):
        raise ValueError("D-line scan does not match immutable version")
    ids = []
    for c in scan.candidates:
        identity = scoped_setup_id(organization_id, version_id, c.setup_id)
        ids.append(identity)
        payload = {
            **c.model_dump(mode="json"),
            "family": "d_line",
            "symbol": spec.symbol,
            "instrument": spec.symbol,
            "strategy_version_id": str(version_id),
            "entry": str(c.entry),
            "stop": str(c.stop),
            "targets": [str(t.price) for t in c.targets],
            "research_plan": c.model_dump(mode="json"),
            "risk_state": "not_evaluated",
            "evidence": {"closed_ohlcv": "AVAILABLE", "htf_bias": c.confluences["htf_bias"]},
            "quality_components": c.confluences,
            "evidence_reference": canonical_sha256(c),
            "manual_checklist": [
                "Did you take losses today?",
                "Are you over-excited?",
                "Alcohol or drugs?",
                "Are you OK with the loss?",
            ],
        }
        event_id = uuid5(identity, "draft_detection")
        existing = session.get(BrainSetupEventRow, event_id)
        if existing:
            if existing.payload != payload:
                raise ValueError("Frozen research event conflicts with stored evidence")
            continue
        row = BrainSetupRow(
            id=identity,
            organization_id=organization_id,
            strategy_id=strategy.id,
            strategy_version_id=version_id,
            symbol=spec.symbol,
            state="NO_SETUP",
            observed_at=c.observed_at,
            expires_at=c.expires_at,
            payload=payload,
        )
        transition(row, "FORMING")
        transition(row, "CONFIRMED")
        if not _insert_once(session, row):
            stored = session.get(BrainSetupRow, identity)
            if stored.payload != payload:
                raise ValueError("Conflicting D-line research projection")
        _insert_once(
            session,
            BrainSetupEventRow(
                id=event_id,
                organization_id=organization_id,
                setup_id=identity,
                kind="draft_detection",
                occurred_at=c.observed_at,
                payload=payload,
            ),
        )
    return tuple(ids)
