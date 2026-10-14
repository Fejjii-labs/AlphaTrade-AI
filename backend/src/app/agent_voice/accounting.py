"""Durable conservative voice admission; no transaction survives upstream I/O."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID, uuid4, uuid5

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent_voice.config import VoiceLimits, VoiceUnavailableError
from app.agent_voice.live_config import LiveLimits
from app.core.config import Settings
from app.db.models import Organization, OrganizationQuota, UsageEvent
from app.db.voice_realtime import VoiceSessionReservation
from app.repositories.usage import UsageRepository
from app.schemas.common import CostSource, UsageStatus
from app.services.auth_service import AuthService
from app.services.conversation_service import ConversationService
from app.services.quota_service import QuotaService
from app.services.turn_context import current_turn

ChangeResult = TypeVar("ChangeResult")


class VoiceLedger:
    def __init__(
        self,
        sessions: Callable[[], Session],
        settings: Settings,
        limits: VoiceLimits | LiveLimits,
        token: str,
        conversation_id: UUID,
    ):
        self.sessions, self.settings, self.limits = sessions, settings, limits
        self.token, self.conversation_id = token, conversation_id
        self.id = uuid4()
        self.organization_id: UUID | None = None
        self.user_id: UUID | None = None

    def principal(self, session: Session) -> Any:
        from app.security.rbac import MUTATION_ROLES

        tenant = AuthService(session, self.settings).resolve_tenant(self.token)
        if tenant.membership_role not in MUTATION_ROLES:
            raise VoiceUnavailableError("voice_permission_denied")
        if str(tenant.organization_id) != self.limits.organization_id:
            raise VoiceUnavailableError("voice_budget_tenant_mismatch")
        if self.organization_id is not None and (
            tenant.organization_id != self.organization_id or tenant.user_id != self.user_id
        ):
            raise VoiceUnavailableError("voice_auth_context_changed")
        ConversationService(session).require(
            self.conversation_id,
            organization_id=tenant.organization_id,
            user_id=tenant.user_id,
        )
        return tenant

    def reserve(self) -> None:
        now = datetime.now(UTC)
        maximum = self.limits.reservation(self.settings)
        tokens = self.limits.reserved_tokens(self.settings)
        with self.sessions() as session:
            tenant = self.principal(session)
            session.scalar(
                select(Organization)
                .where(Organization.id == tenant.organization_id)
                .with_for_update()
            )
            competing = session.scalar(
                select(func.coalesce(func.sum(VoiceSessionReservation.reserved_cost), 0)).where(
                    VoiceSessionReservation.organization_id == tenant.organization_id,
                    VoiceSessionReservation.budget_reference == self.limits.budget_reference,
                )
            )
            usage = UsageRepository(session)
            used = usage.summarize(
                organization_id=tenant.organization_id, since=self.limits.budget_as_of
            ).total_cost
            # A previous reservation remains a debit even after a restart/unknown reply.
            if (
                maximum + Decimal(competing or 0) + Decimal(used or 0)
                > self.limits.remaining_budget_usd
            ):
                raise VoiceUnavailableError("voice_approved_budget_exhausted")
            quota = session.scalar(
                select(OrganizationQuota).where(
                    OrganizationQuota.organization_id == tenant.organization_id
                )
            )
            if quota is None:
                raise VoiceUnavailableError("voice_organization_quota_uninitialized")
            month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            summary = usage.summarize(organization_id=tenant.organization_id, since=month)
            usage_cost, usage_tokens = summary.total_cost, summary.total_tokens
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            daily = usage.count_requests_since(organization_id=tenant.organization_id, since=day)
            if (
                quota.daily_request_limit >= 0
                and daily >= Decimal(quota.daily_request_limit) * quota.hard_block_threshold
            ):
                raise VoiceUnavailableError("voice_daily_request_quota_exhausted")
            feature = usage.count_requests_since(
                organization_id=tenant.organization_id, feature="agent_chat", since=month
            )
            if (
                quota.limit_agent_chat >= 0
                and feature >= Decimal(quota.limit_agent_chat) * quota.hard_block_threshold
            ):
                raise VoiceUnavailableError("voice_agent_feature_quota_exhausted")
            reserved_cost, reserved_tokens = session.execute(
                select(
                    func.coalesce(func.sum(VoiceSessionReservation.reserved_cost), 0),
                    func.coalesce(func.sum(VoiceSessionReservation.reserved_tokens), 0),
                ).where(
                    VoiceSessionReservation.organization_id == tenant.organization_id,
                    (VoiceSessionReservation.status != "closed")
                    | VoiceSessionReservation.accounted_cost.is_(None),
                )
            ).one()
            if quota.monthly_cost_limit > 0 and (
                Decimal(usage_cost) + Decimal(reserved_cost) + maximum
                > quota.monthly_cost_limit * quota.hard_block_threshold
            ):
                raise VoiceUnavailableError("voice_monthly_cost_quota_exhausted")
            if quota.monthly_token_limit >= 0 and (
                usage_tokens + reserved_tokens + tokens
                > Decimal(quota.monthly_token_limit) * quota.hard_block_threshold
            ):
                raise VoiceUnavailableError("voice_monthly_token_quota_exhausted")
            digest = hashlib.sha256(self.limits.model_dump_json().encode()).hexdigest()
            session.add(
                VoiceSessionReservation(
                    id=self.id,
                    organization_id=tenant.organization_id,
                    user_id=tenant.user_id,
                    conversation_id=self.conversation_id,
                    budget_reference=self.limits.budget_reference,
                    model=self.limits.voice_model,
                    status="pending",
                    reserved_cost=maximum,
                    reserved_tokens=tokens,
                    accounted_cost=None,
                    response_count=0,
                    ledger={
                        "limits_digest": digest,
                        "expires_at": min(
                            now + timedelta(seconds=self.limits.maximum_seconds),
                            self.limits.receipt_deadline(),
                        ).isoformat(),
                        "audio_bytes": 0,
                        "responses": [],
                        "tools": {},
                        "reasoning_attempts": [],
                        "embedding_attempts": {},
                    },
                    created_at=now,
                    updated_at=now,
                )
            )
            session.commit()
            self.organization_id, self.user_id = tenant.organization_id, tenant.user_id

    def update(
        self, change: Callable[[VoiceSessionReservation, dict[str, Any]], ChangeResult]
    ) -> ChangeResult:
        with self.sessions() as session:
            self.principal(session)
            row = session.scalar(
                select(VoiceSessionReservation)
                .where(VoiceSessionReservation.id == self.id)
                .with_for_update()
            )
            if row is None or row.status not in {"pending", "active"}:
                raise VoiceUnavailableError("voice_session_closed")
            data = copy.deepcopy(row.ledger)
            if datetime.fromisoformat(data["expires_at"]) <= datetime.now(UTC):
                raise VoiceUnavailableError("voice_session_expired")
            result = change(row, data)
            row.ledger, row.updated_at = data, datetime.now(UTC)
            session.commit()
            return result

    def activate(self) -> None:
        self.update(lambda row, _: setattr(row, "status", "active"))

    def audio(self, length: int) -> None:
        def admit(_row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            total = data["audio_bytes"] + length
            if total > 48000 * self.limits.maximum_audio_seconds:
                raise VoiceUnavailableError("voice_audio_duration_exhausted")
            data["audio_bytes"] = total

        self.update(admit)

    def response(self) -> str:
        limits = self.limits
        assert isinstance(limits, VoiceLimits)

        def admit(row: VoiceSessionReservation, data: dict[str, Any]) -> str:
            if row.response_count >= limits.maximum_responses:
                raise VoiceUnavailableError("voice_response_cap_exhausted")
            key = str(uuid4())
            row.response_count += 1
            data["responses"].append({"admission_id": key, "state": "transport_may_start"})
            return key

        return str(self.update(admit))

    def response_started(self, response_id: str) -> None:
        def bind(_row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            item = next((r for r in data["responses"] if "response_id" not in r), None)
            if item is None:
                raise VoiceUnavailableError("unadmitted_provider_response")
            item["response_id"] = response_id

        self.update(bind)

    def response_done(self, response: dict[str, Any]) -> None:
        assert isinstance(self.limits, VoiceLimits)
        # Never called with a client event. Only the authenticated upstream WS is trusted.
        response_id = str(response.get("id", ""))
        usage = response.get("usage")
        cost: Decimal | None = None
        input_tokens = output_tokens = 0
        if isinstance(usage, dict):
            incoming, outgoing = usage.get("input_tokens"), usage.get("output_tokens")
            if (
                isinstance(incoming, int)
                and not isinstance(incoming, bool)
                and incoming >= 0
                and isinstance(outgoing, int)
                and not isinstance(outgoing, bool)
                and outgoing >= 0
                and incoming <= self.limits.realtime.maximum_input_tokens
                and outgoing <= self.limits.maximum_output_tokens
            ):
                input_tokens, output_tokens = incoming, outgoing
                # Conservative verified rate rather than fabricated audio/cache split.
                cost = (
                    incoming
                    * max(
                        self.limits.realtime.input_usd_per_million,
                        self.limits.realtime.audio_input_usd_per_million,
                    )
                    + outgoing
                    * max(
                        self.limits.realtime.output_usd_per_million,
                        self.limits.realtime.audio_output_usd_per_million,
                    )
                ) / Decimal(1000000)
        with self.sessions() as session:
            row = session.scalar(
                select(VoiceSessionReservation)
                .where(VoiceSessionReservation.id == self.id)
                .with_for_update()
            )
            if row is None:
                raise VoiceUnavailableError("voice_session_missing")
            data = copy.deepcopy(row.ledger)
            item = next((r for r in data["responses"] if r.get("response_id") == response_id), None)
            if item is None:
                raise VoiceUnavailableError("unadmitted_provider_response")
            if item.get("state") == "done":
                return
            item.update(
                state="done", usage=usage, estimated_cost=str(cost) if cost is not None else None
            )
            row.ledger, row.updated_at = data, datetime.now(UTC)
            event_id = uuid5(self.id, "response:" + response_id)
            if session.get(UsageEvent, event_id) is None:
                session.add(
                    UsageEvent(
                        id=event_id,
                        organization_id=self.organization_id,
                        user_id=self.user_id,
                        request_id=str(self.id),
                        feature="agent_voice_realtime",
                        provider="openai",
                        model=self.limits.voice_model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        total_tokens=input_tokens + output_tokens,
                        estimated_cost=cost or Decimal(0),
                        cost_source=CostSource.STATIC_ESTIMATED
                        if cost is not None
                        else CostSource.UNAVAILABLE,
                        cost_is_placeholder=cost is None,
                        status=UsageStatus.SUCCESS
                        if response.get("status") == "completed"
                        else UsageStatus.FAILURE,
                        event_at=datetime.now(UTC),
                    )
                )
            session.commit()

    def tool(self, call_id: str, body: dict[str, Any]) -> tuple[str, bool]:
        def remember(_row: VoiceSessionReservation, data: dict[str, Any]) -> tuple[str, bool]:
            tools = data["tools"]
            if call_id in tools:
                if tools[call_id]["body"] != body:
                    raise VoiceUnavailableError("voice_tool_identity_conflict")
                return tools[call_id]["key"], False
            if len(tools) >= self.limits.maximum_turns:
                raise VoiceUnavailableError("voice_turn_cap_exhausted")
            key = str(uuid5(self.id, "tool:" + call_id))
            tools[call_id] = {"key": key, "body": body, "state": "pending"}
            return key, True

        return self.update(remember)

    def tool_done(self, call_id: str, result: dict[str, Any]) -> None:
        def remember(_row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            data["tools"][call_id].update(state="acknowledged", result=result)

        self.update(remember)

    def reasoning_attempt(self, request: Any) -> int:
        def admit(_row: VoiceSessionReservation, data: dict[str, Any]) -> int:
            attempts = data["reasoning_attempts"]
            if len(attempts) >= 4 * self.limits.maximum_turns:
                raise VoiceUnavailableError("voice_reasoning_attempt_cap")
            if (
                request.model != self.limits.reasoning.model
                or request.max_tokens > self.settings.agent_max_output_tokens
            ):
                raise VoiceUnavailableError("voice_reasoning_route_mismatch")
            # UTF8 bytes conservatively bound text tokens; no tokenizer guess.
            if (
                sum(len(m.content.encode()) for m in request.messages)
                > self.limits.reasoning.maximum_input_tokens
            ):
                raise VoiceUnavailableError("voice_reasoning_context_cap")
            attempts.append({"state": "transport_may_start", "model": request.model})
            return len(attempts) - 1

        return int(self.update(admit))

    def reasoning_done(self, index: int, result: Any) -> None:
        def remember(_row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            data["reasoning_attempts"][index].update(
                state="received",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                resolved_model=result.model,
            )

        self.update(remember)

    def embedding_attempt(self, key: str, query: str) -> None:
        """Commit one original turn admission before embedding transport can start."""
        price = self.limits.embedding
        if (
            price is None
            or price.model != self.settings.embeddings_model
            or price.valid_until <= datetime.now(UTC)
        ):
            raise VoiceUnavailableError("voice_embedding_receipt_unavailable")
        if not query or len(query.encode()) > price.maximum_input_tokens:
            raise VoiceUnavailableError("voice_embedding_input_cap")
        turn = current_turn()
        # No reservation/organization lock is held during quota's independent audit.
        with self.sessions() as session:
            tenant = self.principal(session)
            check = QuotaService(session).check_feature(
                tenant.organization_id, "rag_search", request_id=key, user_id=tenant.user_id
            )
            session.commit()
            if not check.allowed:
                raise VoiceUnavailableError("voice_embedding_quota_exhausted")

        def admit(row: VoiceSessionReservation, data: dict[str, Any]) -> None:
            if data["limits_digest"] != hashlib.sha256(
                self.limits.model_dump_json().encode()
            ).hexdigest() or row.reserved_cost < self.limits.reservation(self.settings):
                raise VoiceUnavailableError("voice_embedding_reservation_mismatch")
            original = next((t for t in data["tools"].values() if t["key"] == key), None)
            if original is None or original["body"].get("message", "")[:2000] != query:
                raise VoiceUnavailableError("voice_embedding_turn_identity_conflict")
            attempts = data.setdefault("embedding_attempts", {})
            if key in attempts:
                # A timed-out or interrupted transport is never retried automatically.
                raise VoiceUnavailableError("voice_embedding_already_admitted")
            if len(attempts) >= self.limits.maximum_turns:
                raise VoiceUnavailableError("voice_embedding_turn_cap")
            if turn is None:
                raise VoiceUnavailableError("voice_embedding_canonical_turn_required")
            attempts[key] = {
                "state": "transport_may_start",
                "query_digest": hashlib.sha256(query.encode()).hexdigest(),
                "model": price.model,
                "request_id": str(turn.turn_id),
            }

        self.update(admit)

    def embedding_done(self, key: str, result: Any | None, *, valid: bool) -> None:
        """Persist server-reported usage, or explicit unknown usage without a refund."""
        price = self.limits.embedding
        if price is None:
            raise VoiceUnavailableError("voice_embedding_receipt_unavailable")
        if valid and result is None:
            raise VoiceUnavailableError("voice_embedding_usage_receipt_missing")
        tokens = result.input_tokens if valid and result is not None else 0
        cost = Decimal(tokens) * price.input_usd_per_million / Decimal(1000000) if valid else None
        with self.sessions() as session:
            row = session.scalar(
                select(VoiceSessionReservation)
                .where(VoiceSessionReservation.id == self.id)
                .with_for_update()
            )
            if row is None:
                raise VoiceUnavailableError("voice_session_missing")
            data = copy.deepcopy(row.ledger)
            item = data.get("embedding_attempts", {}).get(key)
            if item is None:
                raise VoiceUnavailableError("unadmitted_embedding_response")
            if item["state"] in {"received", "uncertain"}:
                return
            item.update(
                state="received" if valid else "uncertain",
                input_tokens=tokens if valid else None,
                estimated_cost=str(cost) if cost is not None else None,
            )
            row.ledger, row.updated_at = data, datetime.now(UTC)
            event_id = uuid5(UUID(key), "voice-embedding:query")
            if session.get(UsageEvent, event_id) is None:
                session.add(
                    UsageEvent(
                        id=event_id,
                        organization_id=self.organization_id,
                        user_id=self.user_id,
                        request_id=item["request_id"],
                        feature="rag_search",
                        provider="openai-embeddings",
                        model=price.model,
                        input_tokens=tokens,
                        output_tokens=0,
                        total_tokens=tokens,
                        estimated_cost=cost if cost is not None else Decimal(0),
                        cost_source=CostSource.STATIC_ESTIMATED
                        if valid
                        else CostSource.UNAVAILABLE,
                        cost_is_placeholder=not valid,
                        status=UsageStatus.SUCCESS if valid else UsageStatus.FAILURE,
                        event_at=datetime.now(UTC),
                    )
                )
            session.commit()

    def close(self) -> None:
        # Closing is cleanup, not a refund, and must work after expiry/logout.
        with self.sessions() as session:
            row = session.scalar(
                select(VoiceSessionReservation)
                .where(VoiceSessionReservation.id == self.id)
                .with_for_update()
            )
            if row is None:
                return
            data = row.ledger
            ambiguous = any(
                r.get("state") != "done" or r.get("estimated_cost") is None
                for r in data["responses"]
            )
            ambiguous |= any(t.get("state") != "acknowledged" for t in data["tools"].values())
            # Actual tool usage belongs to existing ModelRouter UsageEvent records.
            # Unknown canonical cost cannot be replaced by an assumed tool cost/refund.
            ambiguous |= bool(data["reasoning_attempts"]) or bool(data["audio_bytes"])
            ambiguous |= bool(data.get("embedding_attempts"))
            row.status = "uncertain" if ambiguous else "closed"
            row.accounted_cost = None if ambiguous else Decimal(0)
            row.updated_at = datetime.now(UTC)
            session.commit()
