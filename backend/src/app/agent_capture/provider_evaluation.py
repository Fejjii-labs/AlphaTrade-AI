"""Finite, sequential provider evaluation; no runtime activation or oracle quality claims."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid5

from pydantic import Field, model_validator
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.agent_capture.contracts import CapturePlan, Category
from app.agent_capture.model import SYSTEM, CaptureModel
from app.agent_capture.service import CaptureService, CaptureSnapshot
from app.core.config import Settings
from app.db.base import Base
from app.db.models import (
    AgentSavedEntry,
    Membership,
    ModelCallAttempt,
    Organization,
    OrganizationQuota,
    UsageEvent,
    User,
)
from app.providers.base import ProviderStatus
from app.providers.factory import resolve_providers
from app.providers.llm import (
    LLMCompletionRequest,
    LLMCompletionResult,
    LLMProvider,
    OpenAILLMProvider,
)
from app.schemas.common import ConversationMessageRole, MembershipRole, StrictModel
from app.services.conversation_service import ConversationService
from app.services.turn_context import turn_context


class EvaluationBlockedError(RuntimeError):
    """A named preflight/admission refusal; never contains credentials or raw provider errors."""


class ReferenceEntry(StrictModel):
    id: UUID
    category: Category
    text: str = Field(min_length=1, max_length=8000)


class EvaluationCase(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$", max_length=60)
    text: str = Field(min_length=1, max_length=8000)
    expected_categories: list[Category] = Field(max_length=3)
    expects_clarification: bool = False
    reference_entries: list[ReferenceEntry] = Field(default_factory=list, max_length=3)
    expected_target_ids: list[UUID] = Field(default_factory=list, max_length=3)
    summary_qualifiers: list[list[str]] = Field(default_factory=list)


class EvaluationLimits(StrictModel):
    """Existing approved job budget and verified provider price receipt, never placeholder rates."""

    budget_reference: str = Field(min_length=1, max_length=160)
    remaining_budget_usd: Decimal = Field(gt=0, allow_inf_nan=False)
    model: str = Field(min_length=1, max_length=80)
    price_source: str = Field(pattern=r"^https://", max_length=500)
    price_valid_until: datetime
    input_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    output_usd_per_million: Decimal = Field(gt=0, allow_inf_nan=False)
    # Verified provider-enforced context ceiling. Reserving the full ceiling avoids
    # assuming an encoding/tokenizer or an unverified price for a model alias.
    maximum_input_tokens: int = Field(gt=0, le=10_000_000)

    @model_validator(mode="after")
    def price_is_dated(self) -> EvaluationLimits:
        if self.price_valid_until.tzinfo is None:
            raise ValueError("Price receipt requires an explicit timezone.")
        return self


def atomic_report(path: Path, report: dict[str, Any]) -> None:
    """Persist admission before transport; private file survives interrupted requests."""
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(report, stream, indent=2, default=str)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class SpendingGuard:
    """Each case reserves two worst-case attempts, including the router's single retry."""

    def __init__(
        self,
        limits: EvaluationLimits,
        cap: Decimal,
        max_output_tokens: int,
        report: dict[str, Any],
        persist: Callable[[], None],
    ):
        if not cap.is_finite() or cap <= 0 or cap > limits.remaining_budget_usd:
            raise EvaluationBlockedError("cap_exceeds_existing_budget_or_is_not_finite")
        if limits.price_valid_until <= datetime.now(UTC):
            raise EvaluationBlockedError("verified_price_receipt_expired")
        self.limits, self.cap = limits, cap
        self.max_output_tokens = max_output_tokens
        self.per_attempt = (
            limits.input_usd_per_million * limits.maximum_input_tokens
            + limits.output_usd_per_million * max_output_tokens
        ) / Decimal(1_000_000)
        self.report, self.persist = report, persist
        self.charged = Decimal("0")
        self.current: dict[str, Any] | None = None

    def begin(self, case_id: str) -> None:
        if self.current is not None:
            raise EvaluationBlockedError("unfinished_case_no_automatic_retry")
        maximum = 2 * self.per_attempt
        if self.charged + maximum > self.cap:
            raise EvaluationBlockedError("remaining_cap_cannot_cover_case_and_retry")
        self.current = {
            "case_id": case_id,
            "reserved_usd": str(maximum),
            "attempts": [],
            "state": "reserved",
        }
        self.report["reservations"].append(self.current)
        self.charged += maximum
        self.report["reserved_upper_bound_usd"] = str(self.charged)
        self.persist()

    def before_transport(self, request: LLMCompletionRequest) -> dict[str, Any]:
        if (
            self.current is None
            or len(self.current["attempts"]) >= 2
            or request.model != self.limits.model
            or request.max_tokens > self.max_output_tokens
        ):
            raise EvaluationBlockedError("request_outside_reserved_route_or_attempt_limit")
        if (
            sum(len(m.content.encode()) for m in request.messages)
            > self.limits.maximum_input_tokens
        ):
            raise EvaluationBlockedError("prompt_exceeds_verified_input_ceiling")
        attempt: dict[str, Any] = {
            "state": "transport_may_start",
            "upper_bound_usd": str(self.per_attempt),
        }
        self.current["attempts"].append(attempt)
        self.persist()
        attempt["admitted_before_transport"] = True
        return attempt

    def finish(self) -> None:
        assert self.current is not None
        used = len(self.current["attempts"])
        self.charged -= (2 - used) * self.per_attempt
        self.current["reserved_usd"] = str(used * self.per_attempt)
        self.current["state"] = "completed"
        self.report["reserved_upper_bound_usd"] = str(self.charged)
        self.persist()
        self.current = None


