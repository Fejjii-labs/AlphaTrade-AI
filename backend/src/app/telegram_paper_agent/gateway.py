"""Paper-only Telegram interaction layer.

Watcher/Candidate/journal notifications go through TelegramSecurityProtocol
outbox. Inbound discussion is bound private-chat only. Mutating paper actions
require identity-bound confirmation. Telegram never executes, never mints a
Candidate, never overrides SetupAssessment or risk, and never enables live trading.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4, uuid5

from app.candidate_alerts.authority import require_active_binding
from app.candidate_alerts.contracts import CandidateAlertIntent, CandidateAlertRecipient
from app.candidate_alerts.gateway import CandidateAlertGateway
from app.services.canonical_serialization import canonical_sha256
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.signal_fusion.lifecycle import CandidateLifecycleService, in_memory_candidate_lifecycle
from app.telegram_paper_agent.authority import refusal_for
from app.telegram_paper_agent.classify import classify_inbound_text
from app.telegram_paper_agent.confirmation import (
    format_paper_confirmation,
    parse_paper_confirmation_payload,
    payload_from_presented,
)
from app.telegram_paper_agent.content import (
    format_confirmed_setup_footer,
    format_journal_outcome_text,
    format_learning_summary_text,
    format_paper_status_text,
    format_watcher_blocked_text,
)
from app.telegram_paper_agent.contracts import (
    PAPER_NOTIFY_OUTBOX_PREFIX,
    PAPER_RESOURCE_CANDIDATE,
    PAPER_RESOURCE_JOURNAL,
    PAPER_RESOURCE_WATCHER,
    PAPER_THREAD_OUTBOX_PREFIX,
    DiscussionIntent,
    JournalOutcomeView,
    PaperAlertRecipient,
    PaperDiscussionResult,
    PaperNotificationIntent,
    PaperNotificationKind,
    PaperNotificationProjection,
    PaperThread,
    PaperThreadMessage,
    PresentedPaperConfirmation,
    WatcherScanNotice,
)
from app.telegram_paper_agent.errors import (
    PaperActionRefusedError,
    PaperTelegramDisabledError,
    PaperTelegramTenantError,
)
from app.telegram_paper_agent.explain import (
    explain_candidate,
    explain_evidence,
    explain_market_context,
    explain_persisted_candidate,
    explain_risk,
    explain_strategy,
)
from app.telegram_paper_agent.identity import (
    PAPER_NOTIFY_IDENTITY_NAMESPACE,
    build_paper_notification_identity,
    paper_confirmation_id,
    paper_telegram_revision_id,
    paper_thread_id,
)
from app.telegram_paper_agent.memory import (
    InMemoryPaperAgentStore,
    InMemoryPaperContext,
    PaperAgentStore,
)
from app.telegram_paper_agent.ports import PaperContextPort
from app.telegram_security.actions import READ_ONLY_TELEGRAM_ACTIONS, TelegramRemoteAction
from app.telegram_security.clock import Clock, FrozenClock
from app.telegram_security.contracts import (
    ActionOutcome,
    ActionPayload,
    ActionReceiptState,
    CallbackIdentity,
    MessageIdentity,
    OutboxRecord,
    TelegramInboundUpdate,
)
from app.telegram_security.hashing import payload_binding_hash
from app.telegram_security.protocol import TelegramSecurityProtocol

_MUTATING_ACTIONS = frozenset(
    {
        TelegramRemoteAction.REJECT,
        TelegramRemoteAction.SKIP,
        TelegramRemoteAction.APPROVE,
        TelegramRemoteAction.REDUCE_RISK,
    }
)
_BLOCKED_REASONS = frozenset(
    {
        "stale_evidence",
        "provider_outage",
        "wrong_source",
        "candidate_creation_failed",
    }
)
_CHAT_THREAD_RESOURCE = "telegram_chat"


class TelegramPaperAgent:
    """Compose Watcher/Candidate alerts with bound Telegram discussion."""

    def __init__(
        self,
        *,
        protocol: TelegramSecurityProtocol,
        candidates: CandidateAlertGateway,
        clock: Clock,
        store: PaperAgentStore | None = None,
        context: PaperContextPort | None = None,
        enabled: bool | None = None,
    ) -> None:
        self._protocol = protocol
        self._candidates = candidates
        self._clock = clock
        self._store = store or InMemoryPaperAgentStore()
        self._context = context or InMemoryPaperContext()
        self._enabled = protocol.enabled if enabled is None else enabled
        self.execution_attempt_count = 0
        self.candidate_mint_attempt_count = 0
        self.live_trading_enable_attempt_count = 0

    @classmethod
    def in_memory(
        cls,
        *,
        enabled: bool = False,
        now: datetime | None = None,
        protocol: TelegramSecurityProtocol | None = None,
        lifecycle: CandidateLifecycleService | None = None,
        context: PaperContextPort | None = None,
    ) -> TelegramPaperAgent:
        clock = FrozenClock(now) if now is not None else FrozenClock()
        resolved_protocol = protocol or TelegramSecurityProtocol.in_memory(
            enabled=enabled, clock=clock
        )
        resolved_lifecycle = lifecycle or in_memory_candidate_lifecycle(now=clock.now())
        candidates = CandidateAlertGateway(
            lifecycle=resolved_lifecycle, protocol=resolved_protocol, clock=clock
        )
        return cls(
            protocol=resolved_protocol,
            candidates=candidates,
            clock=clock,
            context=context,
            enabled=resolved_protocol.enabled,
        )

    @property
    def protocol(self) -> TelegramSecurityProtocol:
        return self._protocol

    @property
    def candidates(self) -> CandidateAlertGateway:
        return self._candidates

    @property
    def store(self) -> PaperAgentStore:
        return self._store

    def project_watcher_notice(
        self,
        *,
        notice: WatcherScanNotice,
        recipient: PaperAlertRecipient,
        candidate: Candidate | None = None,
        assessment: SetupAssessment | None = None,
        window: CanonicalEvidenceWindowV1 | None = None,
    ) -> PaperNotificationProjection | None:
        """Project a meaningful Watcher event. Empty scans are not alerts."""
        self._require_enabled()
        self._require_recipient_org(recipient, notice.organization_id)
        from app.strategy_brain.sfp.contracts import SFP_KIND

        if notice.sfp_strategy or (
            candidate is not None and candidate.fusion_policy_version == SFP_KIND
        ):
            if notice.user_id != recipient.user_id:
                raise ValueError("SFP scan user does not match recipient.")
            for summary in notice.sfp_alerts:
                self._candidates.project_sfp_event(
                    summary=summary, recipient=_as_candidate_recipient(recipient)
                )
            return None
        confirmed = (
            notice.published
            and candidate is not None
            and assessment is not None
            and window is not None
        )
        from app.schemas.nested_continuation import NESTED_KIND

        nested_strategy = notice.nested_strategy or (
            candidate is not None and candidate.fusion_policy_version == NESTED_KIND
        )
        if nested_strategy and (notice.nested is None or not confirmed):
            return None
        if confirmed:
            assert candidate is not None
            assert assessment is not None
            assert window is not None
            return self.project_candidate_alert(
                candidate=candidate,
                assessment=assessment,
                window=window,
                recipient=recipient,
                watcher_notice=notice,
            )
        if notice.status == "blocked" and notice.reason_code in _BLOCKED_REASONS:
            return self._project_watcher_blocked(notice=notice, recipient=recipient)
        return None

    def project_candidate_alert(
        self,
        *,
        candidate: Candidate,
        assessment: SetupAssessment,
        window: CanonicalEvidenceWindowV1,
        recipient: PaperAlertRecipient,
        watcher_notice: WatcherScanNotice | None = None,
    ) -> PaperNotificationProjection | None:
        self._require_enabled()
        self._require_recipient_org(recipient, candidate.organization_id)
        require_active_binding(self._protocol.store, _as_candidate_recipient(recipient))
        alert = self._candidates.project_canonical_candidate(
            candidate=candidate,
            assessment=assessment,
            window=window,
            recipient=_as_candidate_recipient(recipient),
            nested=None if watcher_notice is None else watcher_notice.nested,
        )
        if alert is None:
            return None
        if alert.intent.content.nested is not None:
            # One informational outbox event; no action confirmations or second footer.
            intent = self._notification(
                recipient=recipient,
                kind=PaperNotificationKind.WATCHER_CONFIRMED_SETUP,
                resource_type=PAPER_RESOURCE_CANDIDATE,
                resource_id=alert.intent.content.nested.setup_id,
                content_hash=alert.intent.identity_hash,
                text=alert.outbox.text,
                candidate_id=alert.intent.candidate_id,
            )
            persisted = self._store.get_or_insert_notification(intent)
            thread = self._thread_for(
                recipient=recipient,
                resource_type=PAPER_RESOURCE_CANDIDATE,
                resource_id=alert.intent.candidate_id,
                notification_id=persisted.intent_id,
            )
            return PaperNotificationProjection(
                intent=persisted,
                outbox=alert.outbox,
                thread=thread,
                candidate_alert=alert,
                converged=alert.converged,
            )
        watcher_setup = watcher_notice is not None and watcher_notice.published
        kind = (
            PaperNotificationKind.WATCHER_CONFIRMED_SETUP
            if watcher_setup
            else PaperNotificationKind.CANDIDATE_ACTIVE
        )
        intent = self._notification(
            recipient=recipient,
            kind=kind,
            resource_type=PAPER_RESOURCE_CANDIDATE,
            resource_id=candidate.candidate_id,
            content_hash=candidate.content_hash,
            text=alert.outbox.text if watcher_setup else format_confirmed_setup_footer(),
            candidate_id=candidate.candidate_id,
            watcher_lineage_id=None if watcher_notice is None else watcher_notice.lineage_id,
            watcher_reason_code=None if watcher_notice is None else watcher_notice.reason_code,
        )
        prior = self._store.get_notification_by_hash(intent.identity_hash)
        persisted = self._store.get_or_insert_notification(intent)
        thread = self._thread_for(
            recipient=recipient,
            resource_type=PAPER_RESOURCE_CANDIDATE,
            resource_id=candidate.candidate_id,
            notification_id=persisted.intent_id,
        )
        if watcher_setup:
            # The setup alert is the only message. Telegram presents no paper action
            # for a Watcher setup, so no confirmation row or nonce exists and any
            # "I confirm ..." reply fails closed. Replies still discuss this thread.
            return PaperNotificationProjection(
                intent=persisted,
                outbox=alert.outbox,
                thread=thread,
                candidate_alert=alert,
                converged=prior is not None or alert.converged,
            )
        confirmations = self._present_candidate_actions(
            thread=thread, recipient=recipient, intent=alert.intent
        )
        existing_thread_outbox = self._protocol.store.get_outbox_by_idempotency(
            organization_id=recipient.organization_id,
            idempotency_key=_thread_key(persisted.identity_hash),
        )
        footer = format_confirmed_setup_footer()
        if confirmations:
            footer = f"{footer}\n\n{format_paper_confirmation(confirmations[0])}"
        outbox = self._protocol.enqueue_outbound(
            organization_id=recipient.organization_id,
            user_id=recipient.user_id,
            bot_id=recipient.bot_id,
            chat_id=recipient.chat_id,
            text=footer,
            idempotency_key=_thread_key(persisted.identity_hash),
            binding_id=recipient.binding_id,
            notification_event=(
                alert.outbox.notification_event.model_copy(
                    update={"duplicate_key": f"{alert.intent.identity_hash}:footer"}
                )
                if alert.outbox.notification_event is not None
                else None
            ),
        )
        return PaperNotificationProjection(
            intent=persisted,
            outbox=outbox,
            thread=thread,
            candidate_alert=alert,
            confirmations=confirmations,
            converged=prior is not None or existing_thread_outbox is not None,
        )

    def project_journal_outcome(
        self, *, view: JournalOutcomeView, recipient: PaperAlertRecipient
    ) -> PaperNotificationProjection:
        self._require_enabled()
        require_active_binding(self._protocol.store, _as_candidate_recipient(recipient))
        text = format_journal_outcome_text(view)
        digest = canonical_sha256(
            {
                "trade_id": str(view.trade_id),
                "status": view.status,
                "result": view.result,
                "net_pnl": view.net_pnl,
            }
        )
        intent = self._notification(
            recipient=recipient,
            kind=PaperNotificationKind.JOURNAL_OUTCOME,
            resource_type=PAPER_RESOURCE_JOURNAL,
            resource_id=view.trade_id,
            content_hash=digest,
            text=text,
        )
        return self._project_simple(
            intent=intent, recipient=recipient, text=text, notification_symbol=view.symbol
        )

    def handle_inbound_message(
        self,
        *,
        identity: MessageIdentity,
        inbound: TelegramInboundUpdate,
        text: str,
        candidate: Candidate | None = None,
        assessment: SetupAssessment | None = None,
        window: CanonicalEvidenceWindowV1 | None = None,
    ) -> PaperDiscussionResult:
        self._require_enabled()
        discussion_intent = classify_inbound_text(text)
        refusal = refusal_for(discussion_intent)
        presented: ActionPayload | None = None
        if discussion_intent is DiscussionIntent.CONFIRM_MUTATION:
            presented = self._resolve_confirm_payload(identity=identity, text=text)
        outcome = self._protocol.receive_private_message(
            identity=identity,
            inbound=inbound,
            text=text,
            presented_payload=presented,
        )
        thread = self._thread_for_identity(identity, candidate=candidate)
        if outcome.replayed:
            return PaperDiscussionResult(
                intent=discussion_intent,
                telegram_outcome=outcome,
                thread=thread,
                refused=refusal is not None,
                refusal_reason=refusal,
            )
        self._append(
            thread=thread,
            role="user",
            intent=discussion_intent,
            content=text,
            receipt_id=outcome.receipt.receipt_id,
        )
        if refusal is not None:
            return self._refuse(
                thread=thread,
                outcome=outcome,
                intent=discussion_intent,
                reason=refusal,
            )
        if discussion_intent is DiscussionIntent.CONFIRM_MUTATION:
            return self._handle_confirmed_mutation(
                thread=thread,
                identity=identity,
                presented=presented,
                outcome=outcome,
            )
        reply_text = self._discussion_reply(
            intent=discussion_intent,
            candidate=candidate,
            assessment=assessment,
            window=window,
            thread=thread,
            recipient_org=thread.organization_id,
            recipient_user=thread.user_id,
        )
        if discussion_intent in {
            DiscussionIntent.REQUEST_REJECT,
            DiscussionIntent.REQUEST_SKIP,
            DiscussionIntent.REQUEST_APPROVE,
        }:
            reply_text = self._present_requested_action(
                thread=thread,
                identity=identity,
                intent=discussion_intent,
                candidate=candidate,
            )
        reply = self._reply(
            thread=thread,
            identity=identity,
            text=reply_text,
            intent=discussion_intent,
        )
        return PaperDiscussionResult(
            intent=discussion_intent,
            telegram_outcome=outcome,
            thread=thread,
            reply_outbox=reply,
        )

    def handle_callback(
        self,
        *,
        identity: CallbackIdentity,
        nonce_token: str,
        presented_payload: ActionPayload,
        inbound: TelegramInboundUpdate,
    ) -> PaperDiscussionResult:
        self._require_enabled()
        result = self._candidates.handle_callback(
            identity=identity,
            nonce_token=nonce_token,
            presented_payload=presented_payload,
            inbound=inbound,
        )
        thread = self._thread_for(
            recipient=PaperAlertRecipient(
                organization_id=presented_payload.organization_id,
                user_id=presented_payload.user_id,
                account_id=presented_payload.account_id,
                binding_id=_require_binding_id(result.telegram_outcome.receipt.binding_id),
                bot_id=identity.bot_id,
                chat_id=identity.chat_id,
            ),
            resource_type=presented_payload.resource_type,
            resource_id=presented_payload.resource_id,
            notification_id=None,
        )
        return PaperDiscussionResult(
            intent=DiscussionIntent.CONFIRM_MUTATION,
            telegram_outcome=result.telegram_outcome,
            thread=thread,
            candidate_result=result,
        )

    def deliver_pending(self, *, limit: int = 10) -> list[object]:
        self._require_enabled()
        return list(self._protocol.deliver_pending(limit=limit))

    def _handle_confirmed_mutation(
        self,
        *,
        thread: PaperThread,
        identity: MessageIdentity,
        presented: ActionPayload | None,
        outcome: ActionOutcome,
    ) -> PaperDiscussionResult:
        if presented is None:
            return self._refuse(
                thread=thread,
                outcome=outcome,
                intent=DiscussionIntent.CONFIRM_MUTATION,
                reason="Telegram can't change this setup. Nothing was changed.",
            )
        if outcome.receipt.state is not ActionReceiptState.APPLIED:
            return self._refuse(
                thread=thread,
                outcome=outcome,
                intent=DiscussionIntent.CONFIRM_MUTATION,
                reason="That confirmation didn't go through. Nothing was changed.",
            )
        candidate_result = None
        if presented.resource_type == PAPER_RESOURCE_CANDIDATE:
            candidate_result = self._candidates.apply_telegram_outcome(
                presented_payload=presented, outcome=outcome
            )
        self._consume_presented(thread=thread, presented=presented)
        reply = self._reply(
            thread=thread,
            identity=identity,
            text=f"{presented.action.value.capitalize()} recorded. No order was placed.",
            intent=DiscussionIntent.CONFIRM_MUTATION,
        )
        return PaperDiscussionResult(
            intent=DiscussionIntent.CONFIRM_MUTATION,
            telegram_outcome=outcome,
            thread=thread,
            reply_outbox=reply,
            candidate_result=candidate_result,
        )

    def _consume_presented(self, *, thread: PaperThread, presented: ActionPayload) -> None:
        digest = payload_binding_hash(presented)
        matches = self._store.presented_for_binding_action(
            organization_id=thread.organization_id,
            binding_id=thread.binding_id,
            action=presented.action,
        )
        now = self._clock.now()
        for row in matches:
            if row.payload_hash == digest:
                self._store.consume_confirmation(row.model_copy(update={"consumed_at": now}))
                return

    def _project_watcher_blocked(
        self, *, notice: WatcherScanNotice, recipient: PaperAlertRecipient
    ) -> PaperNotificationProjection:
        require_active_binding(self._protocol.store, _as_candidate_recipient(recipient))
        resource_id = notice.lineage_id or uuid5(
            PAPER_NOTIFY_IDENTITY_NAMESPACE,
            f"{notice.scan_scope}:{notice.reason_code}:{notice.request_hash or 'none'}",
        )
        content_hash = notice.request_hash or canonical_sha256(
            {
                "reason": notice.reason_code,
                "scope": notice.scan_scope,
                "status": notice.status,
            }
        )
        text = format_watcher_blocked_text(notice)
        intent = self._notification(
            recipient=recipient,
            kind=PaperNotificationKind.WATCHER_SCAN_BLOCKED,
            resource_type=PAPER_RESOURCE_WATCHER,
            resource_id=resource_id,
            content_hash=content_hash,
            text=text,
            watcher_lineage_id=notice.lineage_id,
            watcher_reason_code=notice.reason_code,
        )
        return self._project_simple(
            intent=intent,
            recipient=recipient,
            text=text,
            resource_type=PAPER_RESOURCE_WATCHER,
            resource_id=resource_id,
            notification_symbol=notice.symbol,
        )

    def _project_simple(
        self,
        *,
        intent: PaperNotificationIntent,
        recipient: PaperAlertRecipient,
        text: str,
        resource_type: str | None = None,
        resource_id: UUID | None = None,
        notification_symbol: str | None = None,
    ) -> PaperNotificationProjection:
        prior = self._store.get_notification_by_hash(intent.identity_hash)
        persisted = self._store.get_or_insert_notification(intent)
        existing = self._protocol.store.get_outbox_by_idempotency(
            organization_id=recipient.organization_id,
            idempotency_key=_notify_key(persisted.identity_hash),
        )
        outbox = self._protocol.enqueue_outbound(
            organization_id=recipient.organization_id,
            user_id=recipient.user_id,
            bot_id=recipient.bot_id,
            chat_id=recipient.chat_id,
            text=text,
            idempotency_key=_notify_key(persisted.identity_hash),
            binding_id=recipient.binding_id,
            notification_event=_paper_notification_event(persisted, symbol=notification_symbol),
        )
        thread = self._thread_for(
            recipient=recipient,
            resource_type=resource_type or persisted.resource_type,
            resource_id=resource_id or persisted.resource_id,
            notification_id=persisted.intent_id,
        )
        return PaperNotificationProjection(
            intent=persisted,
            outbox=outbox,
            thread=thread,
            converged=prior is not None or existing is not None,
        )

    def _notification(
        self,
        *,
        recipient: PaperAlertRecipient,
        kind: PaperNotificationKind,
        resource_type: str,
        resource_id: UUID,
        content_hash: str,
        text: str,
        candidate_id: UUID | None = None,
        watcher_lineage_id: UUID | None = None,
        watcher_reason_code: str | None = None,
    ) -> PaperNotificationIntent:
        intent_id, identity_hash = build_paper_notification_identity(
            organization_id=recipient.organization_id,
            user_id=recipient.user_id,
            account_id=recipient.account_id,
            kind=kind,
            resource_type=resource_type,
            resource_id=resource_id,
            content_hash=content_hash,
        )
        return PaperNotificationIntent(
            intent_id=intent_id,
            identity_hash=identity_hash,
            organization_id=recipient.organization_id,
            user_id=recipient.user_id,
            account_id=recipient.account_id,
            kind=kind,
            resource_type=resource_type,
            resource_id=resource_id,
            content_hash=content_hash,
            telegram_revision_id=paper_telegram_revision_id(
                resource_id=resource_id, content_hash=content_hash
            ),
            candidate_id=candidate_id,
            watcher_lineage_id=watcher_lineage_id,
            watcher_reason_code=watcher_reason_code,
            text=text,
            created_at=self._clock.now(),
        )

    def _thread_for(
        self,
        *,
        recipient: PaperAlertRecipient,
        resource_type: str,
        resource_id: UUID | None,
        notification_id: UUID | None,
    ) -> PaperThread:
        now = self._clock.now()
        thread = PaperThread(
            thread_id=paper_thread_id(
                organization_id=recipient.organization_id,
                binding_id=recipient.binding_id,
                resource_type=resource_type,
                resource_id=resource_id,
            ),
            organization_id=recipient.organization_id,
            user_id=recipient.user_id,
            account_id=recipient.account_id,
            binding_id=recipient.binding_id,
            bot_id=recipient.bot_id,
            chat_id=recipient.chat_id,
            resource_type=resource_type,
            resource_id=resource_id,
            notification_id=notification_id,
            created_at=now,
            updated_at=now,
        )
        return self._store.get_or_insert_thread(thread)

    def _thread_for_identity(
        self, identity: MessageIdentity, *, candidate: Candidate | None
    ) -> PaperThread:
        binding = self._protocol.store.get_active_binding_for_telegram_user(
            bot_id=identity.bot_id, telegram_user_id=identity.telegram_user_id
        )
        if binding is None:
            raise PaperTelegramTenantError("No verified Telegram binding for this chat.")
        if binding.organization_id is None:
            raise PaperTelegramTenantError("Binding is missing organization scope.")
        resource_type = PAPER_RESOURCE_CANDIDATE if candidate is not None else _CHAT_THREAD_RESOURCE
        resource_id = None if candidate is None else candidate.candidate_id
        now = self._clock.now()
        thread = PaperThread(
            thread_id=paper_thread_id(
                organization_id=binding.organization_id,
                binding_id=binding.binding_id,
                resource_type=resource_type,
                resource_id=resource_id,
            ),
            organization_id=binding.organization_id,
            user_id=binding.user_id,
            account_id=uuid5(PAPER_NOTIFY_IDENTITY_NAMESPACE, f"acct:{binding.binding_id}"),
            binding_id=binding.binding_id,
            bot_id=binding.bot_id,
            chat_id=binding.chat_id,
            resource_type=resource_type,
            resource_id=resource_id,
            created_at=now,
            updated_at=now,
        )
        return self._store.get_or_insert_thread(thread)

    def _present_candidate_actions(
        self,
        *,
        thread: PaperThread,
        recipient: PaperAlertRecipient,
        intent: CandidateAlertIntent | object,
    ) -> tuple[PresentedPaperConfirmation, ...]:
        if not isinstance(intent, CandidateAlertIntent):
            return ()
        presented: list[PresentedPaperConfirmation] = []
        for action in (
            TelegramRemoteAction.REJECT,
            TelegramRemoteAction.SKIP,
            TelegramRemoteAction.APPROVE,
        ):
            payload = self._candidates.action_payload(intent, action)
            self._candidates.issue_action_nonce(
                binding_id=recipient.binding_id, intent=intent, action=action
            )
            row = PresentedPaperConfirmation(
                confirmation_id=paper_confirmation_id(
                    thread_id=thread.thread_id,
                    action=action.value,
                    payload_hash=payload_binding_hash(payload),
                ),
                thread_id=thread.thread_id,
                organization_id=recipient.organization_id,
                user_id=recipient.user_id,
                account_id=recipient.account_id,
                action=action,
                resource_type=payload.resource_type,
                resource_id=payload.resource_id,
                content_hash=payload.content_hash,
                revision_id=payload.revision_id,
                payload_hash=payload_binding_hash(payload),
                presented_at=self._clock.now(),
            )
            presented.append(self._store.save_presented_confirmation(row))
        return tuple(presented)

    def _present_requested_action(
        self,
        *,
        thread: PaperThread,
        identity: MessageIdentity,
        intent: DiscussionIntent,
        candidate: Candidate | None,
    ) -> str:
        del identity
        if candidate is None:
            return "There is no open setup to act on right now."
        action = {
            DiscussionIntent.REQUEST_REJECT: TelegramRemoteAction.REJECT,
            DiscussionIntent.REQUEST_SKIP: TelegramRemoteAction.SKIP,
            DiscussionIntent.REQUEST_APPROVE: TelegramRemoteAction.APPROVE,
        }[intent]
        matches = [
            row
            for row in self._store.presented_for_binding_action(
                organization_id=thread.organization_id,
                binding_id=thread.binding_id,
                action=action,
            )
            if row.thread_id == thread.thread_id
        ]
        if len(matches) == 1:
            return (
                f"{action.value} remains confirmation-gated.\n"
                f"{format_paper_confirmation(matches[0])}"
            )
        return (
            f"Telegram can't {action.value.lower()} this setup. "
            "You can still ask me anything about it."
        )

    def _resolve_confirm_payload(
        self, *, identity: MessageIdentity, text: str
    ) -> ActionPayload | None:
        parsed = parse_paper_confirmation_payload(text)
        if parsed is not None:
            if parsed.action is TelegramRemoteAction.CLOSE:
                raise PaperActionRefusedError("Telegram CLOSE is unavailable.")
            allowed = (
                parsed.action in _MUTATING_ACTIONS or parsed.action in READ_ONLY_TELEGRAM_ACTIONS
            )
            if not allowed:
                raise PaperActionRefusedError(
                    f"Telegram action {parsed.action.value} is not a paper confirmation."
                )
            return parsed
        binding = self._protocol.store.get_active_binding_for_telegram_user(
            bot_id=identity.bot_id, telegram_user_id=identity.telegram_user_id
        )
        if binding is None:
            return None
        named = _named_confirm_action(text)
        if named is not None:
            matches = self._store.presented_for_binding_action(
                organization_id=binding.organization_id,
                binding_id=binding.binding_id,
                action=named,
            )
            if len(matches) == 1:
                return payload_from_presented(matches[0])
            return None
        return None

    def _discussion_reply(
        self,
        *,
        intent: DiscussionIntent,
        candidate: Candidate | None,
        assessment: SetupAssessment | None,
        window: CanonicalEvidenceWindowV1 | None,
        thread: PaperThread,
        recipient_org: UUID,
        recipient_user: UUID,
    ) -> str:
        del thread
        if intent is DiscussionIntent.PAPER_TRADE_STATUS:
            return format_paper_status_text(
                self._context.paper_trade_status(
                    organization_id=recipient_org, user_id=recipient_user
                )
            )
        if intent is DiscussionIntent.JOURNAL_OUTCOME:
            journal = self._context.journal_outcome(
                organization_id=recipient_org,
                user_id=recipient_user,
                trade_id=None,
            )
            if journal is None:
                return "No closed paper trades yet."
            return format_journal_outcome_text(journal)
        if intent is DiscussionIntent.LEARNING_SUMMARY:
            learning = self._context.learning_summary(
                organization_id=recipient_org,
                user_id=recipient_user,
                candidate_id=None if candidate is None else candidate.candidate_id,
            )
            if learning is None:
                return "No learning results yet."
            return format_learning_summary_text(learning)
        if candidate is not None and assessment is None:
            if intent in {
                DiscussionIntent.EXPLAIN_STRATEGY,
                DiscussionIntent.STRATEGY_DISCUSSION,
            }:
                draft = self._context.strategy_discussion(
                    organization_id=recipient_org,
                    user_id=recipient_user,
                    strategy_id=candidate.strategy_version_id,
                )
                return explain_strategy(candidate=candidate, draft=draft)
            return explain_persisted_candidate(candidate=candidate)
        if candidate is None or assessment is None:
            if intent is DiscussionIntent.STRATEGY_DISCUSSION:
                draft = self._context.strategy_discussion(
                    organization_id=recipient_org, user_id=recipient_user, strategy_id=None
                )
                if draft is None:
                    return (
                        "Strategy discussion is open. Telegram cannot approve, compile, "
                        "or activate a strategy."
                    )
                return (
                    f"{draft.summary}\ncompiled={draft.compiled} approved={draft.approved}. "
                    "Confirmation would still not compile or approve."
                )
            if intent is DiscussionIntent.MARKET_CONTEXT:
                return "Market context requires a bound Candidate evidence window."
            return (
                "I can discuss paper Watcher alerts, Candidates, strategy, market context, "
                "paper status, journal outcomes, and learning summaries. "
                "No Telegram message is trading authority."
            )
        if intent is DiscussionIntent.EXPLAIN_EVIDENCE and window is not None:
            return explain_evidence(window=window, assessment=assessment)
        if intent is DiscussionIntent.EXPLAIN_RISK:
            return explain_risk(candidate=candidate, assessment=assessment)
        if intent is DiscussionIntent.EXPLAIN_STRATEGY:
            draft = self._context.strategy_discussion(
                organization_id=recipient_org,
                user_id=recipient_user,
                strategy_id=candidate.strategy_version_id,
            )
            return explain_strategy(candidate=candidate, draft=draft)
        if intent is DiscussionIntent.MARKET_CONTEXT and window is not None:
            return explain_market_context(window=window)
        if intent in {
            DiscussionIntent.EXPLAIN_CANDIDATE,
            DiscussionIntent.UNKNOWN,
            DiscussionIntent.STRATEGY_DISCUSSION,
        }:
            content = None
            return explain_candidate(candidate=candidate, content=content)
        if window is not None:
            return explain_evidence(window=window, assessment=assessment)
        return explain_candidate(candidate=candidate, content=None)

    def _reply(
        self,
        *,
        thread: PaperThread,
        identity: MessageIdentity,
        text: str,
        intent: DiscussionIntent,
    ) -> OutboxRecord:
        outbox = self._protocol.enqueue_outbound(
            organization_id=thread.organization_id,
            user_id=thread.user_id,
            bot_id=identity.bot_id,
            chat_id=identity.chat_id,
            text=text,
            idempotency_key=f"paper-reply:{outcome_key(identity.update_id, intent.value)}",
            binding_id=thread.binding_id,
        )
        self._append(
            thread=thread,
            role="assistant",
            intent=intent,
            content=text,
            receipt_id=None,
        )
        return outbox

    def _append(
        self,
        *,
        thread: PaperThread,
        role: str,
        intent: DiscussionIntent,
        content: str,
        receipt_id: UUID | None,
    ) -> None:
        now = self._clock.now()
        self._store.append_message(
            PaperThreadMessage(
                message_id=uuid4(),
                thread_id=thread.thread_id,
                organization_id=thread.organization_id,
                user_id=thread.user_id,
                role="user" if role == "user" else "assistant",
                intent=intent,
                content=content[:4096],
                receipt_id=receipt_id,
                created_at=now,
            )
        )
        self._store.touch_thread(thread.model_copy(update={"updated_at": now}))

    def _refuse(
        self,
        *,
        thread: PaperThread,
        outcome: ActionOutcome,
        intent: DiscussionIntent,
        reason: str,
    ) -> PaperDiscussionResult:
        reply = self._protocol.enqueue_outbound(
            organization_id=thread.organization_id,
            user_id=thread.user_id,
            bot_id=thread.bot_id,
            chat_id=thread.chat_id,
            text=reason,
            idempotency_key=f"paper-refuse:{outcome.receipt.receipt_id}",
            binding_id=thread.binding_id,
        )
        self._append(
            thread=thread,
            role="assistant",
            intent=intent,
            content=reason,
            receipt_id=None,
        )
        return PaperDiscussionResult(
            intent=intent,
            telegram_outcome=outcome,
            thread=thread,
            reply_outbox=reply,
            refused=True,
            refusal_reason=reason,
        )

    def _require_enabled(self) -> None:
        if not self._enabled or not self._protocol.enabled:
            raise PaperTelegramDisabledError()

    def _require_recipient_org(self, recipient: PaperAlertRecipient, organization_id: UUID) -> None:
        if recipient.organization_id != organization_id:
            raise PaperTelegramTenantError("Recipient organization does not match the event.")


def _as_candidate_recipient(recipient: PaperAlertRecipient) -> CandidateAlertRecipient:
    return CandidateAlertRecipient(
        organization_id=recipient.organization_id,
        user_id=recipient.user_id,
        account_id=recipient.account_id,
        binding_id=recipient.binding_id,
        bot_id=recipient.bot_id,
        chat_id=recipient.chat_id,
    )


def _require_binding_id(binding_id: UUID | None) -> UUID:
    if binding_id is None:
        raise PaperTelegramTenantError("Telegram receipt is missing binding id.")
    return binding_id


def _notify_key(identity_hash: str) -> str:
    return f"{PAPER_NOTIFY_OUTBOX_PREFIX}{identity_hash}"[:128]


def _paper_notification_event(intent: PaperNotificationIntent, *, symbol: str | None):
    from app.schemas.telegram_policy import (
        NotificationEventType,
        NotificationSeverity,
        TelegramNotificationEvent,
    )

    blocked = intent.kind == PaperNotificationKind.WATCHER_SCAN_BLOCKED
    return TelegramNotificationEvent(
        event_type=NotificationEventType.RISK
        if blocked
        else NotificationEventType.PAPER_TRADE_CLOSED,
        severity=NotificationSeverity.WATCH if blocked else NotificationSeverity.INFO,
        symbol=symbol,
        duplicate_key=intent.identity_hash,
        occurred_at=intent.created_at,
    )


def _thread_key(identity_hash: str) -> str:
    return f"{PAPER_THREAD_OUTBOX_PREFIX}{identity_hash}"[:128]


def outcome_key(update_id: int, intent: str) -> str:
    return f"{update_id}:{intent}"


def _named_confirm_action(text: str) -> TelegramRemoteAction | None:
    lowered = text.strip().lower()
    if lowered.startswith("i confirm reject"):
        return TelegramRemoteAction.REJECT
    if lowered.startswith("i confirm skip"):
        return TelegramRemoteAction.SKIP
    if lowered.startswith("i confirm approve"):
        return TelegramRemoteAction.APPROVE
    return None
