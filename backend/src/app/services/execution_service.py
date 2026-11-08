"""Paper execution only. Real exchange trading is blocked by default.

Phase 1 entry execution is exclusively ``EXECUTE_PAPER_PLAN``. The legacy
``place_paper_order`` mutation path and its demo-venue mirror are fail-closed
and cannot create orders, positions, or venue calls. Read-only order APIs remain.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

import structlog
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import NotFoundError, TradingPolicyError
from app.core.operation_policy import PersistenceKind, assert_write_allowed
from app.core.paper_safety import assert_execution_capable_composition_root
from app.db.models import (
    ExchangeOrder,
    ExecutionCommand,
    Order,
    TradeProposal,
    VenueSubmitEffect,
)
from app.providers.exchange.base import (
    ExchangeExecutionProvider,
    ExchangeOrderResult,
)
from app.providers.execution.fake_venue import FakeVenueSubmitProvider
from app.repositories.approvals import ApprovalRepository
from app.repositories.exchange_orders import ExchangeFillRepository, ExchangeOrderRepository
from app.repositories.orders import OrderRepository
from app.repositories.proposals import ProposalRepository
from app.repositories.trade_plans import TradePlanRevisionRepository
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.audit import AuditRecordCreate
from app.schemas.common import ActorType, AuditEventType
from app.schemas.execution import PaperOrder, PaperOrderPlacementResult, PaperOrderRequest
from app.schemas.execution_protocol import (
    ClosePaperPlanRequest,
    ClosePaperPlanResult,
    ExecutePaperPlanRequest,
    ExecutePaperPlanResult,
    UniqueFillResult,
)
from app.services.audit_service import AuditService
from app.services.canonical_paper_execution import (
    CanonicalPaperExecutionService,
    is_canonical_plan_authority,
)
from app.services.execution_claim import ExecutionClaimHooks
from app.services.market_data_service import MarketDataService
from app.services.paper_execution_risk_gate import BoundPaperPlacement, PaperExecutionRiskGate
from app.services.risk.daily_risk_accounting import DailyRiskAccounting
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk.settings_service import RiskSettingsService
from app.services.risk_service import RiskService
from app.services.venue_submit_dispatcher import VenueSubmitDispatcher

logger = structlog.get_logger(__name__)

LEGACY_PAPER_EXECUTION_REASON = "legacy_paper_execution_disabled"
LEGACY_DEMO_MIRROR_REASON = "legacy_demo_mirror_disabled"


class ExecutionService:
    def __init__(
        self,
        session: Session,
        settings: Settings,
        audit_service: AuditService,
        *,
        exchange_execution: ExchangeExecutionProvider | None = None,
        risk_service: RiskService | None = None,
        risk_settings: RiskSettingsService | None = None,
        market_data_service: MarketDataService | None = None,
        kill_switch: KillSwitchService | None = None,
        canonical_runtime: ProductionCanonicalRuntime | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._orders = OrderRepository(session)
        self._proposals = ProposalRepository(session)
        self._revisions = TradePlanRevisionRepository(session)
        self._approvals = ApprovalRepository(session)
        self._exchange_orders = ExchangeOrderRepository(session)
        self._exchange_fills = ExchangeFillRepository(session)
        self._exchange_execution = exchange_execution
        self._audit = audit_service
        self._risk_service = risk_service or RiskService()
        self._risk_settings = risk_settings or RiskSettingsService(session, audit_service)
        self._market_data = market_data_service
        self._kill_switch = kill_switch or KillSwitchService(session, audit_service, settings)
        self._daily_risk = DailyRiskAccounting(session, self._risk_settings)
        self._risk_gate = PaperExecutionRiskGate(
            risk_service=self._risk_service,
            daily_risk=self._daily_risk,
            kill_switch=self._kill_switch,
        )
        self._canonical_runtime = canonical_runtime
        assert_execution_capable_composition_root(settings)

    def place_paper_order(self, request: PaperOrderRequest) -> PaperOrderPlacementResult:
        assert_execution_capable_composition_root(self._settings)
        assert_write_allowed(PersistenceKind.EXECUTION)
        raise TradingPolicyError(
            "Phase 1 paper entry execution requires EXECUTE_PAPER_PLAN.",
            details={"reason": LEGACY_PAPER_EXECUTION_REASON},
        )

    def _create_or_converge_paper_order(
        self,
        *,
        request: PaperOrderRequest,
        proposal: TradeProposal,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        bound: BoundPaperPlacement,
    ) -> PaperOrderPlacementResult:
        """Unreachable Phase 1 entry path. Kept to fail closed if called."""

        del request, proposal, organization_id, user_id, bound
        raise TradingPolicyError(
            "Phase 1 paper entry execution requires EXECUTE_PAPER_PLAN.",
            details={"reason": LEGACY_PAPER_EXECUTION_REASON},
        )

    def _persist_new_paper_order(
        self,
        *,
        request: PaperOrderRequest,
        proposal: TradeProposal,
        organization_id: uuid.UUID,
        user_id: uuid.UUID,
        bound: BoundPaperPlacement,
        mode_tag: str,
    ) -> Order:
        """Unreachable Phase 1 mutation. Kept to fail closed if called."""

        del request, proposal, organization_id, user_id, bound, mode_tag
        raise TradingPolicyError(
            "Phase 1 paper entry execution requires EXECUTE_PAPER_PLAN.",
            details={"reason": LEGACY_PAPER_EXECUTION_REASON},
        )

    def get_order(self, order_id: uuid.UUID) -> PaperOrder:
        row = self._orders.get(order_id)
        if row is None:
            raise NotFoundError("Order not found")
        return self._to_schema(row)

    def list_orders(
        self,
        *,
        organization_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[PaperOrder], int]:
        rows, total = self._orders.list_orders(
            organization_id=organization_id,
            user_id=user_id,
            limit=limit,
            offset=offset,
        )
        return [self._to_schema(row) for row in rows], total

    def _demo_routing_enabled(self) -> bool:
        """Phase 1 never routes the legacy paper path to a demo venue."""

        return False

    def _assert_market_data_usable(self, symbol: str, *, request: PaperOrderRequest) -> None:
        """Fail closed on stale/degraded market data when live data is expected.

        Mock / intentional-fallback provider modes skip this gate so paper tests and
        local mock runs remain usable. When ``provider_mode`` or market provider is
        configured for live/fallback-to-live, stale or fallback tickers refuse placement.
        """
        if self._market_data is None:
            return
        provider_mode = (self._settings.provider_mode or "").lower()
        market_provider = (self._settings.market_data_provider or "").lower()
        if provider_mode == "mock" or market_provider == "mock":
            return

        try:
            ticker = self._market_data.get_ticker(symbol)
        except Exception:
            self._audit_reject(request, reason="market_data_unavailable")
            raise TradingPolicyError(
                "Market data is unavailable; paper execution refused.",
                details={"reason": "market_data_unavailable", "symbol": symbol},
            ) from None

        meta = ticker.meta
        if meta.is_stale or meta.fallback_used:
            self._audit_reject(
                request,
                reason="market_data_degraded",
                extra={
                    "is_stale": str(meta.is_stale),
                    "fallback_used": str(meta.fallback_used),
                },
            )
            raise TradingPolicyError(
                "Market data is stale or degraded; paper execution refused.",
                details={
                    "reason": "market_data_degraded",
                    "symbol": symbol,
                    "is_stale": str(meta.is_stale),
                    "fallback_used": str(meta.fallback_used),
                },
            )

    def _mirror_to_demo_venue(
        self,
        *,
        request: PaperOrderRequest,
        order: Order,
        bound: BoundPaperPlacement,
    ) -> None:
        """Legacy demo mirror is not an alternate Phase 1 venue path."""

        del request, order, bound
        raise TradingPolicyError(
            "Phase 1 forbids the legacy demo-venue mirror path.",
            details={"reason": LEGACY_DEMO_MIRROR_REASON},
        )

    def _persist_demo_result(
        self, *, exchange_order: ExchangeOrder, result: ExchangeOrderResult
    ) -> None:
        del exchange_order, result
        raise TradingPolicyError(
            "Phase 1 forbids the legacy demo-venue mirror path.",
            details={"reason": LEGACY_DEMO_MIRROR_REASON},
        )

    def _create_or_update_position(
        self,
        *,
        proposal: TradeProposal,
        order: Order,
        bound: BoundPaperPlacement,
    ) -> None:
        del proposal, order, bound
        raise TradingPolicyError(
            "Phase 1 paper entry execution requires EXECUTE_PAPER_PLAN.",
            details={"reason": LEGACY_PAPER_EXECUTION_REASON},
        )

    def execute_paper_plan(
        self,
        request: ExecutePaperPlanRequest,
        *,
        hooks: ExecutionClaimHooks | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> ExecutePaperPlanResult:
        """Claim-time EXECUTE_PAPER_PLAN entry. Does not call any venue network."""

        from app.services.execution_claim import PaperPlanClaimService
        from app.services.safety_epoch import SafetyEpochService

        safety = SafetyEpochService(self._session, self._settings, self._risk_settings)
        resolved_clock = clock or (lambda: datetime.now(UTC))
        plan_row = self._revisions.get_scoped(
            request.revision_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        if plan_row is not None and plan_row.plan_authority == "manual_demo_test":
            raise TradingPolicyError("Manual demo requires its explicit preview/confirmation API.")
        if plan_row is not None and is_canonical_plan_authority(plan_row.plan_authority):
            if self._canonical_runtime is None:
                raise TradingPolicyError(
                    "Canonical paper execution requires the production canonical runtime.",
                    details={"reason": "canonical_runtime_unbound"},
                )
            return CanonicalPaperExecutionService(
                self._session,
                self._settings,
                self._audit,
                self._canonical_runtime,
                safety_epochs=safety,
                clock=resolved_clock,
                hooks=hooks,
            ).execute(request)
        return PaperPlanClaimService(
            self._session,
            self._settings,
            safety,
            clock=resolved_clock,
            hooks=hooks,
        ).claim(request)

    def lease_paper_plan_effect(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        lease_seconds: int = 30,
    ) -> VenueSubmitEffect:
        return self._dispatcher().lease_effect(
            command_id=command_id, owner=owner, lease_seconds=lease_seconds
        )

    def authorize_paper_plan_dispatch(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        fencing_token: int,
    ) -> VenueSubmitEffect:
        return self._dispatcher().authorize_dispatch(
            command_id=command_id, owner=owner, fencing_token=fencing_token
        )

    def attempt_fake_paper_plan_send(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        fencing_token: int,
        provider: FakeVenueSubmitProvider,
    ) -> VenueSubmitEffect:
        return self._dispatcher().attempt_fake_send(
            command_id=command_id,
            owner=owner,
            fencing_token=fencing_token,
            provider=provider,
        )

    def recover_paper_plan_effect(self, *, command_id: uuid.UUID, owner: str) -> VenueSubmitEffect:
        return self._dispatcher().recover_after_crash(command_id=command_id, owner=owner)

    def apply_paper_plan_fill(
        self,
        *,
        command_id: uuid.UUID,
        fill_quantity: Decimal,
        fill_price: Decimal,
        source_identity: str,
        occurred_at: datetime,
        venue_source: str = "phase1-fake-venue",
    ) -> UniqueFillResult:
        command = self._session.get(ExecutionCommand, command_id)
        if command is not None:
            revision = self._revisions.get_scoped(
                command.revision_id,
                organization_id=command.organization_id,
                user_id=command.user_id,
            )
            if (
                revision is not None
                and revision.execution_policy_version == "governed-blofin-demo/v1"
            ):
                raise TradingPolicyError(
                    "Demo fills require governed venue reconciliation.",
                    details={"reason": "demo_synthetic_fill_forbidden"},
                )
        fill = self._dispatcher().apply_unique_fill(
            command_id=command_id,
            fill_quantity=fill_quantity,
            fill_price=fill_price,
            source_identity=source_identity,
            venue_source=venue_source,
            occurred_at=occurred_at,
        )
        self._project_canonical_fill(command_id, fill)
        return fill

    def close_canonical_paper_plan(self, request: ClosePaperPlanRequest) -> ClosePaperPlanResult:
        """Record a paper close for a filled canonical plan. Does not call an exchange."""

        assert_write_allowed(PersistenceKind.JOURNAL)
        if self._canonical_runtime is None:
            raise TradingPolicyError(
                "Canonical paper close requires the production canonical runtime.",
                details={"reason": "canonical_runtime_unbound"},
            )
        plan_row = self._revisions.get_scoped(
            request.revision_id,
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
        if plan_row is None:
            raise NotFoundError("Trade plan revision is unknown; refusing paper close.")
        if not is_canonical_plan_authority(plan_row.plan_authority):
            raise TradingPolicyError(
                "Paper close statistics are recorded only for canonical paper plans.",
                details={"reason": "canonical_plan_required"},
            )
        from app.services.safety_epoch import SafetyEpochService

        return CanonicalPaperExecutionService(
            self._session,
            self._settings,
            self._audit,
            self._canonical_runtime,
            safety_epochs=SafetyEpochService(self._session, self._settings, self._risk_settings),
            clock=lambda: datetime.now(UTC),
        ).close_filled_plan(request)

    def _project_canonical_fill(self, command_id: uuid.UUID, fill: UniqueFillResult) -> None:
        command = self._session.get(ExecutionCommand, command_id)
        if command is None:
            raise NotFoundError("Execution command is unknown; refusing fill journal projection.")
        plan_row = self._revisions.get_scoped(
            command.revision_id,
            organization_id=command.organization_id,
            user_id=command.user_id,
        )
        if plan_row is None:
            raise NotFoundError("Trade plan revision is unknown; refusing fill journal projection.")
        if not is_canonical_plan_authority(plan_row.plan_authority):
            return
        if self._canonical_runtime is None:
            raise TradingPolicyError(
                "Canonical paper fill projection requires the production canonical runtime.",
                details={"reason": "canonical_runtime_unbound"},
            )
        from app.services.safety_epoch import SafetyEpochService

        CanonicalPaperExecutionService(
            self._session,
            self._settings,
            self._audit,
            self._canonical_runtime,
            safety_epochs=SafetyEpochService(self._session, self._settings, self._risk_settings),
            clock=lambda: datetime.now(UTC),
        ).project_fill(
            organization_id=command.organization_id,
            user_id=command.user_id,
            account_id=command.account_id,
            command_id=command.id,
            fill=fill,
            revision_id=command.revision_id,
        )

    def _dispatcher(self) -> VenueSubmitDispatcher:
        from app.services.safety_epoch import SafetyEpochService

        safety = SafetyEpochService(self._session, self._settings, self._risk_settings)
        return VenueSubmitDispatcher(self._session, safety)

    def _audit_reject(
        self,
        request: PaperOrderRequest,
        *,
        reason: str,
        extra: dict[str, str] | None = None,
    ) -> None:
        metadata: dict[str, object] = {"reason": reason, "mode": "paper"}
        if extra:
            metadata.update(extra)
        # Durable outside the business UoW — reject paths raise before route commit.
        self._audit.record_durable_isolated(
            AuditRecordCreate(
                request_id=request.idempotency_key,
                trace_id=request.idempotency_key,
                event_type=AuditEventType.PAPER_ORDER_REJECTED,
                resource_type="paper_order",
                resource_id=str(request.proposal_id),
                actor_type=ActorType.SYSTEM,
                metadata=metadata,
            )
        )

    @staticmethod
    def _to_schema(row: Order) -> PaperOrder:
        return PaperOrder(
            id=row.id,
            organization_id=row.organization_id,
            user_id=row.user_id,
            strategy_id=row.strategy_id,
            proposal_id=row.proposal_id,
            approval_id=row.approval_id,
            mode=row.mode,
            symbol=row.symbol,
            side=row.side,
            type=row.order_type,
            size=row.size,
            price=row.price,
            status=row.status,
            reduce_only=row.reduce_only,
            idempotency_key=row.idempotency_key,
            exchange_order_id=row.exchange_order_id,
            created_at=row.created_at or datetime.now(UTC),
        )