class CappedProvider:
    def __init__(self, provider: LLMProvider, guard: SpendingGuard):
        self.provider, self.guard = provider, guard
        self.name, self.kind = provider.name, provider.kind

    def status(self) -> ProviderStatus:
        return self.provider.status()

    def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
        receipt = self.guard.before_transport(request)
        started = time.monotonic()
        try:
            receipt["transport_invoked"] = True
            result = self.provider.complete(request)
            receipt.update(
                state="response_received",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                resolved_model=result.model,
                fallback_used=result.fallback_used,
                provider_output=result.content,
            )
            if (
                result.model != self.guard.limits.model
                or result.fallback_used
                or result.input_tokens > self.guard.limits.maximum_input_tokens
                or result.output_tokens > self.guard.max_output_tokens
            ):
                raise EvaluationBlockedError("response_outside_verified_route_or_token_ceiling")
            return result
        except Exception:
            # Keep full admission on ambiguous/error outcomes. No raw provider text/secrets.
            receipt["state"] = "error_or_ambiguous"
            raise
        finally:
            receipt["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            self.guard.persist()


def score_case(
    case: EvaluationCase, plan: CapturePlan, status: str, entries: list[Any]
) -> dict[str, Any]:
    summaries = " ".join(entry.summary for entry in entries).casefold()
    source = "\n".join([case.text, *(entry.text for entry in case.reference_entries)])
    categories = sorted(entry.category for entry in entries)
    quotes_ok = all(q in source for item in plan.entries for q in item.evidence_quotes)
    qualifier_ok = all(
        any(term.casefold() in summaries for term in alternatives)
        for alternatives in case.summary_qualifiers
    )
    targets = sorted(str(item.target_entry_id) for item in plan.entries if item.target_entry_id)
    return {
        "case_id": case.id,
        "status": status,
        "observed_categories": categories,
        "category_correct": categories == sorted(case.expected_categories)
        and (
            status == "clarification"
            if case.expects_clarification
            else status == "saved"
            if case.expected_categories
            else status == "not_needed"
        ),
        "false_capture": not case.expected_categories and bool(entries),
        "quotes_match_user_sources": quotes_ok if plan.entries else None,
        "reference_qualifiers_preserved": qualifier_ok
        if entries and case.summary_qualifiers
        else None,
        "correction_target_correct": targets == sorted(str(i) for i in case.expected_target_ids),
        "semantic_faithfulness_review": "pending_human_review",
        "output": plan.model_dump(mode="json"),
    }


def evaluate(
    settings: Settings,
    database_url: str,
    cases: list[EvaluationCase],
    limits: EvaluationLimits,
    cap: Decimal,
    run_id: UUID,
    output: Path,
    revision: str,
) -> dict[str, Any]:
    if not settings.openai_api_key.strip():
        raise EvaluationBlockedError("missing_OPENAI_API_KEY_binding")
    if settings.provider_mode.strip().lower() == "mock":
        raise EvaluationBlockedError("configured_provider_is_mock")
    if limits.model != settings.agent_reasoning_model:
        raise EvaluationBlockedError("price_receipt_does_not_match_configured_model")
    if not cases or len({case.id for case in cases}) != len(cases):
        raise EvaluationBlockedError("empty_or_duplicate_case_identity")
    if output.exists():
        raise EvaluationBlockedError("existing_run_receipt_requires_review_no_automatic_retry")
    if not database_url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise EvaluationBlockedError("durable_isolated_postgresql_required")
    report: dict[str, Any] = {
        "format": "agent-provider-quality/v1",
        "run_id": str(run_id),
        "revision": revision,
        "provider_mode": settings.provider_mode,
        "configured_model": settings.agent_reasoning_model,
        "reasoning_effort": settings.agent_reasoning_effort,
        "measured": False,
        "complete": False,
        "quality_accepted": False,
        "metrics": None,
        "latency_ms": None,
        "usage": None,
        "prompt_sha256": hashlib.sha256(SYSTEM.encode()).hexdigest(),
        "corpus_sha256": hashlib.sha256(
            json.dumps([case.model_dump(mode="json") for case in cases], sort_keys=True).encode()
        ).hexdigest(),
        "cases_total": len(cases),
        "cases": [],
        "reservations": [],
        "spend_cap_usd": str(cap),
        "existing_budget_reference": limits.budget_reference,
        "price_receipt": limits.model_dump(mode="json"),
        "actual_provider_charge_usd": None,
        "reserved_upper_bound_usd": "0",
    }
    guard = SpendingGuard(
        limits, cap, settings.agent_max_output_tokens, report, lambda: atomic_report(output, report)
    )
    # Atomic exclusive creation also fences a parallel launch before any transport.
    descriptor = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    atomic_report(output, report)
    admin = create_engine(database_url, poolclass=NullPool)
    schema = "agent_eval_" + run_id.hex
    report["retained_accounting_schema"] = schema
    if schema in inspect(admin).get_schema_names():
        raise EvaluationBlockedError("existing_accounting_schema_no_automatic_retry")
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    engine = create_engine(
        admin.url.update_query_dict({"options": f"-csearch_path={schema}"}), poolclass=NullPool
    )
    sessions = sessionmaker(engine, expire_on_commit=False)
    Base.metadata.create_all(engine)
    org_id = uuid5(run_id, "synthetic-evaluation-organization")
    with sessions() as session:
        session.add(Organization(id=org_id, name="Synthetic provider quality evaluation"))
        session.flush()
        session.add(OrganizationQuota(organization_id=org_id, monthly_cost_limit=cap))
        session.commit()
    resolved = resolve_providers(settings).llm
    report["configured_provider"] = resolved.name
    actual_provider = isinstance(resolved, OpenAILLMProvider)
    report["evidence_kind"] = (
        "actual_provider" if actual_provider else "deterministic_transport_test"
    )
    provider = CappedProvider(resolved, guard)
    try:
        for case in cases:
            try:
                guard.begin(case.id)
            except EvaluationBlockedError as exc:
                report["stop_reason"] = str(exc)
                break
            started = time.monotonic()
            user_id = uuid5(run_id, case.id)
            turn_id = uuid5(run_id, "turn:" + case.id)
            with sessions() as session:
                session.add(
                    User(
                        id=user_id,
                        email=f"{user_id}@synthetic.invalid",
                        hashed_password="unusable-evaluation-fixture",
                    )
                )
                session.flush()
                session.add(
                    Membership(user_id=user_id, organization_id=org_id, role=MembershipRole.OWNER)
                )
                conversations = ConversationService(session)
                conversation = conversations.get_or_create(
                    organization_id=org_id, user_id=user_id, conversation_id=None
                )
                for reference in case.reference_entries:
                    message = conversations.append_message(
                        conversation=conversation,
                        role=ConversationMessageRole.USER,
                        content=reference.text,
                    )
                    session.flush()
                    session.add(
                        AgentSavedEntry(
                            id=reference.id,
                            organization_id=org_id,
                            user_id=user_id,
                            conversation_id=conversation.id,
                            category=reference.category,
                            title="Existing reference note",
                            summary=reference.text,
                            original_text=reference.text,
                            revision=1,
                            history=[],
                            sources={"message_ids": [str(message.id)]},
                            undone=False,
                        )
                    )
                message = conversations.append_message(
                    conversation=conversation, role=ConversationMessageRole.USER, content=case.text
                )
                session.commit()
                prepared = CaptureService(session, settings).prepare(
                    organization_id=org_id,
                    user_id=user_id,
                    conversation_id=conversation.id,
                    message_id=message.id,
                )
                assert isinstance(prepared, CaptureSnapshot)
                session.expunge_all()
            model = CaptureModel(settings, llm_provider=provider)
            try:
                with turn_context(turn_id, sessions):
                    plan = model.plan(
                        organization_id=org_id,
                        user_id=user_id,
                        conversation_id=prepared.conversation_id,
                        content=prepared.content,
                    )
                with sessions() as session:
                    entries, status, clarification = CaptureService(
                        session, settings, model=model
                    ).apply(prepared, plan)
                    session.commit()
                result = score_case(case, plan, status, entries)
                result["clarification"] = clarification
            except Exception as exc:
                result = {
                    "case_id": case.id,
                    "error_type": type(exc).__name__,
                    "status": "error",
                    "category_correct": False,
                    "false_capture": None,
                }
                report["stop_reason"] = "provider_validation_or_accounting_error"
            result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 2)
            result["usage"] = model.last_usage
            with sessions() as session:
                attempts = list(
                    session.scalars(
                        select(ModelCallAttempt).where(ModelCallAttempt.user_id == user_id)
                    )
                )
                result["attempts"] = [
                    {
                        "id": str(a.id),
                        "usage_event_id": str(a.usage_event_id),
                        "input_tokens": a.input_tokens,
                        "output_tokens": a.output_tokens,
                        "failure_category": a.failure_category.value,
                        "success": a.success,
                        "cost_source": a.cost_source.value,
                        "cost": str(a.estimated_cost)
                        if a.cost_source.value != "unavailable"
                        else None,
                        "accounted": a.usage_event_id is not None
                        and session.get(UsageEvent, a.usage_event_id) is not None,
                    }
                    for a in attempts
                ]
                if not attempts or not all(a["accounted"] for a in result["attempts"]):
                    report["stop_reason"] = "durable_attempt_accounting_unverified"
            report["cases"].append(result)
            guard.finish()
            if report.get("stop_reason"):
                break
        receipts = [attempt for r in report["reservations"] for attempt in r["attempts"]]
        report["measured"] = actual_provider and any(
            r.get("admitted_before_transport") and r.get("transport_invoked") for r in receipts
        )
        report["complete"] = len(report["cases"]) == len(cases) and not report.get("stop_reason")
        report["unexecuted_case_ids"] = [case.id for case in cases[len(report["cases"]) :]]
        diagnostics = {
            "cases_executed": len(report["cases"]),
            "cases_total": len(cases),
            "category_correct": sum(bool(case["category_correct"]) for case in report["cases"]),
            "category_accuracy": sum(bool(case["category_correct"]) for case in report["cases"])
            / len(report["cases"])
            if report["cases"]
            else None,
            "false_captures": sum(case["false_capture"] is True for case in report["cases"]),
            "negative_control_cases_total": sum(not case.expected_categories for case in cases),
            "negative_control_cases_executed": sum(
                not case.expected_categories for case in cases[: len(report["cases"])]
            ),
            "errors": sum(case["status"] == "error" for case in report["cases"]),
            "source_quotes_supported": sum(
                case.get("quotes_match_user_sources") is True for case in report["cases"]
            ),
            "source_quotes_evaluated": sum(
                case.get("quotes_match_user_sources") is not None for case in report["cases"]
            ),
            "qualifier_checks_supported": sum(
                case.get("reference_qualifiers_preserved") is True for case in report["cases"]
            ),
            "qualifier_checks_evaluated": sum(
                case.get("reference_qualifiers_preserved") is not None for case in report["cases"]
            ),
            "correction_target_errors": sum(
                case.get("correction_target_correct") is False for case in report["cases"]
            ),
            "semantic_faithfulness_review": "pending_human_review",
        }
        # Fixture transports verify the runner, never the model's semantic quality.
        report["diagnostics"] = diagnostics
        report["metrics"] = diagnostics if report["measured"] else None
        if report["measured"]:
            report["latency_ms"] = {
                "provider_attempts": [r["elapsed_ms"] for r in receipts],
                "end_to_end_cases": [c["elapsed_ms"] for c in report["cases"]],
            }
            report["usage"] = {
                "attempts": sum(len(case["attempts"]) for case in report["cases"]),
                "reported_input_tokens": sum(r.get("input_tokens", 0) for r in receipts),
                "reported_output_tokens": sum(r.get("output_tokens", 0) for r in receipts),
                "attempts_without_response_usage": sum("input_tokens" not in r for r in receipts),
                "actual_provider_charge_usd": None,
                "reserved_upper_bound_usd": report["reserved_upper_bound_usd"],
            }
        atomic_report(output, report)
        return report
    finally:
        # Preserve the isolated schema/attempts for review and interruption recovery.
        engine.dispose()
        admin.dispose()
