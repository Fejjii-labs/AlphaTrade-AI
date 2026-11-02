"""SFP projections in the existing tenant Brain setup/event journal."""

from datetime import datetime
from uuid import uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.schemas.nested_continuation import EvidenceAvailability
from app.services.canonical_serialization import canonical_sha256
from app.signal_fusion.strategy_evaluation_policy import ExecutableStrategyPolicy
from app.strategy_brain.records import TERMINAL, _insert_once, aware, scoped_setup_id, transition
from app.strategy_brain.sfp.contracts import SFP_KIND, SfpScan, SfpSpec


def record_sfp_scan(
    session: Session,
    *,
    executable: ExecutableStrategyPolicy,
    scan: SfpScan,
    evidence_hash: str,
    evaluated_at: datetime,
) -> None:
    evaluated_at = aware(evaluated_at)
    spec = executable.authored_spec
    if not isinstance(spec, SfpSpec):
        raise ValueError("SFP persistence requires an SFP policy")
    org, version = executable.organization_id, executable.strategy_version_id
    for event in scan.events:
        if (
            event.spec_hash != canonical_sha256(spec)
            or event.observed_at > evaluated_at
            or event.evidence.interval_end is None
        ):
            raise ValueError("SFP event must bind the immutable spec and causal evaluation time")
        identity = scoped_setup_id(org, version, event.setup_id)
        row = session.scalar(
            select(BrainSetupRow)
            .where(
                BrainSetupRow.id == identity,
                BrainSetupRow.organization_id == org,
                BrainSetupRow.strategy_version_id == version,
            )
            .with_for_update()
        )
        if row is None:
            row = BrainSetupRow(
                id=identity,
                organization_id=org,
                strategy_id=executable.strategy_id,
                strategy_version_id=version,
                symbol=spec.symbol,
                state="NO_SETUP",
                observed_at=event.observed_at,
                expires_at=event.expires_at,
                payload={},
            )
            if not _insert_once(session, row):
                row = session.scalar(
                    select(BrainSetupRow)
                    .where(
                        BrainSetupRow.id == identity,
                        BrainSetupRow.organization_id == org,
                        BrainSetupRow.strategy_version_id == version,
                    )
                    .with_for_update()
                )
            assert row is not None
        event_id = uuid5(identity, str(event.event_id))
        if session.get(BrainSetupEventRow, event_id) is not None or row.state in TERMINAL:
            continue
        previous_time = row.payload.get("event_time")
        if event.observed_at < aware(row.observed_at) or (
            event.observed_at == aware(row.observed_at)
            and previous_time
            and event.event_time < datetime.fromisoformat(previous_time)
        ):
            continue
        if row.state == "NO_SETUP":
            transition(row, "FORMING")
        # A new receipt of a provisional candle cannot reverse a final market state.
        if (
            row.state in {"CONFIRMED", "TRADE_CANDIDATE", "BLOCKED_BY_RISK"}
            and event.state.value == "FORMING"
        ):
            continue
        if row.state not in {"TRADE_CANDIDATE", "BLOCKED_BY_RISK"} or event.state.value in TERMINAL:
            transition(row, event.state.value)
        row.observed_at, row.expires_at = event.observed_at, event.expires_at
        quality = event.quality.model_dump(mode="json")
        payload = {
            **row.payload,
            **event.model_dump(mode="json"),
            "sweep": {
                **event.sweep.model_dump(mode="json"),
                "reference_level": {
                    **event.sweep.reference_level.model_dump(mode="json"),
                    "level_id": str(event.sweep.reference_level.level_id),
                },
            },
            "family": "sfp",
            "kind": SFP_KIND,
            "setup_id": str(identity),
            "detector_setup_id": str(event.setup_id),
            "strategy_id": str(executable.strategy_id),
            "strategy_version_id": str(version),
            "strategy_version_content_hash": executable.strategy_version_content_hash,
            "compiled_setup_definition_id": str(executable.compiled_setup_definition_id),
            "compiled_content_hash": executable.compiled_content_hash,
            "evidence_reference": event.evidence.content_hash,
            "canonical_observation": event.evidence.model_dump(mode="json"),
            "required_evidence_max_age_bars": spec.parameters.required_evidence_max_age_bars,
            "canonical_scan_reference": evidence_hash,
            "evidence_at": event.evidence.observed_at.isoformat(),
            "evidence_close_at": event.evidence.interval_end.isoformat(),
            "last_evaluated_at": evaluated_at.isoformat(),
            "venue": event.evidence.identity.venue.value,
            "instrument": event.evidence.identity.instrument.instrument_id,
            "market_type": event.evidence.identity.market_type.value,
            "timeframe": spec.trigger_timeframe.value,
            "rule_results": list(event.reason_codes),
            "required_evidence": "AVAILABLE",
            "data_quality": "AVAILABLE",
            "evidence": {name: component["availability"] for name, component in quality.items()},
            "quality": quality,
            "risk_state": row.payload.get("risk_state", "not_evaluated"),
        }
        row.payload = payload
        _insert_once(
            session,
            BrainSetupEventRow(
                id=event_id,
                organization_id=org,
                setup_id=identity,
                kind="system_observation",
                occurred_at=event.observed_at,
                payload=payload,
            ),
        )
    record_sfp_availability(
        session,
        executable=executable,
        availability=scan.required_evidence,
        reason_codes=scan.reason_codes,
        evaluated_at=evaluated_at,
        evidence_hash=evidence_hash,
    )


