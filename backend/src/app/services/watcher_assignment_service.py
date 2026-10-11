"""Single monitoring assignment authority for HTTP, confirmed Agent actions and workers.

The caller owns commit/rollback. Assignment writes never alter saved versions,
watchlist toggles, approvals, credentials, execution or operator settings.
"""

from collections import Counter
from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationAppError
from app.db.models import Membership, UserStrategy, UserStrategyVersion
from app.db.strategy_immutability import strategy_version_content_hash
from app.db.watcher_assignments import WatcherAssignmentReceiptRow, WatcherStrategyAssignmentRow
from app.db.watcher_watchlist import WatcherSymbolStatusRow
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import MembershipRole
from app.schemas.watcher_assignments import (
    MAX_MONITORING_ASSIGNMENTS,
    WatcherAssignmentObservation,
    WatcherAssignmentRead,
    WatcherAssignmentReplace,
    WatcherAssignmentsRead,
    WatcherAssignmentVersion,
)
from app.services.canonical_serialization import canonical_sha256
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.watcher.contracts import EvaluationCommand
from app.watcher.fusion_evaluation import CandidatePersistenceFence
from app.workers.watcher_paper_targets import PaperScanTarget


def version_snapshot(version: UserStrategyVersion) -> tuple[list[str], dict[str, Any]]:
    """Exact saved authored parameters; configuration cannot mutate compiler input."""
    parameters = dict(version.pattern_spec or {})
    timeframes = list(
        dict.fromkeys(
            str(parameters[key])
            for key in ("trigger_timeframe", "context_timeframe")
            if parameters.get(key)
        )
    )
    if not timeframes:
        timeframes = list(dict.fromkeys(str(v) for v in (version.card or {}).get("timeframes", [])))
    return timeframes, parameters


def assignment_configuration_exists(session: Session, organization_id: UUID) -> bool:
    return (
        session.scalar(
            select(WatcherAssignmentReceiptRow.request_key)
            .where(WatcherAssignmentReceiptRow.organization_id == organization_id)
            .limit(1)
        )
        is not None
    )


def managed_assignment_coverage(
    session: Session,
    organization_id: UUID,
    *,
    coverage: tuple[UUID, UUID, str] | None = None,
) -> set[tuple[UUID, UUID, str]]:
    """Durable actor/strategy/coin overrides, including explicitly removed bindings.

    An original response receipt retains previously managed coverage. It never
    grants monitoring authority: fresh live rows own explicit targets. Project
    just distinct identifiers in PostgreSQL rather than loading full snapshots.
    """
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy import text

        query = (
            "SELECT DISTINCT receipts.user_id, item->>'strategy_id', item->>'symbol' "
            "FROM watcher_assignment_receipts AS receipts "
            "CROSS JOIN LATERAL jsonb_array_elements("
            "COALESCE(receipts.response::jsonb->'assignments', '[]'::jsonb)) AS item "
            "WHERE receipts.organization_id = :organization_id"
        )
        parameters: dict[str, Any] = {"organization_id": organization_id}
        if coverage is not None:
            query += (
                " AND receipts.user_id = :actor AND item->>'strategy_id' = :strategy"
                " AND item->>'symbol' = :symbol LIMIT 1"
            )
            parameters.update(actor=coverage[0], strategy=str(coverage[1]), symbol=coverage[2])
        rows = session.execute(text(query), parameters)
        return {(UUID(str(actor)), UUID(strategy), symbol) for actor, strategy, symbol in rows}
    result: set[tuple[UUID, UUID, str]] = set()
    receipt_query = select(
        WatcherAssignmentReceiptRow.user_id, WatcherAssignmentReceiptRow.response
    ).where(WatcherAssignmentReceiptRow.organization_id == organization_id)
    if coverage is not None:
        receipt_query = receipt_query.where(WatcherAssignmentReceiptRow.user_id == coverage[0])
    for actor, response in session.execute(receipt_query.execution_options(yield_per=20)):
        for assignment in response.get("assignments", []):
            item = (actor, UUID(assignment["strategy_id"]), assignment["symbol"])
            if coverage is None:
                result.add(item)
            elif item == coverage:
                return {item}
    return result


