"""Backtest lifecycle service (AT-034 WS2 — queue/execute/cancel/verify).

Unit-of-work: services flush only; routes (or worker/background tasks) commit.
Paper/historical simulation only — never live execution.
"""

from __future__ import annotations

import time
import uuid
from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.errors import (
    ConflictError,
    IdempotencyConvergenceError,
    NotFoundError,
    ValidationAppError,
)
from app.db.models import BacktestDataset as BacktestDatasetModel
from app.db.models import BacktestRun as BacktestRunModel
from app.db.models import HistoricalCandle as HistoricalCandleModel
from app.providers.factory import resolve_market_data_provider
from app.repositories.backtest import BacktestRunRepository
from app.repositories.backtest_trades import BacktestTradeRepository
from app.repositories.strategy_library import UserStrategyRepository, UserStrategyVersionRepository
from app.schemas.audit import AuditRecordCreate
from app.schemas.backtest import (
    BacktestAssumptions,
    BacktestResult,
    BacktestRun,
    BacktestRunCreate,
    BacktestTradeRecord,
    BacktestVerifyResult,
    PaginatedBacktestTrades,
)
from app.schemas.common import (
    ActorType,
    AuditEventType,
    BacktestRecommendation,
    BacktestRunStatus,
    BacktestSplitLabel,
    PaperValidationStatus,
    TradeDirection,
)
from app.schemas.strategy_library import StrategyCard
from app.schemas.structured_rules import StructuredRules
from app.services.audit_service import AuditService
from app.services.backtest_dataset_service import BacktestDatasetService
from app.services.backtest_engine_service import ENGINE_VERSION, BacktestEngineService
from app.services.backtest_hashing import canonical_json_hash, dataset_content_hash
from app.services.historical_candle_service import HistoricalCandleService
from app.services.strategy_promotion import evaluate_promotion

_REQUEST_TAG = "backtest-api"
_IDEMPOTENCY_CONSTRAINT = "uq_backtest_runs_org_idempotency_key"
_MAX_CONVERGENCE_ATTEMPTS = 10
_INITIAL_BACKOFF = 0.025
_MAX_BACKOFF = 0.2


def _is_idempotency_unique_violation(exc: IntegrityError) -> bool:
    orig = getattr(exc, "orig", None)
    if orig is None:
        return False
    diag = getattr(orig, "diag", None)
    if diag is not None:
        constraint = getattr(diag, "constraint_name", None)
        if constraint == _IDEMPOTENCY_CONSTRAINT:
            return True
    message = str(orig).lower()
    return _IDEMPOTENCY_CONSTRAINT in message or "idempotency_key" in message