def record_sfp_availability(
    session: Session,
    *,
    executable: ExecutableStrategyPolicy,
    availability: EvidenceAvailability,
    reason_codes: tuple[str, ...],
    evaluated_at: datetime,
    evidence_hash: str,
) -> None:
    evaluated_at = aware(evaluated_at)
    if not isinstance(executable.authored_spec, SfpSpec):
        raise ValueError("SFP availability requires an SFP policy")
    # Expiry is durable even when the provider is down. The clock does not invent a candle.
    rows = session.scalars(
        select(BrainSetupRow)
        .where(
            BrainSetupRow.organization_id == executable.organization_id,
            BrainSetupRow.strategy_version_id == executable.strategy_version_id,
            # One lineage may be scanned on several symbols (projected scopes).
            BrainSetupRow.symbol == executable.authored_spec.symbol,
            BrainSetupRow.state.not_in(TERMINAL),
        )
        .with_for_update()
    )
    timeframe = executable.authored_spec.trigger_timeframe.value
    for row in rows:
        # ETH 15m and ETH 1h projections of one version keep separate availability.
        if row.payload.get("timeframe", timeframe) != timeframe:
            continue
        previous = row.payload.get("last_evaluated_at")
        if previous and datetime.fromisoformat(previous) > evaluated_at:
            continue
        expired = evaluated_at >= aware(row.expires_at)
        if expired:
            transition(row, "EXPIRED")
        changed = row.payload.get("required_evidence") != availability.value
        if not expired and not changed:
            continue
        payload = {
            **row.payload,
            "state": row.state,
            "required_evidence": availability.value,
            "data_quality": availability.value,
            "last_evaluated_at": evaluated_at.isoformat(),
            "reason_codes": list(
                dict.fromkeys(
                    [
                        *row.payload.get("reason_codes", []),
                        *reason_codes,
                        *(("setup_expired",) if expired else ()),
                    ]
                )
            ),
        }
        row.payload = payload
        _insert_once(
            session,
            BrainSetupEventRow(
                id=uuid5(
                    row.id,
                    f"{'expiry' if expired else 'availability'}:"
                    f"{evidence_hash}:{availability.value}",
                ),
                organization_id=row.organization_id,
                setup_id=row.id,
                kind="setup_expired" if expired else "evidence_availability",
                occurred_at=evaluated_at,
                payload=payload,
            ),
        )
