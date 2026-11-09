"""Cumulative Live seconds and captions in the existing durable voice reservation."""

from __future__ import annotations

import copy
import math
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid5

from sqlalchemy import select

from app.agent_voice.accounting import VoiceLedger
from app.agent_voice.config import VoiceUnavailableError
from app.agent_voice.live_config import CLEANUP_SECONDS, CLOSE_SECONDS, LiveLimits
from app.agent_voice.reservations_sweep import expired_hold_charge_id
from app.db.models import UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from app.schemas.common import CostSource, UsageStatus
from app.services.conversation_service import ConversationService

# Durable Live transport stage, committed under the reservation row lock.
# ``admitted``: reserved; ``session.start`` has not been handed to the provider
# socket, so no Live session (the only billable unit) can exist.
# ``session_start_sent``: committed immediately before ``session.start`` may be
# written. From here on provider spend is possible and accounting stays
# conservative until a final duration receipt arrives.
LIVE_TRANSPORT = "live_transport"
STAGE_ADMITTED = "admitted"
STAGE_SESSION_START_SENT = "session_start_sent"


def provably_unsent(data: dict[str, Any]) -> bool:
    """True only when the ledger proves no Live session or secondary work began."""
    transport = data.get(LIVE_TRANSPORT)
    return bool(
        isinstance(transport, dict)
        and transport.get("stage") == STAGE_ADMITTED
        and not data.get("live")
        and not data.get("responses")
        and not data.get("audio_bytes")
        and not data.get("tools")
        and not data.get("reasoning_attempts")
        and not data.get("embedding_attempts")
    )


