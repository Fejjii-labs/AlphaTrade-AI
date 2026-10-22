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
from app.db.models import UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from app.schemas.common import CostSource, UsageStatus
from app.services.conversation_service import ConversationService


class LiveLedger(VoiceLedger):
    limits: LiveLimits

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

    def started(self, provider_id: str) -> None:
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
            usage.status = UsageStatus.SUCCESS if final else UsageStatus.FAILURE
            usage.event_at = datetime.now(UTC)
            session.commit()
        if not live["within_reservation"]:
            raise VoiceUnavailableError("live_duration_outside_reservation")

    def close(self) -> None:
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
            data = row.ledger
            live = data.get("live", {})
            # Secondary usage is already emitted by the shared Agent pipeline.
            # Its unresolved cost is never replaced by an assumed refund.
            confirmed = bool(live.get("finalized") and live.get("within_reservation"))
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