class BacktestService:
    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        audit_service: AuditService | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._audit = audit_service or AuditService(session)
        self._runs = BacktestRunRepository(session)
        self._trades = BacktestTradeRepository(session)
        self._strategies = UserStrategyRepository(session)
        self._versions = UserStrategyVersionRepository(session)
        provider = resolve_market_data_provider(self._settings)
        candle_svc = HistoricalCandleService(session, provider, self._settings)
        self._candles = candle_svc
        self._datasets = BacktestDatasetService(session, candle_svc)
        self._engine = BacktestEngineService(session, candle_svc, self._settings)

    # ------------------------------------------------------------------ #
    # Create / list / get
    # ------------------------------------------------------------------ #

    def create(
        self,
        strategy_id: uuid.UUID,
        payload: BacktestRunCreate,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        execute_inline: bool = True,
    ) -> BacktestRun:
        if payload.idempotency_key:
            existing = self._runs.get_by_idempotency_key(
                organization_id=organization_id, idempotency_key=payload.idempotency_key
            )
            if existing is not None:
                return self._to_schema(existing)

        strategy = self._strategies.get_scoped(
            strategy_id, organization_id=organization_id, user_id=user_id
        )
        if strategy is None:
            raise NotFoundError("Strategy not found.")

        if payload.strategy_version_id:
            version = self._versions.get_by_id(payload.strategy_version_id)
            # The strategy is already org/user scoped above; a version that does
            # not belong to it (another strategy or another org) must not leak.
            if version is None or version.strategy_id != strategy.id:
                raise NotFoundError("Strategy version not found.")
        else:
            version = self._versions.latest(strategy_id)
        if version is None or not version.card:
            raise ValidationAppError("Strategy version or card not found.")

        active = self._runs.count_active_for_org(organization_id)
        if active >= self._settings.backtest_max_active_runs_per_org:
            raise ConflictError(
                "Organization has too many active backtest runs.",
                code="backtest_active_limit",
                details={
                    "active": active,
                    "limit": self._settings.backtest_max_active_runs_per_org,
                },
            )

        assumptions = payload.assumptions or BacktestAssumptions()
        frozen_start = assumptions.start_date or (datetime.now(UTC) - timedelta(days=90)).date()
        frozen_end = assumptions.end_date or datetime.now(UTC).date()
        resolved_assumptions = assumptions.model_copy(
            update={"start_date": frozen_start, "end_date": frozen_end}
        )

        card = StrategyCard.model_validate(version.card)
        structured: StructuredRules | None = None
        if version.structured_rules:
            with suppress(Exception):
                structured = StructuredRules.model_validate(version.structured_rules)

        dataset, _rows, _limitations = self._datasets.ensure_dataset(
            symbol=resolved_assumptions.symbol,
            exchange=resolved_assumptions.exchange,
            timeframe=resolved_assumptions.timeframe,
            start_date=frozen_start,
            end_date=frozen_end,
        )

        config_snapshot = {
            "card": card.model_dump(mode="json"),
            "structured_rules": (
                structured.model_dump(mode="json") if structured is not None else None
            ),
            "setup_type": (
                strategy.setup_type.value
                if hasattr(strategy.setup_type, "value")
                else str(strategy.setup_type)
            ),
            "assumptions": resolved_assumptions.model_dump(mode="json"),
            "engine_version": ENGINE_VERSION,
            "dataset_hash": dataset.dataset_hash,
        }
        config_hash = canonical_json_hash(config_snapshot)

        run = BacktestRunModel(
            strategy_id=strategy_id,
            strategy_version_id=version.id,
            organization_id=organization_id,
            user_id=user_id,
            status=BacktestRunStatus.QUEUED,
            assumptions=resolved_assumptions.model_dump(mode="json"),
            config_snapshot=config_snapshot,
            config_hash=config_hash,
            dataset_id=dataset.id,
            engine_version=ENGINE_VERSION,
            idempotency_key=payload.idempotency_key,
            total_bars=dataset.candle_count,
        )

        try:
            self._persist_new_run(run)
        except IdempotencyConvergenceError:
            raise
        except ConflictError:
            # Concurrent winner committed — return that run unchanged.
            if payload.idempotency_key:
                winner = self._runs.get_by_idempotency_key(
                    organization_id=organization_id,
                    idempotency_key=payload.idempotency_key,
                )
                if winner is not None:
                    return self._to_schema(winner)
            raise

        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=AuditEventType.BACKTEST_RUN_CREATED,
                resource_type="backtest_run",
                resource_id=str(run.id),
                organization_id=organization_id,
                user_id=user_id,
                actor_type=ActorType.USER,
                metadata={
                    "strategy_id": str(strategy_id),
                    "total_bars": dataset.candle_count,
                    "sync": dataset.candle_count <= self._settings.backtest_sync_max_bars,
                    "has_idempotency_key": payload.idempotency_key is not None,
                },
            )
        )

        if execute_inline and dataset.candle_count <= self._settings.backtest_sync_max_bars:
            return self.execute_run(run.id, organization_id=organization_id)

        return self._to_schema(run)

    def _persist_new_run(self, run: BacktestRunModel) -> None:
        nested = self._session.begin_nested()
        try:
            self._runs.add(run)
            self._session.flush()
            nested.commit()
        except IntegrityError as exc:
            nested.rollback()
            if not _is_idempotency_unique_violation(exc) or not run.idempotency_key:
                raise
            converged = self._wait_for_idempotent_run(
                organization_id=run.organization_id,
                idempotency_key=run.idempotency_key,
            )
            if converged is None:
                raise IdempotencyConvergenceError(
                    "Could not converge on concurrent backtest idempotency key.",
                    details={"idempotency_key": run.idempotency_key},
                ) from exc
            raise ConflictError(
                "Backtest run already exists for idempotency key.",
                details={"run_id": str(converged.id)},
            ) from exc

    def _wait_for_idempotent_run(
        self,
        *,
        organization_id: uuid.UUID,
        idempotency_key: str,
    ) -> BacktestRunModel | None:
        existing = self._runs.get_by_idempotency_key(
            organization_id=organization_id, idempotency_key=idempotency_key
        )
        if existing is not None:
            return existing
        bind = self._session.get_bind()
        factory = sessionmaker(bind=bind, expire_on_commit=False)
        backoff = _INITIAL_BACKOFF
        for attempt in range(_MAX_CONVERGENCE_ATTEMPTS):
            with factory() as probe:
                row = BacktestRunRepository(probe).get_by_idempotency_key(
                    organization_id=organization_id, idempotency_key=idempotency_key
                )
                if row is not None:
                    return row
            if attempt + 1 >= _MAX_CONVERGENCE_ATTEMPTS:
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF)
        return None

    def list_for_strategy(
        self,
        strategy_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[BacktestRun], int]:
        strategy = self._strategies.get_scoped(
            strategy_id, organization_id=organization_id, user_id=user_id
        )
        if strategy is None:
            raise NotFoundError("Strategy not found.")
        rows, total = self._runs.list_for_strategy(
            strategy_id, organization_id=organization_id, limit=limit, offset=offset
        )
        return [self._to_schema(row) for row in rows], total

    def get(
        self,
        run_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
    ) -> BacktestRun:
        row = self._runs.get_scoped(run_id, organization_id=organization_id)
        if row is None:
            raise NotFoundError("Backtest run not found.")
        return self._to_schema(row)

    def list_trades(
        self,
        run_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        limit: int = 200,
        offset: int = 0,
    ) -> PaginatedBacktestTrades:
        row = self._runs.get_scoped(run_id, organization_id=organization_id)
        if row is None:
            raise NotFoundError("Backtest run not found.")
        rows, total = self._trades.list_for_run(run_id, limit=limit, offset=offset)
        items = [
            BacktestTradeRecord(
                id=trade.id,
                entry_time=trade.entry_time,
                exit_time=trade.exit_time,
                direction=TradeDirection(trade.direction),
                entry_price=trade.entry_price,
                exit_price=trade.exit_price,
                stop_loss=trade.stop_loss,
                size=trade.size,
                fees=trade.fees,
                slippage_cost=trade.slippage_cost,
                gross_pnl=trade.gross_pnl,
                net_pnl=trade.net_pnl,
                tp_hit_status=trade.tp_hit_status,
                exit_reason=trade.exit_reason,
                rule_notes=trade.rule_notes,
                mfe_price=trade.mfe_price,
                mae_price=trade.mae_price,
                mfe_amount=trade.mfe_amount,
                mae_amount=trade.mae_amount,
                available_profit=trade.available_profit,
                capture_pct=trade.capture_pct,
                funding_cost=trade.funding_cost or Decimal("0"),
                split_label=(
                    BacktestSplitLabel(trade.split_label)
                    if trade.split_label
                    else BacktestSplitLabel.IN_SAMPLE
                ),
                split_index=trade.split_index if trade.split_index is not None else 0,
                sequence=trade.sequence,
            )
            for trade in rows
        ]
        if row.result and row.result.get("replay"):
            extras = {t["sequence"]: t for t in row.result.get("trades", [])}
            items = [
                BacktestTradeRecord.model_validate(
                    {
                        **item.model_dump(),
                        **{
                            field: extras.get(item.sequence, {}).get(field)
                            for field in (
                                "planned_targets",
                                "r_result",
                                "holding_bars",
                                "holding_seconds",
                                "setup_id",
                            )
                        },
                    }
                )
                for item in items
            ]
        return PaginatedBacktestTrades(items=items, total=total, limit=limit, offset=offset)

    # ------------------------------------------------------------------ #
    # Execute / cancel / verify
    # ------------------------------------------------------------------ #

    def execute_run(self, run_id: uuid.UUID, organization_id: uuid.UUID) -> BacktestRun:
        run = self._runs.get_scoped(run_id, organization_id=organization_id)
        if run is None:
            raise NotFoundError("Backtest run not found.")

        if run.status in (
            BacktestRunStatus.COMPLETED,
            BacktestRunStatus.FAILED,
            BacktestRunStatus.CANCELLED,
        ):
            return self._to_schema(run)

        if run.status == BacktestRunStatus.RUNNING:
            # Another worker owns this run; return current snapshot.
            return self._to_schema(run)

        if run.status != BacktestRunStatus.QUEUED:
            raise ConflictError(
                f"Backtest run cannot be executed from status {run.status.value}.",
                code="backtest_invalid_status",
            )

        claimed = self._runs.claim_queued(run_id)
        if claimed is None:
            refreshed = self._runs.get_scoped(run_id, organization_id=organization_id)
            if refreshed is None:
                raise NotFoundError("Backtest run not found.")
            return self._to_schema(refreshed)
        run = claimed

        try:
            from app.schemas.strategy_replay import REPLAY_ENGINE
            from app.services.strategy_replay_service import StrategyReplayService

            if run.engine_version == REPLAY_ENGINE:
                result = StrategyReplayService(self._session, self).replay(run, persist=True)
            else:
                result = self._execute_legacy_engine(run)
            now = datetime.now(UTC)
            run.finished_at = now
            if result.cancelled or self._should_cancel(run_id):
                run.status = BacktestRunStatus.CANCELLED
                run.result = result.model_dump(mode="json")
                self._audit_lifecycle(
                    run,
                    AuditEventType.BACKTEST_RUN_CANCELLED,
                    {"via": "execute_cancel"},
                )
            else:
                run.status = BacktestRunStatus.COMPLETED
                run.result = result.model_dump(mode="json")
                if run.engine_version != REPLAY_ENGINE:
                    self._apply_promotion(run, result)
                self._audit_lifecycle(
                    run,
                    AuditEventType.BACKTEST_RUN_COMPLETED,
                    {"trade_count": len(result.trades)},
                )
        except Exception as exc:
            run.status = BacktestRunStatus.FAILED
            run.error_message = str(exc)[:2000]
            run.finished_at = datetime.now(UTC)
            self._audit_lifecycle(
                run,
                AuditEventType.BACKTEST_RUN_FAILED,
                {"error_type": type(exc).__name__},
            )

        return self._to_schema(run)

    def _execute_legacy_engine(self, run: BacktestRunModel) -> BacktestResult:
        card, setup_type, structured, start_date, end_date = self._resolve_frozen(run)
        return self._engine.run(
            run=run,
            card=card,
            setup_type=setup_type,
            structured_rules=structured,
            start_date=start_date,
            end_date=end_date,
            should_cancel=lambda: self._should_cancel(run.id),
            persist=True,
        )

    def cancel(
        self,
        run_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BacktestRun:
        run = self._runs.get_scoped(run_id, organization_id=organization_id)
        if run is None:
            raise NotFoundError("Backtest run not found.")

        now = datetime.now(UTC)
        if run.status == BacktestRunStatus.QUEUED:
            run.status = BacktestRunStatus.CANCELLED
            run.finished_at = now
            run.cancel_requested_at = now
        elif run.status == BacktestRunStatus.RUNNING:
            run.status = BacktestRunStatus.CANCEL_REQUESTED
            run.cancel_requested_at = now
        elif run.status == BacktestRunStatus.CANCEL_REQUESTED:
            return self._to_schema(run)
        else:
            raise ConflictError(
                f"Cannot cancel backtest in status {run.status.value}.",
                code="backtest_cancel_invalid",
            )

        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=AuditEventType.BACKTEST_RUN_CANCELLED,
                resource_type="backtest_run",
                resource_id=str(run.id),
                organization_id=organization_id,
                user_id=user_id,
                actor_type=ActorType.USER,
                metadata={"status": run.status.value},
            )
        )
        self._session.flush()
        return self._to_schema(run)

    def verify(
        self,
        run_id: uuid.UUID,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BacktestVerifyResult:
        run = self._runs.get_scoped(run_id, organization_id=organization_id)
        if run is None:
            raise NotFoundError("Backtest run not found.")
        if run.status != BacktestRunStatus.COMPLETED:
            raise ConflictError(
                "Only completed backtest runs can be verified.",
                code="backtest_verify_invalid",
            )
        if not run.config_snapshot or run.dataset_id is None:
            raise ValidationAppError("Backtest run lacks a frozen config/dataset snapshot.")

        from app.schemas.strategy_replay import REPLAY_ENGINE
        from app.services.strategy_replay_service import StrategyReplayService

        if run.engine_version == REPLAY_ENGINE:
            try:
                recomputed = StrategyReplayService(self._session, self).replay(run, persist=False)
            except ValidationAppError as exc:
                self._audit_verify(run, user_id=user_id, match=False, dataset_ok=False)
                return BacktestVerifyResult(
                    run_id=run.id,
                    result_hash_stored=run.result_hash,
                    match=False,
                    dataset_ok=False,
                    detail=str(exc),
                )
            try:
                stored_result = BacktestResult.model_validate(run.result)
            except ValueError:
                self._audit_verify(run, user_id=user_id, match=False, dataset_ok=True)
                return BacktestVerifyResult(
                    run_id=run.id,
                    result_hash_stored=run.result_hash,
                    result_hash_recomputed=recomputed.result_hash,
                    match=False,
                    dataset_ok=True,
                    detail="stored_replay_result_invalid",
                )
            stored_content_hash = canonical_json_hash(
                stored_result.model_dump(mode="json", exclude={"result_hash"})
            )
            match = (
                recomputed.result_hash == run.result_hash == stored_result.result_hash
                and stored_content_hash == run.result_hash
            )
            self._audit_verify(run, user_id=user_id, match=match, dataset_ok=True)
            return BacktestVerifyResult(
                run_id=run.id,
                result_hash_stored=run.result_hash,
                result_hash_recomputed=recomputed.result_hash,
                match=match,
                dataset_ok=True,
                detail=None if match else "result_hash_mismatch",
            )

        dataset = self._session.get(BacktestDatasetModel, run.dataset_id)
        if dataset is None:
            raise ValidationAppError("Stored backtest dataset is missing.")

        candle_rows = self._load_dataset_candles(dataset)
        recomputed_dataset_hash = dataset_content_hash(candle_rows)
        dataset_ok = recomputed_dataset_hash == dataset.dataset_hash
        stored_hash = run.result_hash

        if not dataset_ok:
            result = BacktestVerifyResult(
                run_id=run.id,
                result_hash_stored=stored_hash,
                result_hash_recomputed=None,
                match=False,
                dataset_ok=False,
                detail="dataset_mismatch",
            )
            self._audit_verify(run, user_id=user_id, match=False, dataset_ok=False)
            return result

        card, setup_type, structured, start_date, end_date = self._resolve_frozen(run)
        recomputed = self._engine.run(
            run=run,
            card=card,
            setup_type=setup_type,
            structured_rules=structured,
            start_date=start_date,
            end_date=end_date,
            persist=False,
        )
        recomputed_hash = recomputed.result_hash
        match = bool(stored_hash and recomputed_hash and stored_hash == recomputed_hash)
        result = BacktestVerifyResult(
            run_id=run.id,
            result_hash_stored=stored_hash,
            result_hash_recomputed=recomputed_hash,
            match=match,
            dataset_ok=True,
            detail=None if match else "result_hash_mismatch",
        )
        self._audit_verify(run, user_id=user_id, match=match, dataset_ok=True)
        return result

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    def _should_cancel(self, run_id: uuid.UUID) -> bool:
        """Read cancel flags via AUTOCOMMIT so peer commits are visible.

        Must not open a second ORM session on the same StaticPool connection —
        that shares the DBAPI connection and can roll back the caller's unit of
        work (SQLite in-memory tests).
        """
        from sqlalchemy import select
        from sqlalchemy.engine import Engine

        bind = self._session.get_bind()
        if not isinstance(bind, Engine):
            # Fallback: same-session read (peer commits may not be visible).
            probed = self._runs.cancel_probe(run_id)
            if probed is None:
                return True
            status, cancel_at = probed
            return cancel_at is not None or status in (
                BacktestRunStatus.CANCEL_REQUESTED,
                BacktestRunStatus.CANCELLED,
            )
        with bind.connect() as conn:
            autocommit = conn.execution_options(isolation_level="AUTOCOMMIT")
            probed_row = autocommit.execute(
                select(BacktestRunModel.status, BacktestRunModel.cancel_requested_at).where(
                    BacktestRunModel.id == run_id
                )
            ).one_or_none()
            if probed_row is None:
                return True
            status, cancel_at = probed_row[0], probed_row[1]
            return cancel_at is not None or status in (
                BacktestRunStatus.CANCEL_REQUESTED,
                BacktestRunStatus.CANCELLED,
            )

    def _resolve_frozen(
        self, run: BacktestRunModel
    ) -> tuple[StrategyCard, object, StructuredRules | None, date | None, date | None]:
        snapshot = run.config_snapshot or {}
        card = StrategyCard.model_validate(snapshot.get("card") or {})
        setup_type = snapshot.get("setup_type")
        structured: StructuredRules | None = None
        raw_rules = snapshot.get("structured_rules")
        if raw_rules:
            with suppress(Exception):
                structured = StructuredRules.model_validate(raw_rules)
        assumptions = BacktestAssumptions.model_validate(
            snapshot.get("assumptions") or run.assumptions or {}
        )
        return card, setup_type, structured, assumptions.start_date, assumptions.end_date

    def _apply_promotion(self, run: BacktestRunModel, result: BacktestResult) -> None:
        if run.strategy_version_id is None or result.cancelled or result.metrics is None:
            return
        version = self._versions.get_by_id(run.strategy_version_id)
        if version is None:
            return
        needs_rules = BacktestRecommendation.NEEDS_STRUCTURED_RULES
        if result.recommendation == needs_rules:
            return
        promotion = evaluate_promotion(
            metrics=result.metrics,
            machine_readable=True,
            data_quality=result.data_quality,
            meets_success_criteria=result.meets_success_criteria,
        )
        version.backtest_status = promotion.backtest_status
        if promotion.validation_status is not None:
            version.validation_status = promotion.validation_status
        from app.services.paper_eligibility_service import PaperEligibilityService

        PaperEligibilityService(self._session, self._settings).refresh_strategy_flag(
            run.strategy_id,
            organization_id=run.organization_id,
            user_id=run.user_id,
        )
        strategy = self._strategies.get_scoped(
            run.strategy_id, organization_id=run.organization_id, user_id=run.user_id
        )
        if strategy is not None and strategy.paper_eligible:
            version.paper_validation_status = PaperValidationStatus.NOT_STARTED

    def _load_dataset_candles(self, dataset: BacktestDatasetModel) -> list[HistoricalCandleModel]:
        from sqlalchemy import select

        start_dt = datetime.combine(dataset.start_date, datetime.min.time(), tzinfo=UTC)
        end_dt = datetime.combine(dataset.end_date, datetime.max.time(), tzinfo=UTC)
        return list(
            self._session.scalars(
                select(HistoricalCandleModel)
                .where(
                    HistoricalCandleModel.symbol == dataset.symbol,
                    HistoricalCandleModel.exchange == dataset.exchange,
                    HistoricalCandleModel.timeframe == dataset.timeframe,
                    HistoricalCandleModel.open_time >= start_dt,
                    HistoricalCandleModel.open_time <= end_dt,
                )
                .order_by(HistoricalCandleModel.open_time.asc())
                .execution_options(populate_existing=True)
            ).all()
        )

    def _audit_lifecycle(
        self,
        run: BacktestRunModel,
        event_type: AuditEventType,
        metadata: dict[str, object],
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=event_type,
                resource_type="backtest_run",
                resource_id=str(run.id),
                organization_id=run.organization_id,
                user_id=run.user_id,
                actor_type=ActorType.SYSTEM,
                metadata=metadata,
            )
        )

    def _audit_verify(
        self,
        run: BacktestRunModel,
        *,
        user_id: uuid.UUID,
        match: bool,
        dataset_ok: bool,
    ) -> None:
        self._audit.record(
            AuditRecordCreate(
                request_id=_REQUEST_TAG,
                trace_id=_REQUEST_TAG,
                event_type=AuditEventType.BACKTEST_RUN_VERIFIED,
                resource_type="backtest_run",
                resource_id=str(run.id),
                organization_id=run.organization_id,
                user_id=user_id,
                actor_type=ActorType.USER,
                metadata={"match": match, "dataset_ok": dataset_ok},
            )
        )

    @staticmethod
    def _to_schema(row: BacktestRunModel) -> BacktestRun:
        assumptions = BacktestAssumptions.model_validate(row.assumptions or {})
        result = None
        if row.result:
            result = BacktestResult.model_validate(row.result)
        return BacktestRun(
            id=row.id,
            strategy_id=row.strategy_id,
            strategy_version_id=row.strategy_version_id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            status=row.status,
            assumptions=assumptions,
            result=result,
            error_message=row.error_message,
            config_hash=row.config_hash,
            dataset_id=row.dataset_id,
            engine_version=row.engine_version,
            result_hash=row.result_hash,
            idempotency_key=row.idempotency_key,
            replay_config=row.config_snapshot
            if (row.config_snapshot or {}).get("replay_request")
            else None,
            started_at=row.started_at,
            finished_at=row.finished_at,
            cancel_requested_at=row.cancel_requested_at,
            processed_bars=row.processed_bars,
            total_bars=row.total_bars,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
