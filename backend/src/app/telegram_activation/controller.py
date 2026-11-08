"""Paper Telegram activation controller. Default state is disarmed."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.core.config import Settings, TelegramInboundMode
from app.signal_fusion.candidate import Candidate
from app.telegram_activation.contracts import (
    InboundSourceKind,
    OutboxCounts,
    PollResult,
    PreflightReport,
    WebhookAccept,
)
from app.telegram_activation.cursor import (
    ActivationCursorStore,
    InboundCursor,
    InMemoryActivationCursorStore,
)
from app.telegram_activation.delivery import deliver_due
from app.telegram_activation.errors import TelegramActivationError
from app.telegram_activation.intake import (
    ParsedTelegramUpdate,
    TelegramUpdateSource,
    parse_webhook_body,
    secret_matches,
)
from app.telegram_activation.preflight import run_preflight
from app.telegram_paper_agent.contracts import PaperAlertRecipient
from app.telegram_paper_agent.errors import PaperTelegramDisabledError, PaperTelegramError
from app.telegram_paper_agent.gateway import TelegramPaperAgent
from app.telegram_security.actions import MAX_INBOUND_UPDATE_BYTES, TelegramRemoteAction
from app.telegram_security.clock import Clock
from app.telegram_security.contracts import (
    ActionNonce,
    ActionPayload,
    CallbackIdentity,
    DeliveryAttempt,
    MessageIdentity,
    OutboxState,
    ProtocolAuditEvent,
    TelegramInboundUpdate,
)
from app.telegram_security.errors import (
    TelegramRateLimitedError,
    TelegramSecurityError,
    TelegramSecurityReason,
)
from app.telegram_security.hashing import hash_secret, payload_binding_hash

if TYPE_CHECKING:
    from app.workers.watcher_paper import WatcherPaperScanReport


class TelegramPaperActivation:
    """Controlled paper path from a bound recipient to discussion.

    ``arm`` does not flip settings, enable Watcher, or open a network client.
    """

    def __init__(
        self,
        *,
        settings: Settings,
        agent: TelegramPaperAgent,
        recipient: PaperAlertRecipient,
        clock: Clock,
        outbound_ready: bool,
        update_source: TelegramUpdateSource | None = None,
        cursor_store: ActivationCursorStore | None = None,
        candidate_loader: Callable[[UUID], Candidate | None] | None = None,
    ) -> None:
        self._settings = settings
        self._agent = agent
        self._recipient = recipient
        self._clock = clock
        self._outbound_ready = outbound_ready
        self._source = update_source
        self._cursors = cursor_store or InMemoryActivationCursorStore()
        self._candidate_loader = candidate_loader
        self._armed = False
        self._webhook_mounted = False

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def webhook_mounted(self) -> bool:
        return self._webhook_mounted

    @property
    def cursor_store(self) -> ActivationCursorStore:
        return self._cursors

    @property
    def recipient_organization_id(self) -> UUID:
        return self._recipient.organization_id

    def mark_webhook_mounted(self) -> None:
        self._webhook_mounted = True

    def preflight(self) -> PreflightReport:
        source_kind = InboundSourceKind.REFUSING if self._source is None else self._source.kind
        cursor = self._cursors.get_cursor(bot_id=self._recipient.bot_id)
        report = run_preflight(
            settings=self._settings,
            store=self._agent.protocol.store,
            recipient=self._recipient,
            protocol_enabled=self._agent.protocol.enabled,
            outbound_ready=self._outbound_ready,
            source_kind=source_kind,
            webhook_mounted=self._webhook_mounted,
            inbound_cursor_present=cursor is not None,
        )
        return report.model_copy(update={"outbox": self._outbox_counts()})

    def arm(self) -> PreflightReport:
        """Require a passing preflight. Does not change environment variables."""
        report = self.preflight()
        if not report.runtime_armable:
            self._audit("activation_arm_refused", reason="preflight_blocked")
            raise TelegramActivationError(
                "Paper Telegram activation preflight failed.",
                reason="preflight_blocked",
            )
        self._armed = True
        self._audit("activation_armed")
        return self.preflight()

    def disarm(self) -> PreflightReport:
        """In-process rollback. Does not edit env, revoke bindings, or delete audits."""
        self._armed = False
        self._webhook_mounted = False
        self._audit("activation_disarmed")
        return self.preflight()

    def deliver(self, *, limit: int = 10) -> list[DeliveryAttempt]:
        self._require_armed()
        return deliver_due(
            self._agent.protocol,
            organization_id=self._recipient.organization_id,
            limit=limit,
        )

    def poll_once(self, *, limit: int = 20) -> PollResult:
        self._require_armed()
        if self._settings.telegram_inbound_mode is not TelegramInboundMode.POLLING:
            raise TelegramActivationError(
                "Polling is not the configured inbound mode.",
                reason="inbound_mode",
            )
        if self._source is None:
            raise TelegramActivationError(
                "No polling source is configured.",
                reason="polling_source_disabled",
            )
        cursor = self._cursors.get_cursor(bot_id=self._recipient.bot_id)
        offset = None if cursor is None else cursor.last_update_id + 1
        batch = self._source.fetch(offset=offset, limit=limit)
        result = PollResult(fetched=len(batch))
        for update in batch:
            disposition = self._consume(update)
            result = _count(result, disposition)
            if disposition == "deferred":
                break
        return result

    def accept_webhook(self, *, raw_body: bytes, secret_header: str | None) -> WebhookAccept:
        if len(raw_body) > MAX_INBOUND_UPDATE_BYTES:
            return WebhookAccept(
                status_code=413,
                disposition="rejected",
                reason="update_too_large",
            )
        if not self._armed:
            return WebhookAccept(status_code=503, disposition="deferred", reason="not_armed")
        if self._settings.telegram_inbound_mode is not TelegramInboundMode.WEBHOOK:
            return WebhookAccept(status_code=503, disposition="deferred", reason="inbound_mode")
        if not secret_matches(
            configured=self._settings.telegram_webhook_secret,
            presented=secret_header,
        ):
            self._audit("webhook_rejected", reason="secret_mismatch")
            return WebhookAccept(status_code=401, disposition="rejected", reason="secret_mismatch")
        parsed = parse_webhook_body(raw_body)
        if parsed.rejection == "update_too_large":
            return WebhookAccept(
                status_code=413,
                disposition="rejected",
                reason="update_too_large",
            )
        disposition = self._consume(parsed)
        if disposition == "deferred":
            return WebhookAccept(status_code=503, disposition="deferred", reason="rate_limited")
        if parsed.kind == "rejected":
            return WebhookAccept(
                status_code=200,
                disposition="rejected",
                reason=parsed.rejection,
            )
        return WebhookAccept(status_code=200, disposition=disposition)

    def paper_scan_hook(self) -> Callable[[WatcherPaperScanReport], None]:
        """Return a worker hook. This does not install it on the Watcher runtime."""
        from app.paper_interaction.bridge import telegram_scan_hook

        if not self._armed:
            raise TelegramActivationError(
                "The scan hook is available only while activation is armed.",
                reason="not_armed",
            )
        return telegram_scan_hook(self._agent, self._recipient)

    def _consume(self, update: ParsedTelegramUpdate) -> str:
        try:
            return self._apply_update(update)
        except TelegramRateLimitedError:
            self._audit("inbound_deferred", reason="rate_limited")
            return "deferred"

    def _apply_update(self, update: ParsedTelegramUpdate) -> str:
        if update.kind == "rejected" or update.rejection is not None:
            if update.update_id > 0:
                self._advance(update.update_id)
            self._audit("inbound_rejected", reason=update.rejection or "rejected")
            return "rejected"
        if update.chat_id != self._recipient.chat_id:
            self._advance(update.update_id)
            self._audit("inbound_rejected", reason="recipient_mismatch")
            return "rejected"
        try:
            if update.kind == "message":
                outcome = self._agent.handle_inbound_message(
                    identity=_message_identity(self._recipient.bot_id, update),
                    inbound=TelegramInboundUpdate(
                        update_type="message", body_size=update.body_size
                    ),
                    text=update.text,
                    candidate=self._load_candidate(),
                )
                replayed = outcome.telegram_outcome.replayed
            else:
                outcome = self._agent.handle_callback(
                    identity=_callback_identity(self._recipient.bot_id, update),
                    nonce_token=update.callback_data or "",
                    presented_payload=self._payload_for_callback(update),
                    inbound=TelegramInboundUpdate(
                        update_type="callback_query",
                        body_size=update.body_size,
                    ),
                )
                replayed = outcome.telegram_outcome.replayed
        except TelegramRateLimitedError:
            raise
        except TelegramSecurityError as exc:
            self._reject_update(update, reason=exc.reason.value)
            return "rejected"
        except TelegramActivationError as exc:
            self._reject_update(update, reason=exc.reason)
            return "rejected"
        except PaperTelegramDisabledError:
            # Agent-wide state, not a bad update: keep the cursor so it is retried.
            raise
        except PaperTelegramError as exc:
            self._reject_update(update, reason=type(exc).__name__)
            return "rejected"
        self._advance(update.update_id)
        disposition = "replayed" if replayed else "applied"
        self._audit("inbound_applied" if disposition == "applied" else "inbound_replayed")
        return disposition

    def _payload_for_callback(self, update: ParsedTelegramUpdate) -> ActionPayload:
        token = update.callback_data or ""
        nonce = self._agent.protocol.store.get_nonce_by_hash(hash_secret(token))
        if nonce is None:
            raise TelegramSecurityError(
                "Callback nonce was not found.",
                reason=TelegramSecurityReason.NONCE_NOT_FOUND,
            )
        if (
            nonce.organization_id != self._recipient.organization_id
            or nonce.bot_id != self._recipient.bot_id
            or nonce.chat_id != update.chat_id
        ):
            raise TelegramSecurityError(
                "Callback nonce does not match the bound recipient.",
                reason=TelegramSecurityReason.CROSS_ORGANIZATION,
            )
        return _payload_from_nonce(nonce)

    def _reject_update(self, update: ParsedTelegramUpdate, *, reason: str) -> None:
        if update.update_id > 0:
            self._advance(update.update_id)
        self._audit("inbound_rejected", reason=reason)

    def _advance(self, update_id: int) -> None:
        self._cursors.save_cursor(
            InboundCursor(
                bot_id=self._recipient.bot_id,
                organization_id=self._recipient.organization_id,
                last_update_id=update_id,
                updated_at=self._clock.now(),
            )
        )

    def _load_candidate(self) -> Candidate | None:
        if self._candidate_loader is None:
            return None
        return self._candidate_loader(self._recipient.organization_id)

    def _require_armed(self) -> None:
        if not self._armed:
            raise TelegramActivationError(
                "Paper Telegram activation is not armed.",
                reason="not_armed",
            )

    def _audit(self, event_type: str, *, reason: str | None = None) -> None:
        self._agent.protocol.store.append_audit(
            ProtocolAuditEvent(
                event_id=uuid4(),
                at=self._clock.now(),
                event_type=event_type,
                organization_id=self._recipient.organization_id,
                user_id=self._recipient.user_id,
                reason_code=reason,
            )
        )

    def _outbox_counts(self) -> OutboxCounts:
        rows = self._agent.protocol.store.list_outbox(
            organization_id=self._recipient.organization_id,
            limit=1000,
        )
        counts = dict.fromkeys(OutboxState, 0)
        for row in rows:
            counts[row.state] = counts.get(row.state, 0) + 1
        return OutboxCounts(
            pending=counts[OutboxState.PENDING],
            claimed=counts[OutboxState.CLAIMED],
            sent=counts[OutboxState.SENT],
            acknowledged=counts[OutboxState.ACKNOWLEDGED],
            retryable=counts[OutboxState.RETRYABLE],
            dead_letter=counts[OutboxState.DEAD_LETTER],
        )


def _count(result: PollResult, disposition: str) -> PollResult:
    if disposition == "applied":
        return result.model_copy(update={"applied": result.applied + 1})
    if disposition == "replayed":
        return result.model_copy(update={"replayed": result.replayed + 1})
    if disposition == "deferred":
        return result.model_copy(update={"deferred": result.deferred + 1})
    return result.model_copy(update={"rejected": result.rejected + 1})


def _message_identity(bot_id: str, update: ParsedTelegramUpdate) -> MessageIdentity:
    return MessageIdentity(
        bot_id=bot_id,
        telegram_user_id=update.telegram_user_id,
        chat_id=update.chat_id,
        chat_type=update.chat_type,
        update_id=update.update_id,
        message_id=update.message_id or "missing",
    )


def _callback_identity(bot_id: str, update: ParsedTelegramUpdate) -> CallbackIdentity:
    return CallbackIdentity(
        bot_id=bot_id,
        telegram_user_id=update.telegram_user_id,
        chat_id=update.chat_id,
        chat_type=update.chat_type,
        update_id=update.update_id,
        callback_query_id=update.callback_query_id or "missing",
    )


def _payload_from_nonce(nonce: ActionNonce) -> ActionPayload:
    payload = ActionPayload(
        action=nonce.action,
        organization_id=nonce.organization_id,
        user_id=nonce.user_id,
        account_id=nonce.account_id,
        resource_type=nonce.resource_type,
        resource_id=nonce.resource_id,
        revision_id=nonce.revision_id,
        content_hash=nonce.content_hash,
    )
    if payload_binding_hash(payload) != nonce.payload_hash:
        raise TelegramActivationError(
            "Stored nonce payload hash does not match.",
            reason="payload_mismatch",
        )
    if payload.action is TelegramRemoteAction.CLOSE:
        raise TelegramActivationError(
            "Telegram CLOSE is unavailable.",
            reason="close_unavailable",
        )
    return payload
