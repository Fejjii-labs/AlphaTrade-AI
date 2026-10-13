"""Canonical confirmed Candidate → authorized, durable BloFin demo execution.

Uses the existing eligibility, risk, plan, approval, reservation, dispatch fence,
immutable fill, Journal and learning authorities. The separate staging arm and
principal pins never grant real execution. SFP keeps its existing refusal.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import ExchangeMode, ExecutionMode, Settings
from app.core.errors import ConflictError
from app.core.execution_credentials import (
    blofin_execution_authorized,
    governed_demo_worker_access_requested,
)
from app.db.models import (
    ExecutionAccount,
    ExecutionCommand,
    ExecutionProjection,
    GovernedDemoLifecycleResolution,
    JournalTrade,
    TradePlanRevision,
    VenueSubmitEffect,
)
from app.evidence_pipeline.types import AssembledCanonicalEvidence
from app.experiments.governed_context import GovernedExperimentContext
from app.experiments.native_producer import BloFinNativeOutcomeProducer
from app.market_contracts.enums import VenueId
from app.providers.exchange.blofin_activity import BloFinActivityProvider
from app.providers.exchange.demo_preflight import failure_diagnostics
from app.providers.exchange.factory import build_blofin_client
from app.providers.exchange.governed_blofin import DemoVenueSnapshot, GovernedBloFinDemoProvider
from app.runtime.canonical import ProductionCanonicalRuntime
from app.schemas.approval import ApprovalDecisionRequest
from app.schemas.common import ApprovalAction, TradeDirection
from app.schemas.execution_protocol import ExecutionCommandOutcome, VenueSubmitEffectState
from app.schemas.risk import KillSwitchMutationRequest
from app.schemas.trade_plan import AuthorizationChannel, EntrySide, TradePlanRevisionCreate
from app.security.tenant import TenantContext
from app.services.approval_service import ApprovalService
from app.services.audit_service import AuditService
from app.services.automated_paper_loop import (
    AutomatedPaperLoop,
    AutomatedPaperLoopProof,
    _eligibility_command,
    _execute_request,
    _fill_id,
    _plan_terms,
    _proof,
)
from app.services.canonical_paper_execution import CanonicalPaperExecutionService
from app.services.canonical_serialization import canonical_sha256
from app.services.demo_account_history import has_demo_entry_history
from app.services.demo_lifecycle_resolution import resolve_verified_demo_exit
from app.services.execution_service import ExecutionService
from app.services.planned_reward_risk import PlannedRewardRiskError, planned_reward_risk
from app.services.risk.kill_switch import KillSwitchService
from app.services.risk.settings_service import RiskSettingsService
from app.services.safety_epoch import SafetyEpochService
from app.services.venue_submit_dispatcher import VenueSubmitDispatcher
from app.signal_fusion.action_eligibility import (
    ActionEligibilityCommand,
    MarketActionEvidence,
    PaperExecutionConfiguration,
)
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.ports import Clock
from app.signal_fusion.strategy_evaluation_policy import ExecutableStrategyPolicy
from app.workers.watcher_paper_targets import PaperScanTarget

DEMO_POLICY = "governed-blofin-demo/v1"
_NAMESPACE = UUID("c44ea7de-0008-4000-8000-a070e1100001")


def demo_posture_refusal(settings: Settings) -> str | None:
    if not governed_demo_worker_access_requested(settings):
        return "governed_demo_disarmed"
    if not blofin_execution_authorized(settings):
        return "governed_demo_credential_gate_closed"
    return None


def _resolved_lifecycle(session: Session, command: ExecutionCommand) -> str | None:
    resolved = session.scalar(
        select(GovernedDemoLifecycleResolution).where(
            GovernedDemoLifecycleResolution.command_id == command.id,
            GovernedDemoLifecycleResolution.organization_id == command.organization_id,
            GovernedDemoLifecycleResolution.account_id == command.account_id,
            GovernedDemoLifecycleResolution.user_id == command.user_id,
            GovernedDemoLifecycleResolution.revision_id == command.revision_id,
        )
    )
    if resolved is None:
        return None
    if canonical_sha256(resolved.evidence_payload) != resolved.content_hash:
        return "demo_lifecycle_integrity_operator_hold"
    return "demo_closed_reconciled"


class GovernedBloFinDemoLoop(AutomatedPaperLoop):
    """Existing canonical loop with venue-specific evidence and dispatch only."""

    def __init__(
        self,
        runtime: ProductionCanonicalRuntime,
        settings: Settings,
        clock: Clock,
        *,
        provider: GovernedBloFinDemoProvider | None = None,
        experiment_context: GovernedExperimentContext | None = None,
        activity_provider: BloFinActivityProvider | None = None,
    ) -> None:
        super().__init__(runtime, settings, clock)
        self._provider = provider
        self._snapshot: DemoVenueSnapshot | None = None
        self._experiment_context = experiment_context
        self._activity_provider = activity_provider

    def continue_experiment_setup(
        self,
        session: Session,
        *,
        tenant: TenantContext,
        experiment_id: UUID,
        version_id: UUID,
        variant_key: str,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        kill_switch_active: bool,
    ) -> AutomatedPaperLoopProof:
        """Explicit internal experiment entry through the existing governed loop.

        Domain start does not call this method and ordinary Watcher scans carry
        no experiment selection. Existing posture/approval/risk gates still apply.
        """
        if (tenant.organization_id, tenant.user_id) != (target.organization_id, target.user_id):
            raise ValueError("Experiment target must belong to its authenticated tenant.")
        context = GovernedExperimentContext(tenant, experiment_id, version_id, variant_key)
        context.require_active(session, self._settings, self._clock.now())
        loop = GovernedBloFinDemoLoop(
            self._runtime,
            self._settings,
            self._clock,
            provider=self._provider,
            experiment_context=context,
            activity_provider=self._activity_provider,
        )
        return loop.continue_confirmed_setup(
            session,
            target=target,
            candidate=candidate,
            assessment=assessment,
            window=window,
            assembled=assembled,
            policy=policy,
            kill_switch_active=kill_switch_active,
        )

    def _posture_refusal(self) -> str | None:
        return demo_posture_refusal(self._settings)

    def _policy_version(self) -> str:
        return DEMO_POLICY

    def _get_provider(self) -> GovernedBloFinDemoProvider:
        if self._provider is None:
            self._provider = GovernedBloFinDemoProvider(build_blofin_client(self._settings))
        return self._provider

    def _account(self, session: Session, target: PaperScanTarget) -> ExecutionAccount | None:
        if (str(target.organization_id), str(target.user_id)) != (
            self._settings.governed_blofin_demo_organization_id,
            self._settings.governed_blofin_demo_user_id,
        ):
            return None
        account = session.get(
            ExecutionAccount, UUID(self._settings.governed_blofin_demo_account_id)
        )
        if (
            account is None
            or not account.enabled
            or account.organization_id != target.organization_id
            or account.user_id != target.user_id
        ):
            return None
        return account

    def _bound(
        self,
        session: Session,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        account: ExecutionAccount,
    ) -> AutomatedPaperLoopProof:
        from app.strategy_brain.sfp.contracts import SfpSpec

        if isinstance(getattr(policy, "authored_spec", None), SfpSpec):
            return _proof(candidate, "skipped", "sfp_execution_plan_not_authorized")
        existing = self._runtime.plan_store.get_by_candidate_scope(
            organization_id=target.organization_id,
            user_id=target.user_id,
            account_id=account.id,
            candidate_id=candidate.candidate_id,
        )
        if existing is not None:
            if (
                existing.plan.execution_venue != "BLOFIN_DEMO"
                or existing.plan.execution_policy_version != DEMO_POLICY
            ):
                return _proof(candidate, "blocked", "demo_existing_plan_venue_mismatch")
            return super()._bound(
                session,
                target=target,
                candidate=candidate,
                assessment=assessment,
                window=window,
                assembled=assembled,
                policy=policy,
                account=account,
            )
        # One demo account/key cannot acquire concurrent unrelated positions.
        # Reservations persist for accepted and uncertain entries, fail closed.
        if has_demo_entry_history(
            session, account_id=account.id, organization_id=target.organization_id
        ):
            return _proof(candidate, "blocked", "demo_prior_entry_requires_reconciliation")
        session.commit()  # Persist Candidate; no DB transaction held over venue reads.
        try:
            self._snapshot = self._get_provider().snapshot(
                symbol=target.symbol,
                now=self._clock.now(),
                side=EntrySide.BUY
                if candidate.direction is TradeDirection.LONG
                else EntrySide.SELL,
            )
        except Exception as exc:
            diagnostic = failure_diagnostics(exc, stage="snapshot")
            return _proof(candidate, "blocked", f"demo_{diagnostic['reason_code']}")
        return super()._bound(
            session,
            target=target,
            candidate=candidate,
            assessment=assessment,
            window=window,
            assembled=assembled,
            policy=policy,
            account=account,
        )

    def _eligibility_command(
        self,
        session: Session,
        *,
        settings: Settings,
        target: PaperScanTarget,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        assembled: AssembledCanonicalEvidence,
        account: ExecutionAccount,
        now: datetime,
    ) -> ActionEligibilityCommand:
        command = _eligibility_command(
            session,
            settings=settings,
            target=target,
            candidate=candidate,
            assessment=assessment,
            window=window,
            assembled=assembled,
            account=account,
            now=now,
        )
        snapshot = self._require_snapshot()
        market = command.market_action
        return command.model_copy(
            update={
                "market_action": MarketActionEvidence(
                    venue_state_id=uuid5(
                        _NAMESPACE,
                        f"demo:{snapshot.instrument}:{snapshot.price}:{snapshot.observed_at.isoformat()}",
                    ),
                    evidence_venue=market.evidence_venue,
                    execution_venue=VenueId.BLOFIN,
                    evidence_price=market.evidence_price,
                    execution_price=snapshot.price,
                    required_action_evidence_fresh=True,
                    basis_fresh=True,
                    action_evidence_valid_until=min(
                        market.action_evidence_valid_until,
                        snapshot.observed_at + timedelta(seconds=10),
                    ),
                    symbol=market.symbol,
                ),
                "configuration": PaperExecutionConfiguration(
                    execution_mode=ExecutionMode.PAPER,
                    enable_real_trading=False,
                    exchange_mode=ExchangeMode.PAPER_EXCHANGE_DEMO,
                ),
            }
        )

    def _plan_terms(
        self,
        *,
        candidate: Candidate,
        assembled: AssembledCanonicalEvidence,
        policy: ExecutableStrategyPolicy,
        account_id: UUID,
        equity: Decimal,
        now: datetime,
        eligibility_valid_until: datetime,
        eligibility_id: UUID,
    ) -> TradePlanRevisionCreate | str:
        snapshot = self._require_snapshot()
        equity = min(equity, snapshot.equity, snapshot.available)
        terms = _plan_terms(
            candidate=candidate,
            assembled=assembled,
            policy=policy,
            account_id=account_id,
            equity=equity,
            now=now,
            eligibility_valid_until=eligibility_valid_until,
            eligibility_id=eligibility_id,
        )
        if isinstance(terms, str):
            return terms
        if snapshot.book.side is not terms.side:
            return "demo_quote_side_mismatch"
        if not 0 <= (now - snapshot.observed_at).total_seconds() < 10:
            return "demo_quote_stale"
        is_long = terms.side.value == "BUY"
        stop = (terms.risk_and_exits.stop.value / snapshot.tick).to_integral_value(
            rounding=ROUND_FLOOR if is_long else ROUND_CEILING
        ) * snapshot.tick
        target = (terms.risk_and_exits.targets[0].price.value / snapshot.tick).to_integral_value(
            rounding=ROUND_FLOOR if is_long else ROUND_CEILING
        ) * snapshot.tick
        entry = snapshot.price
        if (is_long and not stop < entry < target) or (not is_long and not target < entry < stop):
            return "demo_price_invalidates_setup"
        # Reserve fees+slippage conservatively; sizing uses base value per contract.
        cost_per_base = entry * Decimal("0.002")
        raw = (
            min(
                equity * Decimal("0.01") / (abs(entry - stop) + cost_per_base),
                equity * Decimal("0.10") / entry,
            )
            / snapshot.multiplier
        )
        raw = min(raw, snapshot.maximum)
        quantity = (raw / snapshot.lot).to_integral_value(rounding=ROUND_FLOOR) * snapshot.lot
        if quantity < snapshot.minimum or quantity * snapshot.multiplier * entry < Decimal("5"):
            return "demo_size_below_minimum"
        try:
            worst_entry = snapshot.book.worst_price(
                quantity, lot=snapshot.lot, minimum=snapshot.minimum, maximum=snapshot.maximum
            )
        except ValueError:
            return "demo_quote_depth_insufficient"
        base = quantity * snapshot.multiplier
        lower, upper = min(entry, worst_entry), max(entry, worst_entry)
        adverse_entry = upper if is_long else lower
        cost_per_base = upper * Decimal("0.002")
        maximum_loss = base * (abs(adverse_entry - stop) + cost_per_base)
        if base * upper > equity * Decimal("0.10"):
            return "demo_depth_exceeds_size_limit"
        if maximum_loss > equity * Decimal("0.01"):
            return "demo_depth_exceeds_risk_limit"
        values = terms.model_dump(mode="python")
        values.update(
            {
                "execution_venue": "BLOFIN_DEMO",
                "execution_instrument": snapshot.instrument,
                "execution_policy_version": DEMO_POLICY,
                "permission_attestation_id": uuid5(
                    _NAMESPACE,
                    f"demo-permission:{terms.account_id}:{snapshot.observed_at.isoformat()}",
                ),
                "permission_attestation_version": "verified-demo-read-trade/v1",
                "instrument_mapping_version": "blofin-linear-usdt/v1",
                "quantity": {"value": quantity, "unit": "CONTRACTS"},
                "quantity_unit": "CONTRACTS",
                "entry_zone": {"lower": lower, "upper": upper, "price_unit": "USDT"},
                "slippage_policy": {
                    "policy_id": "blofin-demo-conservative",
                    "policy_version": "1",
                    "maximum_bps": "10",
                },
                "instrument_rules": {
                    "contract_multiplier": snapshot.multiplier,
                    "contract_type": "LINEAR",
                    "base_currency": terms.instrument_rules.base_currency,
                    "quote_currency": "USDT",
                    "settlement_currency": "USDT",
                    "tick_size": snapshot.tick,
                    "lot_size": snapshot.lot,
                    "minimum_quantity": snapshot.minimum,
                    "minimum_notional": "5",
                    "rules_version": "blofin-observed-linear/v1",
                },
                "basis_policy": {
                    "policy_id": "binance-blofin-demo",
                    "policy_version": "1",
                    "evidence_price": terms.basis_policy.evidence_price.model_dump(),
                    "execution_price": {"value": entry, "unit": "USDT"},
                    "formula": "abs(execution_price-evidence_price)/evidence_price",
                    "timestamp": snapshot.observed_at,
                    "tolerance_bps": "20",
                    "freshness_seconds": 10,
                },
                "valid_until": min(terms.valid_until, snapshot.observed_at + timedelta(seconds=10)),
                "calculation_inputs": [
                    {
                        "name": "rounded_contract_quantity",
                        "input_value": raw,
                        "result_value": quantity,
                        "unit": "CONTRACTS",
                        "formula_id": "floor-to-venue-lot",
                        "formula_version": "1",
                        "precision": 12,
                        "rounding_mode": "ROUND_FLOOR",
                        "conservative_remainder": raw - quantity,
                    }
                ],
            }
        )
        values["risk_and_exits"].update(
            {
                "risk_budget": {"value": base * abs(adverse_entry - stop), "unit": "USDT"},
                "maximum_loss": {
                    "value": maximum_loss,
                    "unit": "USDT",
                },
                "fee_allowance": {"value": base * upper * Decimal("0.001"), "unit": "USDT"},
                "slippage_allowance": {"value": base * upper * Decimal("0.001"), "unit": "USDT"},
                "stop": {"value": stop, "unit": "USDT"},
                "margin_assumption_id": "verified-demo-existing-1x",
                "margin_assumption_version": "1",
            }
        )
        values["risk_and_exits"]["targets"][0]["price"]["value"] = target
        revised = TradePlanRevisionCreate.model_validate(values)
        try:
            planned_reward_risk(revised)
        except PlannedRewardRiskError as exc:
            # Executable bid/ask can invalidate an otherwise exactly 1R setup.
            return exc.reason
        return revised

    def _require_snapshot(self) -> DemoVenueSnapshot:
        if self._snapshot is None:
            raise ValueError("Demo preflight evidence missing.")
        return self._snapshot

    def _execute_existing(
        self,
        session: Session,
        *,
        target: PaperScanTarget,
        candidate: Candidate,
        revision_id: UUID,
        account_id: UUID,
        eligibility_id: UUID | None = None,
        eligibility_state: str | None = None,
    ) -> AutomatedPaperLoopProof:
        plans = self._plan_service()
        envelope = plans.get_scoped(
            revision_id, organization_id=target.organization_id, user_id=target.user_id
        )
        if (
            envelope.plan.execution_venue != "BLOFIN_DEMO"
            or envelope.plan.execution_policy_version != DEMO_POLICY
        ):
            return _proof(
                candidate, "blocked", "demo_existing_plan_venue_mismatch", revision_id=revision_id
            )
        context = self._experiment_context
        producer: BloFinNativeOutcomeProducer | None = None
        if context is not None:
            if (context.tenant.organization_id, context.tenant.user_id) != (
                target.organization_id,
                target.user_id,
            ):
                raise ValueError("Experiment target must belong to its authenticated tenant.")
            context.require_active(
                session, self._settings, self._clock.now(), revision_id=revision_id
            )
            producer = BloFinNativeOutcomeProducer(
                session, self._settings, context.tenant, clock=self._clock.now
            )
            producer.register_plan(
                context.experiment_id, context.version_id, context.variant_key, revision_id
            )
            session.commit()  # Origin is durable BEFORE any command is created.
            context.require_active(
                session, self._settings, self._clock.now(), revision_id=revision_id
            )
        if (
            session.scalar(
                select(GovernedDemoLifecycleResolution.id).where(
                    GovernedDemoLifecycleResolution.command_id.in_(
                        select(ExecutionCommand.id).where(
                            ExecutionCommand.organization_id == target.organization_id,
                            ExecutionCommand.user_id == target.user_id,
                            ExecutionCommand.account_id == account_id,
                            ExecutionCommand.revision_id == revision_id,
                        )
                    )
                )
            )
            is not None
        ):
            return _proof(candidate, "closed", "demo_closed_reconciled", revision_id=revision_id)
        approval = ApprovalService(
            session, AuditService(session), clock=self._clock.now, plans=plans
        )
        pending = approval.create_for_plan_revision(
            revision_id=revision_id,
            organization_id=target.organization_id,
            user_id=target.user_id,
            approval_reason="Explicit automatic demo capability; exact immutable hash binding",
        )
        decided = approval.decide(
            pending.id,
            ApprovalDecisionRequest(action=ApprovalAction.APPROVE),
            principal_organization_id=target.organization_id,
            principal_user_id=target.user_id,
            channel=AuthorizationChannel.API,
        )
        if decided.authorization is None:
            return _proof(candidate, "blocked", "authorization_missing", revision_id=revision_id)
        result = ExecutionService(
            session, self._settings, AuditService(session), canonical_runtime=self._runtime
        ).execute_paper_plan(
            _execute_request(
                target=target,
                account_id=account_id,
                revision_id=revision_id,
                authorization_id=decided.authorization.authorization_id,
                candidate_id=candidate.candidate_id,
            ),
            clock=self._clock.now,
        )
        if result.outcome is not ExecutionCommandOutcome.ALLOW:
            return _proof(
                candidate,
                "blocked",
                result.blocked_reason_code or "execution_blocked",
                revision_id=revision_id,
            )
        command_id = result.command_id
        effect = session.scalar(
            select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command_id)
        )
        if effect is None:
            raise ValueError("Durable demo effect missing.")
        if producer is not None:
            from app.services.blofin_activity_service import activity_provider

            producer.bind_entry(
                command_id, provider=self._activity_provider or activity_provider(self._settings)
            )
            session.commit()  # Verified position/binding is durable BEFORE dispatch leasing.
        dispatcher = self._dispatcher(session)
        if effect.state in {VenueSubmitEffectState.CREATED, VenueSubmitEffectState.LEASED}:
            owner = f"demo:{command_id}"
            effect = dispatcher.lease_effect(command_id=command_id, owner=owner)
            fence = int(effect.fencing_token)
            effect = dispatcher.authorize_dispatch(
                command_id=command_id, owner=owner, fencing_token=fence
            )
            if effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
                dispatcher.attempt_governed_demo_send(
                    command_id=command_id,
                    owner=owner,
                    fencing_token=fence,
                    provider=self._get_provider(),
                    settings=self._settings,
                    plan=envelope.plan,
                )
        elif effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
            dispatcher.recover_after_crash(command_id=command_id, owner="demo-recovery")
        session.commit()
        reason = self.reconcile_command(session, command_id=command_id)
        if producer is not None:
            try:
                producer.reconcile(command_id)
                session.commit()
            except ConflictError as error:
                if error.code != "experiment_account_unverified":
                    raise
                # A lost/stale identity after preflight keeps native reads
                # unavailable. It must not erase the committed uncertain send
                # identity or turn a safe dispatch refusal into another command.
                session.rollback()
        trade = session.scalar(
            select(JournalTrade).where(
                JournalTrade.organization_id == target.organization_id,
                JournalTrade.execution_lifecycle_id == command_id,
            )
        )
        return AutomatedPaperLoopProof(
            stage="filled" if trade is not None and trade.entry_price is not None else "dispatched",
            reason_code=reason,
            replayed=result.replayed,
            candidate_id=candidate.candidate_id,
            eligibility_id=envelope.lineage.eligibility_id,
            eligibility_state=envelope.lineage.eligibility_state.value,
            trade_plan_revision_id=revision_id,
            execution_command_id=command_id,
            paper_fill_id=_fill_id(session, command_id),
            journal_trade_id=trade.id if trade is not None else None,
            journal_status=trade.status.value if trade is not None else None,
        )

    def _dispatcher(self, session: Session) -> VenueSubmitDispatcher:
        return VenueSubmitDispatcher(session, self._safety(session), clock=self._clock.now)

    def _safety(self, session: Session) -> SafetyEpochService:
        return SafetyEpochService(
            session, self._settings, RiskSettingsService(session, AuditService(session))
        )

    def reconcile_command(self, session: Session, *, command_id: UUID) -> str:
        """Read-only venue recovery runs despite the kill switch. Never resubmits."""
        with self._runtime.bind_session(session):
            command = session.get(ExecutionCommand, command_id)
            if command is None or (
                str(command.organization_id),
                str(command.user_id),
                str(command.account_id),
            ) != (
                self._settings.governed_blofin_demo_organization_id,
                self._settings.governed_blofin_demo_user_id,
                self._settings.governed_blofin_demo_account_id,
            ):
                return "demo_scope_mismatch"
            envelope = self._runtime.plans.get_scoped(
                command.revision_id,
                organization_id=command.organization_id,
                user_id=command.user_id,
            )
            if envelope.plan.execution_policy_version != DEMO_POLICY:
                return "demo_plan_required"
            completed = _resolved_lifecycle(session, command)
            if completed is not None:
                return completed
            effect = session.scalar(
                select(VenueSubmitEffect).where(VenueSubmitEffect.command_id == command_id)
            )
            if effect is None or effect.state in {
                VenueSubmitEffectState.CREATED,
                VenueSubmitEffectState.LEASED,
                VenueSubmitEffectState.PROVEN_UNSENT,
            }:
                return "demo_not_sent"
            if effect.reconciliation_disposition == "REJECTED":
                return "demo_rejected"
            client_order_id = effect.client_order_id
            session.commit()
            try:
                evidence = self._get_provider().reconcile(
                    plan=envelope.plan, client_order_id=client_order_id
                )
            except Exception:
                return "demo_reconciliation_unavailable"
            if evidence is None:
                return "demo_ambiguous_operator_hold"
            self._safety(session).lock_epoch(
                organization_id=command.organization_id, account_id=command.account_id
            )
            completed = _resolved_lifecycle(session, command)
            if completed is not None:
                session.commit()
                return completed
            if evidence.status in {"rejected"} and not evidence.fills:
                self._dispatcher(session).record_demo_order(command_id=command_id, rejected=True)
                session.commit()
                return "demo_rejected"
            self._dispatcher(session).record_demo_order(command_id=command_id)
            projector = CanonicalPaperExecutionService(
                session,
                self._settings,
                AuditService(session),
                self._runtime,
                safety_epochs=self._safety(session),
                clock=self._clock.now,
            )
            # These are reported fee amounts, never a normalized cost/net PnL.
            # Missing historical currency must not become inferred USDT money.
            fee_currency_verified = all(
                fact.fee_currency == envelope.plan.instrument_rules.settlement_currency
                for fact in evidence.fills
            )
            cumulative_fees = Decimal("0") if fee_currency_verified else None
            for fact in sorted(evidence.fills, key=lambda item: (item.occurred_at, item.identity)):
                if cumulative_fees is not None:
                    cumulative_fees += fact.fee
                fill = self._dispatcher(session).apply_unique_fill(
                    command_id=command_id,
                    fill_quantity=fact.quantity,
                    fill_price=fact.price,
                    source_identity=fact.identity,
                    occurred_at=fact.occurred_at,
                    venue_source="blofin_demo",
                )
                projector.project_fill(
                    organization_id=command.organization_id,
                    user_id=command.user_id,
                    account_id=command.account_id,
                    command_id=command_id,
                    fill=fill,
                    revision_id=command.revision_id,
                    venue_fill_fee=fact.fee,
                    venue_fill_fee_currency=fact.fee_currency,
                    cumulative_fees=cumulative_fees,
                    demo_protection=evidence.protection_status,
                    demo_protection_order_ids=evidence.protection_order_ids,
                    demo_native_tpsl_id=evidence.native_tpsl_id,
                )
            exit_unavailable = False
            if evidence.fills:
                projection = session.scalar(
                    select(ExecutionProjection).where(
                        ExecutionProjection.receipt_id == effect.receipt_id
                    )
                )
                if (
                    projection is not None
                    and cumulative_fees is not None
                    and projection.fees != cumulative_fees
                ):
                    projection.fees = cumulative_fees
                    projection.version = int(projection.version) + 1
                if evidence.status in {"canceled", "cancelled", "expired"}:
                    self._dispatcher(session).record_demo_terminal(command_id=command_id)
                # Persist actual entry facts before separate bounded venue reads.
                # No DB transaction/epoch lock spans the exit provider IO.
                session.commit()
                try:
                    exit_evidence = self._get_provider().reconcile_exit(
                        plan=envelope.plan, entry=evidence
                    )
                except Exception:
                    exit_evidence = None
                    exit_unavailable = True
                if exit_evidence is not None:
                    try:
                        with session.begin_nested():
                            resolve_verified_demo_exit(
                                session,
                                runtime=self._runtime,
                                settings=self._settings,
                                epochs=self._safety(session),
                                command=command,
                                envelope=envelope,
                                evidence=exit_evidence,
                            )
                        session.commit()
                        return "demo_closed_reconciled"
                    except Exception:
                        KillSwitchService(session, AuditService(session), self._settings).activate(
                            organization_id=command.organization_id,
                            actor_user_id=command.user_id,
                            payload=KillSwitchMutationRequest(
                                confirm=True,
                                reason="BloFin demo exit projection requires operator review",
                            ),
                        )
                        session.commit()  # Preserve previously committed entry facts.
                        return "demo_exit_projection_operator_hold"
            self._safety(session).lock_epoch(
                organization_id=command.organization_id, account_id=command.account_id
            )
            completed = _resolved_lifecycle(session, command)
            if completed is not None:
                session.commit()
                return completed
            if evidence.fills and not evidence.protected:
                KillSwitchService(session, AuditService(session), self._settings).activate(
                    organization_id=command.organization_id,
                    actor_user_id=command.user_id,
                    payload=KillSwitchMutationRequest(
                        confirm=True,
                        reason="BloFin demo protection missing; operator action required",
                    ),
                )
                reason = "demo_protection_failed_operator_hold"
                effect.reconciliation_disposition = (
                    "DEMO_PROTECTION_" + evidence.protection_status.upper()
                )
            else:
                reason = "demo_filled_protected" if evidence.fills else "demo_acknowledged_unfilled"
                effect.reconciliation_disposition = (
                    "DEMO_PROTECTED" if evidence.fills else "DEMO_UNFILLED"
                )
                if exit_unavailable:
                    reason = "demo_filled_protected_exit_unavailable"
                    effect.reconciliation_disposition = "DEMO_PROTECTED_EXIT_UNAVAILABLE"
            if evidence.status in {"canceled", "cancelled", "expired"}:
                self._dispatcher(session).record_demo_terminal(command_id=command_id)
                reason = (
                    "demo_cancelled_after_fill" if evidence.fills else "demo_cancelled_unfilled"
                )
            session.commit()
            return reason

    def reconcile_pending(self, session: Session) -> tuple[str, ...]:
        if demo_posture_refusal(self._settings) is not None:
            return ()
        ids = list(
            session.scalars(
                select(ExecutionCommand.id)
                .join(TradePlanRevision, TradePlanRevision.id == ExecutionCommand.revision_id)
                .outerjoin(
                    GovernedDemoLifecycleResolution,
                    GovernedDemoLifecycleResolution.command_id == ExecutionCommand.id,
                )
                .where(
                    ExecutionCommand.organization_id
                    == UUID(self._settings.governed_blofin_demo_organization_id),
                    ExecutionCommand.user_id == UUID(self._settings.governed_blofin_demo_user_id),
                    ExecutionCommand.account_id
                    == UUID(self._settings.governed_blofin_demo_account_id),
                    ExecutionCommand.outcome == ExecutionCommandOutcome.ALLOW,
                    TradePlanRevision.execution_venue == "BLOFIN_DEMO",
                    TradePlanRevision.execution_policy_version == DEMO_POLICY,
                    GovernedDemoLifecycleResolution.id.is_(None),
                )
                .order_by(ExecutionCommand.created_at)
                .limit(20)
            )
        )
        session.commit()
        return tuple(self.reconcile_command(session, command_id=identity) for identity in ids)
