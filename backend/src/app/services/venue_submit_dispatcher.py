"""Dispatch-time safety barriers for VenueSubmitEffect.

Barrier 1 is the claim transaction (safety epoch lock).
Barrier 2: lease claim revalidates the epoch.
Barrier 3: LEASED -> DISPATCH_AUTHORIZED immediately before any send.
Barrier 4: ambiguous send enters RECONCILIATION_REQUIRED.

No database transaction is held across provider I/O. Production/demo POST is
refused; tests may call :meth:`attempt_fake_send` only.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal

import structlog
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import ConflictError, NotFoundError, TradingPolicyError
from app.core.execution_credentials import (
    blofin_execution_authorized,
    governed_demo_worker_access_requested,
    manual_demo_access_requested,
)
from app.db.models import (
    AccountSafetyEpoch,
    ExecutionCommand,
    ExecutionFillFact,
    VenueSubmitEffect,
)
from app.db.models import TradePlanRevision as PlanRow
from app.guardrails.redaction import redact_text
from app.providers.exchange.errors import ExchangeRequestError
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.providers.execution.fake_venue import FakeVenueSubmitProvider
from app.repositories.execution_protocol import (
    ExecutionCommandRepository,
    ExecutionFillFactRepository,
    ExecutionProjectionRepository,
    ExecutionReceiptRepository,
    RiskReservationRepository,
    VenueSubmitEffectRepository,
)
from app.schemas.execution_protocol import (
    ExecutionReceiptState,
    ExecutionReconciliationStatus,
    RiskReservationReleaseReason,
    RiskReservationReleaseState,
    UniqueFillResult,
    VenueSendDisposition,
    VenueSubmitEffectState,
)
from app.schemas.trade_plan import TradePlanRevision
from app.services.execution_claim import conservative_reservation
from app.services.execution_dispatch_boundary import (
    commit_barrier3,
    load_committed_dispatch_authorization,
    require_committed_dispatch_authorization,
    require_idle_session_for_provider_io,
)
from app.services.execution_fills import (
    convert_reservation_for_fill,
    cumulative_weighted_price,
    fill_content_hash,
)
from app.services.execution_integrity import ensure_db_transaction, is_fill_fact_unique_violation
from app.services.execution_transitions import append_transition, apply_projection_transition
from app.services.mappers.trade_plan_mapper import trade_plan_revision_to_schema
from app.services.safety_epoch import SafetyEpochService

_LEASE_SECONDS = 30
logger = structlog.get_logger(__name__)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class VenueSubmitDispatcher:
    def __init__(
        self,
        session: Session,
        safety_epochs: SafetyEpochService,
        *,
        clock: Callable[[], datetime] = _now,
    ) -> None:
        self._session = session
        self._epochs = safety_epochs
        self._settings = safety_epochs._settings
        self._clock = clock
        self._effects = VenueSubmitEffectRepository(session)
        self._commands = ExecutionCommandRepository(session)
        self._receipts = ExecutionReceiptRepository(session)
        self._projections = ExecutionProjectionRepository(session)
        self._reservations = RiskReservationRepository(session)
        self._fills = ExecutionFillFactRepository(session)

    def lease_effect(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        lease_seconds: int = _LEASE_SECONDS,
    ) -> VenueSubmitEffect:
        now = _aware(self._clock())
        ensure_db_transaction(self._session)
        command = self._require_command(command_id)
        from app.experiments.governed_context import require_registered_experiment_dispatch

        require_registered_experiment_dispatch(self._session, self._settings, command, now=now)
        epoch = self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        effect = self._require_effect_locked(command_id)
        if self._safety_blocks_dispatch(
            command=command, epoch=epoch, claimed_epoch=int(effect.safety_epoch)
        ):
            self._block_before_dispatch(command=command, effect=effect, now=now)
            return effect
        if effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
            return effect
        if effect.state is VenueSubmitEffectState.PROVEN_UNSENT:
            return effect
        if (
            effect.state is VenueSubmitEffectState.LEASED
            and effect.lease_owner == owner
            and effect.lease_expires_at is not None
            and _aware(effect.lease_expires_at) > now
        ):
            return effect
        if (
            effect.state is VenueSubmitEffectState.LEASED
            and effect.lease_owner not in {None, owner}
            and effect.lease_expires_at is not None
            and _aware(effect.lease_expires_at) > now
        ):
            raise ConflictError(
                "Venue submit effect lease is held by another worker.",
                details={"reason": "lease_held", "lease_owner": effect.lease_owner},
            )
        if effect.state not in {VenueSubmitEffectState.CREATED, VenueSubmitEffectState.LEASED}:
            raise ConflictError(
                "Effect is not eligible for lease.",
                details={"reason": "effect_not_leaseable", "state": effect.state.value},
            )
        effect.state = VenueSubmitEffectState.LEASED
        effect.lease_owner = owner
        effect.lease_expires_at = now + timedelta(seconds=lease_seconds)
        effect.fencing_token = int(effect.fencing_token) + 1
        effect.updated_at = now
        self._session.flush()
        return effect

    def authorize_dispatch(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        fencing_token: int,
    ) -> VenueSubmitEffect:
        now = _aware(self._clock())
        ensure_db_transaction(self._session)
        command = self._require_command(command_id)
        from app.experiments.governed_context import require_registered_experiment_dispatch

        require_registered_experiment_dispatch(self._session, self._settings, command, now=now)
        epoch = self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        effect = self._require_effect_locked(command_id)
        if effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
            if int(effect.dispatch_fencing_token or -1) != fencing_token:
                raise ConflictError(
                    "Dispatch already authorized with a different fence.",
                    details={"reason": "dispatch_fence_mismatch"},
                )
            commit_barrier3(self._session)
            return effect
        if self._safety_blocks_dispatch(
            command=command, epoch=epoch, claimed_epoch=int(effect.safety_epoch)
        ):
            self._block_before_dispatch(command=command, effect=effect, now=now)
            commit_barrier3(self._session)
            return effect
        if (
            effect.state is not VenueSubmitEffectState.LEASED
            or effect.lease_owner != owner
            or int(effect.fencing_token) != fencing_token
        ):
            raise ConflictError(
                "Lease fence does not match; stale worker cannot dispatch.",
                details={"reason": "lease_fence_mismatch"},
            )
        if effect.lease_expires_at is not None and _aware(effect.lease_expires_at) <= now:
            raise ConflictError(
                "Venue submit effect lease has expired.",
                details={"reason": "lease_expired"},
            )
        effect.state = VenueSubmitEffectState.DISPATCH_AUTHORIZED
        effect.dispatch_authorized_at = now
        effect.dispatch_safety_epoch = int(epoch.epoch)
        effect.dispatch_fencing_token = fencing_token
        effect.attempt = int(effect.attempt) + 1
        effect.dispatch_attempt = effect.attempt
        effect.updated_at = now
        commit_barrier3(self._session)
        return effect

    def attempt_fake_send(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        fencing_token: int,
        provider: FakeVenueSubmitProvider,
    ) -> VenueSubmitEffect:
        """Send only through the in-process fake. Never a BloFin or network client.

        Barrier 3 must already be committed. This method refuses pending writes and
        closes leftover read transactions before invoking the provider.
        """

        if not isinstance(provider, FakeVenueSubmitProvider):
            raise TradingPolicyError(
                "Phase 1 venue POST is disabled for production and demo providers.",
                details={"reason": "external_venue_submit_disabled"},
            )
        require_idle_session_for_provider_io(self._session)
        snapshot = require_committed_dispatch_authorization(
            load_committed_dispatch_authorization(self._session, command_id),
            owner=owner,
            fencing_token=fencing_token,
        )
        require_idle_session_for_provider_io(self._session)
        client_order_id = snapshot.client_order_id
        try:
            result = provider.submit(client_order_id=client_order_id)
        except Exception:
            ensure_db_transaction(self._session)
            self._mark_ambiguous(command_id=command_id, now=_aware(self._clock()))
            raise
        if provider.crash_before_local_ack:
            raise RuntimeError("crash_after_provider_before_ack")
        ensure_db_transaction(self._session)
        if result is None:
            return self._mark_ambiguous(command_id=command_id, now=_aware(self._clock()))
        if result.status == "accepted":
            return self._mark_acknowledged(command_id=command_id, now=_aware(self._clock()))
        return self._mark_rejected(command_id=command_id, now=_aware(self._clock()))

    def attempt_governed_demo_send(
        self,
        *,
        command_id: uuid.UUID,
        owner: str,
        fencing_token: int,
        provider: GovernedBloFinDemoProvider,
        settings: Settings,
        plan: TradePlanRevision,
    ) -> VenueSubmitEffect:
        """Consume committed dispatch authority once, before one demo POST.

        The durable ambiguous marker commits before IO. A crash at any point
        therefore requires read-only client-id reconciliation, never resend.
        """
        if not isinstance(provider, GovernedBloFinDemoProvider) or not (
            blofin_execution_authorized(settings)
            and (
                manual_demo_access_requested(settings)
                if plan.schema_version == "ManualDemoTradePlanV1"
                else governed_demo_worker_access_requested(settings)
            )
        ):
            raise TradingPolicyError("Governed demo dispatch is disarmed.")
        if plan.execution_venue != "BLOFIN_DEMO" or plan.execution_policy_version != (
            "manual-blofin-demo/v1"
            if plan.schema_version == "ManualDemoTradePlanV1"
            else "governed-blofin-demo/v1"
        ):
            raise TradingPolicyError("Canonical governed demo plan required.")
        from app.services.planned_reward_risk import PlannedRewardRiskError, execution_reward_risk

        try:
            execution_reward_risk(plan)
        except PlannedRewardRiskError as exc:
            raise TradingPolicyError(str(exc), details={"reason": exc.reason}) from exc
        require_idle_session_for_provider_io(self._session)
        snapshot = require_committed_dispatch_authorization(
            load_committed_dispatch_authorization(self._session, command_id),
            owner=owner,
            fencing_token=fencing_token,
        )
        ensure_db_transaction(self._session)
        command = self._require_command(command_id)
        epoch = self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        effect = self._require_effect_locked(command_id)
        if effect.state is not VenueSubmitEffectState.DISPATCH_AUTHORIZED:
            raise ConflictError("Demo send already consumed; reconciliation required.")
        if (
            (str(command.organization_id), str(command.user_id), str(command.account_id))
            != (
                settings.governed_blofin_demo_organization_id,
                settings.governed_blofin_demo_user_id,
                settings.governed_blofin_demo_account_id,
            )
            or command.revision_id != plan.revision_id
            or command.plan_content_hash != plan.content_hash
        ):
            raise TradingPolicyError("Demo dispatch principal/plan binding mismatch.")
        if self._safety_blocks_dispatch(
            command=command, epoch=epoch, claimed_epoch=int(effect.safety_epoch)
        ):
            self._mark_ambiguous(command_id=command_id, now=_aware(self._clock()))
            self._session.commit()
            return effect
        if _aware(plan.valid_until) <= _aware(self._clock()):
            self._mark_ambiguous(command_id=command_id, now=_aware(self._clock()))
            self._session.commit()
            return effect
        self._mark_ambiguous(command_id=command_id, now=_aware(self._clock()))
        self._session.commit()
        require_idle_session_for_provider_io(self._session)
        try:
            provider.submit(
                plan=plan,
                client_order_id=snapshot.client_order_id,
                before_post=lambda: self._verify_demo_send_gate(
                    command_id=command_id, owner=owner, fencing_token=fencing_token, plan=plan
                ),
            )
        except ExchangeRequestError as exc:
            if exc.details is not None and exc.details.http_status in {400, 401, 403, 422}:
                ensure_db_transaction(self._session)
                return self._mark_rejected(
                    command_id=command_id, now=_aware(self._clock()), source="blofin_demo"
                )
            return self._require_effect(command_id)
        except Exception as exc:
            logger.warning(
                "governed_demo_send_incomplete",
                command_id=str(command_id),
                exception_type=type(exc).__name__,
                reason=redact_text(str(exc))[:180],
            )
            # Includes response loss, HTTP failures, and failed protection.
            # Only client-id lookup can prove what the venue accepted.
            return self._require_effect(command_id)
        ensure_db_transaction(self._session)
        return self._mark_acknowledged(
            command_id=command_id, now=_aware(self._clock()), source="blofin_demo"
        )

    def _verify_demo_send_gate(
        self, *, command_id: uuid.UUID, owner: str, fencing_token: int, plan: TradePlanRevision
    ) -> None:
        """Final kill/fence/TTL check after read preflight and directly before POST."""
        ensure_db_transaction(self._session)
        # Preflight leaves time for a different worker/operator to advance the
        # fence or safety epoch. Locked reads must refresh the identity map too.
        self._session.expire_all()
        command = self._require_command(command_id)
        from app.experiments.governed_context import require_registered_experiment_dispatch

        try:
            require_registered_experiment_dispatch(
                self._session, self._settings, command, now=_aware(self._clock())
            )
        except Exception:
            self._session.rollback()
            raise
        epoch = self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        effect = self._require_effect_locked(command_id)
        if (
            effect.state is not VenueSubmitEffectState.SEND_AMBIGUOUS
            or effect.lease_owner != owner
            or effect.dispatch_fencing_token != fencing_token
            or self._safety_blocks_dispatch(
                command=command, epoch=epoch, claimed_epoch=int(effect.safety_epoch)
            )
            or _aware(plan.valid_until) <= _aware(self._clock())
        ):
            self._session.rollback()
            raise TradingPolicyError("Demo final dispatch fence, safety, or plan TTL refused.")
        self._session.commit()
        require_idle_session_for_provider_io(self._session)

    def record_demo_order(
        self, *, command_id: uuid.UUID, rejected: bool = False
    ) -> VenueSubmitEffect:
        """Record an identity-verified read response; no order IO or fill invention."""
        effect = self._require_effect(command_id)
        if rejected:
            if (
                effect.reconciliation_disposition == VenueSendDisposition.REJECTED.value
                and not effect.uncertainty
            ):
                return effect
            return self._mark_rejected(
                command_id=command_id, now=_aware(self._clock()), source="blofin_demo"
            )
        if effect.state is VenueSubmitEffectState.SEND_ATTEMPTED and not effect.uncertainty:
            return effect
        return self._mark_acknowledged(
            command_id=command_id, now=_aware(self._clock()), source="blofin_demo"
        )

    def record_demo_terminal(self, *, command_id: uuid.UUID) -> VenueSubmitEffect:
        """Release only the verified unfilled remainder after venue cancellation."""
        command = self._require_command(command_id)
        self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        effect = self._require_effect_locked(command_id)
        receipt = self._receipts.get_by_command(command_id)
        projection = (
            self._projections.get_by_receipt_for_update(receipt.id) if receipt is not None else None
        )
        if receipt is None or projection is None:
            raise NotFoundError("Demo cancellation receipt missing.")
        if projection.state in {
            ExecutionReceiptState.CANCELLED,
            ExecutionReceiptState.PARTIALLY_FILLED_CANCELLED,
        }:
            return effect
        now = _aware(self._clock())
        state = (
            ExecutionReceiptState.PARTIALLY_FILLED_CANCELLED
            if projection.filled_quantity
            else ExecutionReceiptState.CANCELLED
        )
        transition = append_transition(
            self._session,
            receipt=receipt,
            prior_state=projection.state,
            new_state=state,
            source_fact="blofin_demo_cancelled",
            source_identity=effect.client_order_id,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor="blofin_demo",
        )
        apply_projection_transition(
            self._session,
            projection=projection,
            transition=transition,
            remaining_quantity=Decimal("0"),
            reconciliation_status=ExecutionReconciliationStatus.RESOLVED,
            updated_at=now,
        )
        self._release_unused_reservation(
            command_id=command_id,
            reason=RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_CANCELLATION,
            now=now,
        )
        return effect

    def fence_proven_unsent(self, *, command_id: uuid.UUID) -> VenueSubmitEffect:
        """Fence an existing never-dispatched effect; no provider request or retry."""
        command = self._require_command(command_id)
        self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        effect = self._require_effect_locked(command_id)
        if (
            effect.state
            not in {
                VenueSubmitEffectState.CREATED,
                VenueSubmitEffectState.LEASED,
                VenueSubmitEffectState.PROVEN_UNSENT,
            }
            or effect.dispatch_authorized_at is not None
            or effect.uncertainty
        ):
            raise TradingPolicyError("Dispatch may have occurred; local unsent recovery refused.")
        if effect.state != VenueSubmitEffectState.PROVEN_UNSENT:
            effect.fencing_token += 1
            self._block_before_dispatch(
                command=command, effect=effect, now=_aware(self._clock()), release_reservation=False
            )
        return effect

    def recover_after_crash(self, *, command_id: uuid.UUID, owner: str) -> VenueSubmitEffect:
        ensure_db_transaction(self._session)
        effect = self._require_effect(command_id)
        if effect.state is VenueSubmitEffectState.CREATED:
            return self.lease_effect(command_id=command_id, owner=owner)
        if effect.state is VenueSubmitEffectState.LEASED:
            return self.authorize_dispatch(
                command_id=command_id,
                owner=owner,
                fencing_token=int(effect.fencing_token),
            )
        if effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
            return self._mark_ambiguous(command_id=command_id, now=_aware(self._clock()))
        return effect

    def apply_unique_fill(
        self,
        *,
        command_id: uuid.UUID,
        fill_quantity: Decimal,
        fill_price: Decimal,
        source_identity: str,
        occurred_at: datetime,
        venue_source: str = "phase1-fake-venue",
    ) -> UniqueFillResult:
        now = _aware(self._clock())
        if source_identity is None or not str(source_identity).strip():
            raise ConflictError(
                "Authoritative fill identity is required.",
                details={"reason": "missing_source_fill_identity"},
            )
        if occurred_at is None:
            raise ConflictError(
                "Authoritative fill occurrence time is required.",
                details={"reason": "missing_authoritative_occurred_at"},
            )
        fill_time = _aware(occurred_at)
        if (
            not fill_quantity.is_finite()
            or not fill_price.is_finite()
            or fill_quantity <= 0
            or fill_price <= 0
        ):
            raise ConflictError(
                "Fill quantity and price must be positive.",
                details={"reason": "invalid_fill_amounts"},
            )
        ensure_db_transaction(self._session)
        command = self._require_command(command_id)
        self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        receipt = self._receipts.get_by_command(command_id)
        projection = (
            self._projections.get_by_receipt_for_update(receipt.id) if receipt is not None else None
        )
        reservation = self._reservations.get_by_command_for_update(command_id)
        if receipt is None or projection is None or reservation is None:
            raise NotFoundError("Fill target identities were not found.")
        existing_fact = self._fills.get_by_source(
            receipt_id=receipt.id, source_fill_identity=source_identity
        )
        if existing_fact is None and fill_quantity > projection.remaining_quantity:
            raise ConflictError("Fill exceeds the immutable authorized quantity.")
        digest = fill_content_hash(
            receipt_id=str(receipt.id),
            command_id=str(command.id),
            venue_source=venue_source,
            source_fill_identity=source_identity,
            quantity=fill_quantity,
            price=fill_price,
            unit=projection.quantity_unit,
            occurred_at=fill_time,
        )
        fact = ExecutionFillFact(
            organization_id=command.organization_id,
            command_id=command.id,
            receipt_id=receipt.id,
            venue_source=venue_source,
            source_fill_identity=source_identity,
            quantity=fill_quantity,
            price=fill_price,
            unit=projection.quantity_unit,
            occurred_at=fill_time,
            content_hash=digest,
        )
        try:
            with self._session.begin_nested():
                self._fills.add(fact)
                self._session.flush()
        except IntegrityError as exc:
            if not is_fill_fact_unique_violation(exc):
                raise
            existing = self._fills.get_by_source(
                receipt_id=receipt.id, source_fill_identity=source_identity
            )
            if existing is None:
                raise
            if existing.content_hash != digest:
                raise ConflictError(
                    "Source fill identity already exists with conflicting content.",
                    details={
                        "reason": "conflicting_fill_identity",
                        "source_fill_identity": source_identity,
                    },
                ) from exc
            return UniqueFillResult(
                replayed=True,
                fill_id=existing.id,
                receipt_id=receipt.id,
                source_fill_identity=source_identity,
                content_hash=existing.content_hash,
                filled_quantity=projection.filled_quantity,
                remaining_quantity=projection.remaining_quantity,
                weighted_price=projection.weighted_price,
            )

        accounting = self._epochs.lock_risk_accounting(
            organization_id=command.organization_id,
            account_id=command.account_id,
            user_id=command.user_id,
            exposure_unit=reservation.exposure_unit,
        )
        convert_reservation_for_fill(
            reservation=reservation,
            accounting=accounting,
            fill_quantity=fill_quantity,
            fill_price=fill_price,
            now=now,
        )
        new_filled = projection.filled_quantity + fill_quantity
        new_remaining = projection.remaining_quantity - fill_quantity
        if new_remaining < 0:
            new_remaining = Decimal("0")
        weighted = cumulative_weighted_price(
            previous_filled=projection.filled_quantity,
            previous_weighted=projection.weighted_price,
            fill_quantity=fill_quantity,
            fill_price=fill_price,
        )
        new_state = (
            ExecutionReceiptState.ACKNOWLEDGED
            if new_remaining == 0
            else ExecutionReceiptState.PARTIALLY_FILLED
        )
        transition = append_transition(
            self._session,
            receipt=receipt,
            prior_state=projection.state,
            new_state=new_state,
            source_fact="unique_fill",
            source_identity=source_identity,
            occurred_at=fill_time,
            observed_at=now,
            recorded_at=now,
            actor="execution_service",
            quantity=fill_quantity,
            quantity_unit=projection.quantity_unit,
            unit_price=fill_price,
        )
        apply_projection_transition(
            self._session,
            projection=projection,
            transition=transition,
            filled_quantity=new_filled,
            remaining_quantity=new_remaining,
            weighted_price=weighted,
            updated_at=now,
        )
        return UniqueFillResult(
            replayed=False,
            fill_id=fact.id,
            receipt_id=receipt.id,
            source_fill_identity=source_identity,
            content_hash=digest,
            filled_quantity=new_filled,
            remaining_quantity=new_remaining,
            weighted_price=weighted,
        )

    def _block_before_dispatch(
        self,
        *,
        command: ExecutionCommand,
        effect: VenueSubmitEffect,
        now: datetime,
        release_reservation: bool = True,
    ) -> None:
        if effect.state is VenueSubmitEffectState.DISPATCH_AUTHORIZED:
            raise ConflictError(
                "Cannot pretend a dispatch-authorized send disappeared.",
                details={"reason": "already_dispatch_authorized"},
            )
        receipt = self._receipts.get_by_command(command.id)
        projection = self._projections.get_by_receipt(receipt.id) if receipt is not None else None
        if receipt is None or projection is None:
            raise NotFoundError("Receipt missing during blocked-before-dispatch.")
        if projection.state is ExecutionReceiptState.BLOCKED_BEFORE_DISPATCH:
            return
        transition = append_transition(
            self._session,
            receipt=receipt,
            prior_state=projection.state,
            new_state=ExecutionReceiptState.BLOCKED_BEFORE_DISPATCH,
            source_fact="safety_epoch_block_before_dispatch",
            source_identity=f"epoch:{int(effect.safety_epoch)}",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor="safety_epoch",
        )
        apply_projection_transition(
            self._session,
            projection=projection,
            transition=transition,
            reconciliation_status=ExecutionReconciliationStatus.RESOLVED,
            updated_at=now,
        )
        effect.state = VenueSubmitEffectState.PROVEN_UNSENT
        effect.uncertainty = False
        effect.reconciliation_disposition = VenueSendDisposition.PROVEN_UNSENT.value
        effect.lease_owner = None
        effect.lease_expires_at = None
        effect.updated_at = now
        if release_reservation:
            self._release_unused_reservation(
                command_id=command.id,
                reason=RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_PROVEN_UNSENT,
                now=now,
            )
        self._session.flush()

    def _mark_ambiguous(self, *, command_id: uuid.UUID, now: datetime) -> VenueSubmitEffect:
        command = self._require_command(command_id)
        effect = self._require_effect(command_id)
        receipt = self._receipts.get_by_command(command_id)
        projection = self._projections.get_by_receipt(receipt.id) if receipt is not None else None
        if receipt is None or projection is None:
            raise NotFoundError("Receipt missing during ambiguous send.")
        epoch = self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        transition = append_transition(
            self._session,
            receipt=receipt,
            prior_state=projection.state,
            new_state=ExecutionReceiptState.RECONCILIATION_REQUIRED,
            source_fact="ambiguous_send",
            source_identity=f"epoch:{int(epoch.epoch)}",
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor="execution_service",
        )
        apply_projection_transition(
            self._session,
            projection=projection,
            transition=transition,
            reconciliation_status=ExecutionReconciliationStatus.REQUIRED,
            updated_at=now,
        )
        effect.state = VenueSubmitEffectState.SEND_AMBIGUOUS
        effect.uncertainty = True
        effect.reconciliation_disposition = VenueSendDisposition.AMBIGUOUS.value
        effect.updated_at = now
        self._session.flush()
        return effect

    def _mark_acknowledged(
        self, *, command_id: uuid.UUID, now: datetime, source: str = "fake_venue"
    ) -> VenueSubmitEffect:
        effect = self._require_effect(command_id)
        receipt = self._receipts.get_by_command(command_id)
        projection = self._projections.get_by_receipt(receipt.id) if receipt is not None else None
        if receipt is None or projection is None:
            raise NotFoundError("Receipt missing during acknowledgement.")
        transition = append_transition(
            self._session,
            receipt=receipt,
            prior_state=projection.state,
            new_state=ExecutionReceiptState.ACKNOWLEDGED,
            source_fact=f"{source}_accepted",
            source_identity=effect.client_order_id,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor=source,
        )
        apply_projection_transition(
            self._session,
            projection=projection,
            transition=transition,
            reconciliation_status=ExecutionReconciliationStatus.RESOLVED,
            updated_at=now,
        )
        effect.state = VenueSubmitEffectState.SEND_ATTEMPTED
        effect.uncertainty = False
        effect.reconciliation_disposition = VenueSendDisposition.ACCEPTED.value
        effect.updated_at = now
        self._session.flush()
        return effect

    def _mark_rejected(
        self, *, command_id: uuid.UUID, now: datetime, source: str = "fake_venue"
    ) -> VenueSubmitEffect:
        effect = self._require_effect(command_id)
        receipt = self._receipts.get_by_command(command_id)
        projection = self._projections.get_by_receipt(receipt.id) if receipt is not None else None
        if receipt is None or projection is None:
            raise NotFoundError("Receipt missing during rejection.")
        transition = append_transition(
            self._session,
            receipt=receipt,
            prior_state=projection.state,
            new_state=ExecutionReceiptState.REJECTED,
            source_fact=f"{source}_rejected",
            source_identity=effect.client_order_id,
            occurred_at=now,
            observed_at=now,
            recorded_at=now,
            actor=source,
        )
        apply_projection_transition(
            self._session,
            projection=projection,
            transition=transition,
            remaining_quantity=Decimal("0"),
            updated_at=now,
        )
        effect.state = VenueSubmitEffectState.SEND_ATTEMPTED
        effect.uncertainty = False
        effect.reconciliation_disposition = VenueSendDisposition.REJECTED.value
        effect.updated_at = now
        self._release_unused_reservation(
            command_id=command_id,
            reason=RiskReservationReleaseReason.UNUSED_REMAINDER_AFTER_REJECTION,
            now=now,
        )
        self._session.flush()
        return effect

    def _release_unused_reservation(
        self,
        *,
        command_id: uuid.UUID,
        reason: RiskReservationReleaseReason,
        now: datetime,
    ) -> None:
        command = self._require_command(command_id)
        self._epochs.lock_epoch(
            organization_id=command.organization_id, account_id=command.account_id
        )
        reservation = self._reservations.get_by_command_for_update(command_id)
        if reservation is None:
            return
        unused = reservation.remaining_reserved_notional
        if unused <= 0:
            return
        plan_row = self._session.get(PlanRow, command.revision_id)
        if plan_row is not None and plan_row.schema_version == "ManualDemoTradePlanV1":
            plan = trade_plan_revision_to_schema(plan_row)
            from app.services.manual_demo_recovery import verified_entry_notional

            actual = verified_entry_notional(self._session, command, plan)
            exact_unused = max(
                Decimal("0"), conservative_reservation(plan).pending_notional - actual
            )
            if abs(exact_unused - unused) > Decimal("0.00000001"):
                raise TradingPolicyError("Manual reservation remainder conflicts with plan/fills.")
            unused = min(unused, exact_unused.quantize(Decimal("0.00000001"), rounding=ROUND_FLOOR))
        accounting = self._epochs.lock_risk_accounting(
            organization_id=command.organization_id,
            account_id=command.account_id,
            user_id=command.user_id,
            exposure_unit=reservation.exposure_unit,
        )
        accounting.reserved_notional -= unused
        if accounting.reserved_notional < 0:
            accounting.reserved_notional = Decimal("0")
        # Partial cancellation terminates only the unfilled remainder. Existing
        # exposure retains its loss budget and converted daily trade count.
        if not reservation.converted_trade_slots:
            accounting.reserved_daily_loss -= reservation.daily_loss_allocation
            if accounting.reserved_daily_loss < 0:
                accounting.reserved_daily_loss = Decimal("0")
        accounting.reserved_trade_slots -= max(
            0, int(reservation.daily_trade_allocation) - int(reservation.converted_trade_slots)
        )
        if accounting.reserved_trade_slots < 0:
            accounting.reserved_trade_slots = 0
        reserved = dict(accounting.symbol_reserved or {})
        current = Decimal(str(reserved.get(reservation.instrument, "0")))
        reserved[reservation.instrument] = str(max(Decimal("0"), current - unused))
        accounting.symbol_reserved = reserved
        reservation.remaining_reserved_notional = Decimal("0")
        reservation.release_state = RiskReservationReleaseState.RELEASED
        reservation.release_reason = reason
        reservation.updated_at = now
        accounting.version = int(accounting.version) + 1

    def _safety_blocks_dispatch(
        self,
        *,
        command: ExecutionCommand,
        epoch: AccountSafetyEpoch,
        claimed_epoch: int,
    ) -> bool:
        if self._epochs.organization_kill_active(command.organization_id):
            return True
        return self._epoch_blocks(epoch_value=claimed_epoch, current=epoch)

    def _epoch_blocks(self, *, epoch_value: int, current: AccountSafetyEpoch) -> bool:
        return bool(current.blocking) or int(current.epoch) != epoch_value

    def _require_command(self, command_id: uuid.UUID) -> ExecutionCommand:
        command = self._commands.get(command_id)
        if command is None:
            raise NotFoundError("Execution command not found")
        return command

    def _require_effect(self, command_id: uuid.UUID) -> VenueSubmitEffect:
        effect = self._effects.get_by_command(command_id)
        if effect is None:
            raise NotFoundError("Venue submit effect not found")
        return effect

    def _require_effect_locked(self, command_id: uuid.UUID) -> VenueSubmitEffect:
        effect = self._effects.get_by_command_for_update(command_id)
        if effect is None:
            raise NotFoundError("Venue submit effect not found")
        return effect