class WatcherAssignmentService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.watchlists = WatcherWatchlistRepository(session)

    def _member(self, organization_id: UUID, user_id: UUID, *, write: bool = False) -> None:
        member = self.session.scalar(
            select(Membership)
            .where(Membership.organization_id == organization_id, Membership.user_id == user_id)
            .execution_options(populate_existing=True)
        )
        allowed = {MembershipRole.OWNER, MembershipRole.TRADER}
        if not write:
            allowed.add(MembershipRole.VIEWER)
        if member is None or member.role not in allowed:
            raise ForbiddenError("Monitoring assignment access is unavailable.")

    def _versions(
        self, organization_id: UUID, user_id: UUID
    ) -> list[tuple[UserStrategyVersion, UserStrategy]]:
        return list(
            self.session.execute(
                select(UserStrategyVersion, UserStrategy)
                .join(UserStrategy, UserStrategy.id == UserStrategyVersion.strategy_id)
                .where(
                    UserStrategy.organization_id == organization_id, UserStrategy.user_id == user_id
                )
                .order_by(UserStrategy.name, UserStrategyVersion.version.desc())
                .limit(100)
                .execution_options(populate_existing=True)
            ).tuples()
        )

    def _version(
        self, organization_id: UUID, user_id: UUID, version_id: UUID
    ) -> tuple[UserStrategyVersion, UserStrategy]:
        value = self.session.execute(
            select(UserStrategyVersion, UserStrategy)
            .join(UserStrategy, UserStrategy.id == UserStrategyVersion.strategy_id)
            .where(
                UserStrategyVersion.id == version_id,
                UserStrategy.organization_id == organization_id,
                UserStrategy.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        ).first()
        if value is None:
            raise NotFoundError("Saved strategy version not found.")
        version, strategy = value
        expected = strategy_version_content_hash(
            card=version.card,
            structured_rules=version.structured_rules,
            lesson_source_metadata=version.lesson_source_metadata,
            pattern_spec=version.pattern_spec,
        )
        if version.content_hash != expected:
            raise ValidationAppError("Saved strategy version integrity is unavailable.")
        return version, strategy

    def eligibility(
        self, row: WatcherStrategyAssignmentRow, *, enabled_symbols: set[str]
    ) -> tuple[bool, str]:
        if not row.monitoring_enabled:
            return False, "monitoring_disabled"
        if row.symbol not in enabled_symbols:
            return False, "coin_disabled"
        try:
            self._member(row.organization_id, row.user_id, write=True)
            version, strategy = self._version(
                row.organization_id, row.user_id, row.strategy_version_id
            )
        except (ForbiddenError, NotFoundError, ValidationAppError):
            return False, "strategy_binding_unavailable"
        if not strategy.enabled:
            return False, "saved_strategy_disabled"
        timeframes, parameters = version_snapshot(version)
        if (
            version.content_hash != row.version_content_hash
            or row.timeframes != timeframes
            or row.parameters != parameters
        ):
            return False, "version_snapshot_mismatch"
        if parameters.get("symbol") != row.symbol:
            return False, "authored_symbol_mismatch" if parameters else "authored_pattern_missing"
        try:
            resolve_executable_strategy_policy(
                self.session,
                organization_id=row.organization_id,
                user_id=row.user_id,
                strategy_version_id=row.strategy_version_id,
            )
        except StrategyEvaluationPolicyError as exc:
            return False, exc.reason_code
        except NotFoundError:
            return False, "strategy_binding_unavailable"
        return True, "ready"

    def read(self, *, organization_id: UUID, user_id: UUID) -> WatcherAssignmentsRead:
        self._member(organization_id, user_id)
        config = self.watchlists.load(organization_id)
        versions = self._versions(organization_id, user_id)
        options = []
        for version, strategy in versions:
            timeframes, parameters = version_snapshot(version)
            options.append(
                WatcherAssignmentVersion(
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    strategy_name=strategy.name,
                    version=version.version,
                    version_content_hash=version.content_hash,
                    symbol=parameters.get("symbol"),
                    timeframes=timeframes,
                    parameters=parameters,
                )
            )
        observations: dict[UUID, WatcherAssignmentObservation] = {}
        now = datetime.now(UTC)
        for status in self.session.scalars(
            select(WatcherSymbolStatusRow).where(
                WatcherSymbolStatusRow.organization_id == organization_id,
                WatcherSymbolStatusRow.configuration_revision == config.revision,
            )
        ):
            observed = (
                status.observed_at.replace(tzinfo=UTC)
                if status.observed_at.tzinfo is None
                else status.observed_at
            )
            if not 0 <= (now - observed).total_seconds() <= 90:
                continue
            for raw in status.payload.get("assignment_observations", []):
                fact = WatcherAssignmentObservation.model_validate(raw)
                if fact.configuration_revision == config.revision:
                    observations[fact.assignment_id] = fact
        assignments = []
        for row in self.session.scalars(
            select(WatcherStrategyAssignmentRow)
            .where(
                WatcherStrategyAssignmentRow.organization_id == organization_id,
                WatcherStrategyAssignmentRow.user_id == user_id,
            )
            .order_by(
                WatcherStrategyAssignmentRow.symbol,
                WatcherStrategyAssignmentRow.created_at,
                WatcherStrategyAssignmentRow.id,
            )
            .execution_options(populate_existing=True)
        ):
            version, strategy = self._version(organization_id, user_id, row.strategy_version_id)
            eligible, reason = self.eligibility(row, enabled_symbols=set(config.enabled_symbols()))
            assignments.append(
                WatcherAssignmentRead(
                    id=row.id,
                    symbol=row.symbol,
                    strategy_version_id=row.strategy_version_id,
                    monitoring_enabled=row.monitoring_enabled,
                    timeframes=row.timeframes,
                    parameters=row.parameters,
                    strategy_id=strategy.id,
                    strategy_name=strategy.name,
                    version=version.version,
                    version_content_hash=row.version_content_hash,
                    eligible=eligible,
                    reason_code=reason,
                    latest=observations.get(row.id),
                )
            )
        return WatcherAssignmentsRead(
            revision=config.revision,
            configured=assignment_configuration_exists(self.session, organization_id),
            assignments=assignments,
            available_versions=options,
            symbols=[slot.symbol for slot in config.slots],
        )

    def replace(
        self, *, organization_id: UUID, user_id: UUID, body: WatcherAssignmentReplace
    ) -> WatcherAssignmentsRead:
        self._member(organization_id, user_id, write=True)
        self.watchlists._lock_organization(organization_id)
        payload_hash = canonical_sha256(body.model_dump(mode="json", exclude={"request_key"}))
        receipt = self.session.get(
            WatcherAssignmentReceiptRow, (organization_id, body.request_key), populate_existing=True
        )
        if receipt is not None:
            if receipt.user_id != user_id or receipt.payload_hash != payload_hash:
                raise ConflictError(
                    "Monitoring request key already belongs to a different request."
                )
            return WatcherAssignmentsRead.model_validate(receipt.response)
        config = self.watchlists.load(organization_id)
        if body.revision != config.revision:
            raise ConflictError("Monitoring configuration changed; reload before saving.")
        symbols = {slot.symbol for slot in config.slots}
        existing = list(
            self.session.scalars(
                select(WatcherStrategyAssignmentRow)
                .where(WatcherStrategyAssignmentRow.organization_id == organization_id)
                .execution_options(populate_existing=True)
            )
        )
        other = [row for row in existing if row.user_id != user_id]
        if len(other) + len(body.assignments) > MAX_MONITORING_ASSIGNMENTS:
            raise ValidationAppError("At most 25 monitoring assignments are supported.")
        counts = Counter(row.symbol for row in other)
        counts.update(item.symbol for item in body.assignments)
        if any(value > 5 for value in counts.values()):
            raise ValidationAppError("At most five saved versions can monitor one coin.")
        prepared = []
        for item in body.assignments:
            if item.symbol not in symbols:
                raise ValidationAppError(
                    "Add this coin to the watchlist before assigning strategies."
                )
            version, strategy = self._version(organization_id, user_id, item.strategy_version_id)
            timeframes, parameters = version_snapshot(version)
            if item.timeframes != timeframes or item.parameters != parameters:
                raise ValidationAppError(
                    "Use saved timeframes and parameters; author a new version to change them."
                )
            if parameters and parameters.get("symbol") != item.symbol:
                raise ValidationAppError("Saved strategy is authored for a different coin.")
            collision = self.session.get(
                WatcherStrategyAssignmentRow, item.id, populate_existing=True
            )
            if collision is not None and (
                collision.organization_id != organization_id or collision.user_id != user_id
            ):
                raise NotFoundError("Monitoring assignment not found.")
            prepared.append((item, version, strategy))
        now = datetime.now(UTC)
        wanted = {item.id for item in body.assignments}
        self.session.execute(
            delete(WatcherStrategyAssignmentRow).where(
                WatcherStrategyAssignmentRow.organization_id == organization_id,
                WatcherStrategyAssignmentRow.user_id == user_id,
                WatcherStrategyAssignmentRow.id.not_in(wanted),
            )
        )
        for item, version, strategy in prepared:
            row = self.session.get(WatcherStrategyAssignmentRow, item.id, populate_existing=True)
            values = {
                "organization_id": organization_id,
                "user_id": user_id,
                "strategy_id": strategy.id,
                "strategy_version_id": version.id,
                "symbol": item.symbol,
                "version_content_hash": version.content_hash,
                "timeframes": list(item.timeframes),
                "parameters": dict(item.parameters),
                "monitoring_enabled": item.monitoring_enabled,
                "updated_at": now,
            }
            if row is None:
                self.session.add(WatcherStrategyAssignmentRow(id=item.id, created_at=now, **values))
            else:
                if (row.symbol, row.strategy_version_id) != (item.symbol, item.strategy_version_id):
                    raise ValidationAppError(
                        "An assignment identity cannot be rebound; create a new assignment."
                    )
                for key, value in values.items():
                    setattr(row, key, value)
        self.watchlists.replace(
            organization_id,
            [(s.symbol, s.enabled) for s in config.slots],
            expected_revision=body.revision,
        )
        self.session.flush()
        # Flush a marker before read so configured remains true for an empty first save.
        receipt = WatcherAssignmentReceiptRow(
            organization_id=organization_id,
            request_key=body.request_key,
            user_id=user_id,
            payload_hash=payload_hash,
            configuration_revision=body.revision + 1,
            response={},
            created_at=now,
        )
        self.session.add(receipt)
        self.session.flush()
        result = self.read(organization_id=organization_id, user_id=user_id)
        receipt.response = result.model_dump(mode="json")
        self.session.flush()
        return result


def assignment_scan_targets(
    session: Session, *, organization_id: UUID, symbols: tuple[str, ...], limit: int
) -> tuple[tuple[PaperScanTarget, ...], dict[str, list[dict[str, Any]]]]:
    """Exact pinned targets plus explicit bounded refusal diagnostics for one tenant."""
    from dataclasses import replace

    from app.workers.watcher_paper_targets import _target_from_executable

    service = WatcherAssignmentService(session)
    config = service.watchlists.load(organization_id)
    targets: list[PaperScanTarget] = []
    observations: dict[str, list[dict[str, Any]]] = {}
    now = datetime.now(UTC)
    rows = session.scalars(
        select(WatcherStrategyAssignmentRow)
        .where(WatcherStrategyAssignmentRow.organization_id == organization_id)
        .order_by(
            WatcherStrategyAssignmentRow.symbol,
            WatcherStrategyAssignmentRow.created_at,
            WatcherStrategyAssignmentRow.id,
        )
        .execution_options(populate_existing=True)
    )
    for row in rows:
        eligible, reason = service.eligibility(
            row, enabled_symbols=set(symbols) & set(config.enabled_symbols())
        )
        if eligible and len(targets) >= limit:
            eligible, reason = False, "cycle_capacity_reached"
        observations.setdefault(row.symbol, []).append(
            WatcherAssignmentObservation(
                assignment_id=row.id,
                configuration_revision=config.revision,
                observed_at=now,
                status="pending" if eligible else "unavailable",
                reason_code=reason,
            ).model_dump(mode="json")
        )
        if not eligible:
            continue
        version, strategy = service._version(organization_id, row.user_id, row.strategy_version_id)
        executable = resolve_executable_strategy_policy(
            session,
            organization_id=organization_id,
            user_id=row.user_id,
            strategy_version_id=version.id,
        )
        targets.append(
            replace(
                _target_from_executable(strategy, executable, symbol=row.symbol),
                assignment_id=row.id,
                configuration_revision=config.revision,
            )
        )
    return tuple(targets), observations


def assignment_target_reason(session: Session, target: PaperScanTarget) -> str | None:
    """Fresh authority check; retained ORM state cannot authorize a paused assignment."""
    service = WatcherAssignmentService(session)
    config = service.watchlists.load(target.organization_id)
    if config.revision != target.configuration_revision:
        return "monitoring_configuration_changed"
    if target.assignment_id is None:
        # Inferred coverage was not managed when loaded at this exact revision.
        # Every explicit replacement atomically advances that same revision, so
        # a first add/pause/remove cannot reuse a stale inferred target.
        if managed_assignment_coverage(
            session,
            target.organization_id,
            coverage=(target.user_id, target.strategy_id, target.symbol),
        ):
            return "monitoring_configuration_changed"
        if target.symbol not in config.enabled_symbols():
            return "coin_disabled"
        try:
            service._member(target.organization_id, target.user_id, write=True)
            version, strategy = service._version(
                target.organization_id, target.user_id, target.strategy_version_id
            )
        except (ForbiddenError, NotFoundError, ValidationAppError):
            return "strategy_binding_unavailable"
        if strategy.id != target.strategy_id or strategy.current_version != version.version:
            return "strategy_binding_unavailable"
        if not strategy.enabled:
            return "saved_strategy_disabled"
        try:
            executable = resolve_executable_strategy_policy(
                session,
                organization_id=target.organization_id,
                user_id=target.user_id,
                strategy_version_id=target.strategy_version_id,
            )
        except StrategyEvaluationPolicyError as exc:
            return exc.reason_code
        except NotFoundError:
            return "strategy_binding_unavailable"
        if (
            executable.compiled_setup_definition_id != target.compiled_setup_definition_id
            or executable.compiled_content_hash != target.compiled_content_hash
            or executable.authored_spec.symbol != target.symbol
            or executable.authored_spec.trigger_timeframe != target.timeframe
        ):
            return "strategy_binding_unavailable"
        return None
    row = session.get(WatcherStrategyAssignmentRow, target.assignment_id, populate_existing=True)
    if row is None or (row.organization_id, row.user_id, row.symbol, row.strategy_version_id) != (
        target.organization_id,
        target.user_id,
        target.symbol,
        target.strategy_version_id,
    ):
        return "strategy_binding_unavailable"
    eligible, reason = service.eligibility(row, enabled_symbols=set(config.enabled_symbols()))
    return None if eligible else reason


class AssignmentCandidateFence:
    """Serialize the final monitoring publish guard with configuration replacement.

    Provider reads happen before this short lock. Existing lease publication fences
    still own Candidate writes; this guard grants no execution authority.
    """

    def __init__(
        self, session: Session, target: PaperScanTarget, inner: CandidatePersistenceFence | None
    ) -> None:
        self.session, self.target, self.inner = session, target, inner

    def bind_from_evaluation_command(
        self, command: EvaluationCommand
    ) -> AbstractContextManager[None]:
        from contextlib import contextmanager, nullcontext

        from sqlalchemy.engine import Connection

        from app.watcher.errors import WatcherEvidenceUnavailableError

        @contextmanager
        def guarded() -> Iterator[None]:
            bind = self.session.get_bind()
            engine = bind.engine if isinstance(bind, Connection) else bind
            with Session(engine, expire_on_commit=False) as probe:
                from sqlalchemy import text

                from app.db.models import Organization

                # Research writes in the scan session hold FK KEY SHARE locks.
                # NO KEY UPDATE serializes with config writers without waiting on
                # our own uncommitted research inserts in that separate session.
                if engine.dialect.name == "postgresql":
                    probe.execute(text("SET LOCAL lock_timeout = '2000ms'"))
                probe.execute(
                    select(Organization.id)
                    .where(Organization.id == self.target.organization_id)
                    .with_for_update(key_share=True)
                ).one()
                reason = assignment_target_reason(probe, self.target)
                if reason is not None:
                    raise WatcherEvidenceUnavailableError(reason, reason_code=reason)
                with (
                    self.inner.bind_from_evaluation_command(command)
                    if self.inner is not None
                    else nullcontext()
                ):
                    yield

        return guarded()