class LiveLedger(VoiceLedger):
    limits: LiveLimits

    def initial_ledger(self) -> dict[str, Any]:
        return {LIVE_TRANSPORT: {"stage": STAGE_ADMITTED}}

    def session_start_may_send(self) -> None:
        """Commit the provider-contact boundary before ``session.start`` is written.

        Raises (and nothing is sent) when the row is no longer pending/active,
        expired, or the principal changed, so a swept or released hold can never
        be followed by provider spend.
        """

        def mark(_row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            transport = data.get(LIVE_TRANSPORT)
            if not isinstance(transport, dict) or transport.get("stage") != STAGE_ADMITTED:
                raise VoiceUnavailableError("live_transport_stage_conflict")
            transport["stage"] = STAGE_SESSION_START_SENT

        self.update(mark)

    def history(self) -> list[dict[str, Any]]:
        with self.sessions() as session:
            tenant = self.principal(session)
            conversations = ConversationService(session)
            conversation = conversations.require(
                self.conversation_id, organization_id=tenant.organization_id, user_id=tenant.user_id
            )
            messages = conversations.history_turns(conversation)
            history: list[dict[str, Any]] = []
            size = 0
            for message in reversed(messages):
                if len(history) >= 128:
                    break
                size += len(message.content.encode())
                if size > 8000:
                    break
                history.insert(
                    0,
                    {
                        "type": "message",
                        "role": message.role,
                        "content": [
                            {
                                "type": "input_text" if message.role == "user" else "output_text",
                                "text": message.content,
                            }
                        ],
                    },
                )
            return history

    def started(self, provider_id: str, *, resolved_model: str | None = None) -> None:
        """Bind the provider session identity so its duration receipts are accepted.

        Called before the resolved configuration is validated: a started session
        bills even when its configuration is then refused. A resolved model other
        than the priced one is kept and makes the final receipt unconfirmed.
        """

        def remember(row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            if not provider_id or data.get("live"):
                raise VoiceUnavailableError("invalid_live_session_identity")
            row.status = "active"
            data["live"] = {
                "provider_session_id": provider_id,
                "finalized": False,
                "seconds": None,
                "reason": None,
            }
            if resolved_model is not None:
                data["live"]["resolved_model"] = resolved_model
            data["transcripts"] = []

        self.update(remember)

    def transcript(self, role: str, fragment: dict[str, Any]) -> None:
        def remember(_row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            fragments = data["transcripts"]
            if any(f["event_id"] == fragment["event_id"] for f in fragments):
                return
            if (
                len(fragments) >= 512
                or sum(len(f["delta"].encode()) for f in fragments)
                + len(fragment["delta"].encode())
                > 64000
            ):
                raise VoiceUnavailableError("live_transcript_cap")
            fragments.append({"role": role, **fragment})

        self.update(remember)

    def usage(self, event: dict[str, Any], *, final: bool = False) -> None:
        # Only provider events reach this method. Client usage fields are refused.
        value = event.get("usage", {}).get("seconds")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise VoiceUnavailableError("invalid_live_usage_receipt")
        seconds = Decimal(str(value))
        with self.sessions() as session:
            row = session.scalar(
                select(VoiceSessionReservation)
                .where(VoiceSessionReservation.id == self.id)
                .with_for_update()
            )
            if row is None:
                raise VoiceUnavailableError("voice_session_missing")
            data = copy.deepcopy(row.ledger)
            live = data.get("live")
            if not live:
                raise VoiceUnavailableError("live_usage_before_start")
            previous = live.get("seconds")
            if previous is not None and seconds < Decimal(previous):
                raise VoiceUnavailableError("live_usage_regressed")
            if final and event.get("session", {}).get("id") != live["provider_session_id"]:
                raise VoiceUnavailableError("live_final_session_mismatch")
            if final and event.get("reason") not in {
                "close_requested",
                "expired",
                "content",
                "remote_hangup",
                "connection_lost",
            }:
                raise VoiceUnavailableError("invalid_live_final_reason")
            if live["finalized"]:
                if final and seconds == Decimal(previous):
                    if event["reason"] != live["reason"]:
                        raise VoiceUnavailableError("live_final_receipt_conflict")
                    return
                raise VoiceUnavailableError("live_usage_after_finalization")
            live.update(
                seconds=str(seconds), finalized=final, reason=event.get("reason") if final else None
            )
            # Keep an over-bound receipt as actual observed usage, but never refund
            # its conservative reservation or silently accept it as within budget.
            live["within_reservation"] = (
                seconds <= self.limits.maximum_seconds + CLOSE_SECONDS + CLEANUP_SECONDS
            )
            row.ledger, row.updated_at = data, datetime.now(UTC)
            event_id = uuid5(self.id, "live-duration")
            usage = session.get(UsageEvent, event_id)
            if usage is None:
                usage = UsageEvent(
                    id=event_id,
                    organization_id=self.organization_id,
                    user_id=self.user_id,
                    request_id=str(self.id),
                    feature="agent_voice_live",
                    provider="openai",
                    model=self.limits.voice_model,
                    input_tokens=0,
                    output_tokens=0,
                    total_tokens=0,
                )
                session.add(usage)
            usage.estimated_cost = seconds * self.limits.live.usd_per_second
            usage.cost_source = CostSource.STATIC_ESTIMATED
            usage.cost_is_placeholder = False
            usage.status = UsageStatus.SUCCESS if final else UsageStatus.PARTIAL
            usage.event_at = datetime.now(UTC)
            if final:
                # Measured seconds replace the conservative full-reservation charge.
                charged = session.get(UsageEvent, expired_hold_charge_id(self.id))
                if charged is not None:
                    session.delete(charged)
            session.commit()
        if not live["within_reservation"]:
            raise VoiceUnavailableError("live_duration_outside_reservation")

    def close(self, failure: dict[str, Any] | None = None) -> None:
        # Classify and settle in one locked transaction. Realtime's separate
        # close would temporarily release a silent, unconfirmed Live hold.
        with self.sessions() as session:
            row = session.scalar(
                select(VoiceSessionReservation)
                .where(VoiceSessionReservation.id == self.id)
                .with_for_update()
            )
            if row is None:
                return
            data = copy.deepcopy(row.ledger)
            transport = data.get(LIVE_TRANSPORT)
            if isinstance(transport, dict) and failure and "failure" not in transport:
                # Sanitized codes only (stage, class, status); kept for audit.
                transport["failure"] = failure
            row.ledger = data
            if provably_unsent(data):
                # session.start was never handed to the provider: no Live session,
                # no seconds and no secondary work exist. Release the hold at $0.
                row.status = "closed"
                row.accounted_cost = Decimal(0)
                row.updated_at = datetime.now(UTC)
                session.commit()
                return
            live = data.get("live", {})
            # Secondary usage is already emitted by the shared Agent pipeline.
            # Its unresolved cost is never replaced by an assumed refund.
            confirmed = bool(
                live.get("finalized")
                and live.get("within_reservation")
                and live.get("resolved_model", self.limits.voice_model) == self.limits.voice_model
            )
            secondary = bool(
                data["tools"] or data["reasoning_attempts"] or data.get("embedding_attempts")
            )
            row.status = "closed" if confirmed and not secondary else "uncertain"
            row.accounted_cost = (
                Decimal(live["seconds"]) * self.limits.live.usd_per_second
                if confirmed and not secondary
                else None
            )
            row.updated_at = datetime.now(UTC)
            session.commit()
