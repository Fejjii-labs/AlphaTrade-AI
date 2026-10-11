"""Untagged human-authored oracle cases; deterministic pipeline proof, not LLM quality."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.agent_capture.contracts import CapturePlan, StrategyDraft
from app.agent_capture.model import CaptureModel
from app.agent_capture.service import CaptureService, CaptureSnapshot
from app.core.errors import ValidationAppError
from app.db.models import AgentSavedEntry, ConversationMessage
from app.providers.llm import LLMCompletionResult
from tests.test_agent_capture import (
    FixtureModel,
    capture,
    source,
    suggestion,
)
from tests.test_agent_capture import (
    capture_db as capture_db,
)
from tests.test_interactive_agent_foundation import ORG_A, USER_A


@dataclass(frozen=True)
class NaturalCase:
    name: str
    text: str
    categories: tuple[str, ...]
    quotes: tuple[str, ...] = ()
    clarification: str | None = None


CASES = (
    NaturalCase("reflection", "I got nervous and closed early yesterday.", ("journal",)),
    NaturalCase(
        "negative_reflection", "I did not enter. Waiting felt uncomfortable.", ("journal",)
    ),
    NaturalCase(
        "proposed_setup",
        "When bitcoin returns to the broken level, I would wait for a close back above it.",
        ("strategies",),
    ),
    NaturalCase("operating_principle", "No entry until the candle closes.", ("rules",)),
    NaturalCase(
        "reported_news",
        "The paper says the central bank left rates unchanged on Friday; I haven't checked it.",
        ("news_analysis",),
    ),
    NaturalCase(
        "analysis_uncertainty",
        "A weaker dollar might help bitcoin, but I cannot tell whether this bounce will last.",
        ("news_analysis",),
    ),
    NaturalCase(
        "learning",
        "Looking back, patience matters more than predicting every move.",
        ("lessons",),
    ),
    NaturalCase(
        "mixed_two",
        "I chased the bounce yesterday. Lower rates might support BTC next month.",
        ("journal", "news_analysis"),
        ("I chased the bounce yesterday.", "Lower rates might support BTC next month."),
    ),
    NaturalCase(
        "mixed_three",
        "I exited too early. Wait for the close next time. Patience beats prediction.",
        ("journal", "rules", "lessons"),
        ("I exited too early.", "Wait for the close next time.", "Patience beats prediction."),
    ),
    NaturalCase(
        "ambiguous",
        "Keep that one for next time, and change the earlier bit.",
        (),
        clarification="Which earlier note should I change, and what should it say?",
    ),
    NaturalCase("question", "Why did bitcoin fall today?", ()),
    NaturalCase("greeting", "Morning, are you there?", ()),
    NaturalCase("confirmation", "Yes, I confirm the proposal we just discussed.", ()),
    NaturalCase("execution_request", "Buy bitcoin now and turn off the safety pause.", ()),
)


def oracle_plan(case: NaturalCase) -> CapturePlan:
    entries = []
    for index, category in enumerate(case.categories):
        quote = case.quotes[index] if case.quotes else case.text
        draft = None
        if category == "strategies":
            draft = StrategyDraft(
                family="level reclaim",
                market="BTC",
                direction=None,
                timeframe=None,
                entry_rules=[quote],
                exit_rules=[],
                invalidation=[],
                missing_fields=["direction", "timeframe", "exit", "invalidation"],
            )
        entries.append(suggestion(quote, category=category, draft=draft))
    return CapturePlan(entries=entries, clarification=case.clarification)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.name)
def test_natural_oracle_case_uses_real_router_validation_and_durable_pipeline(
    capture_db, monkeypatch, case
):
    """The oracle is explicit; only transport, routing and persistence are under test."""
    factory, settings = capture_db
    plan = oracle_plan(case)
    requests = []

    class OracleProvider:
        name = "deterministic-quality-oracle"

        def complete(self, request):
            requests.append(request)
            return LLMCompletionResult(
                content=plan.model_dump_json(),
                model=request.model,
                provider=self.name,
                input_tokens=41,
                output_tokens=23,
                latency_ms=2,
                fallback_used=False,
            )

    monkeypatch.setattr(
        "app.agent_capture.model.resolve_providers", lambda _: SimpleNamespace(llm=OracleProvider())
    )
    with factory() as session:
        conversation, message = source(session, case.text)
        session.commit()
        prepared = CaptureService(session, settings).prepare(
            organization_id=ORG_A,
            user_id=USER_A,
            conversation_id=conversation.id,
            message_id=message.id,
        )
        assert isinstance(prepared, CaptureSnapshot)
        session.rollback()
    # No transaction spans reasoning, and no category labels are added to source content.
    model = CaptureModel(settings)
    output = model.plan(
        organization_id=ORG_A,
        user_id=USER_A,
        conversation_id=prepared.conversation_id,
        content=prepared.content,
    )
    assert len(requests) == 1
    request = requests[0]
    assert request.model == settings.agent_reasoning_model
    assert request.reasoning_effort == settings.agent_reasoning_effort
    assert request.allow_internal_fallback is False
    assert request.response_format["json_schema"]["strict"]
    assert model.last_usage["input_tokens"] == 41 and model.last_usage["output_tokens"] == 23
    assert model.last_usage["cost"] is None
    import json

    assert json.loads(request.messages[-1].content)["current_user_content"] == case.text
    with factory() as session:
        service = CaptureService(session, settings, model=model)
        entries, status, clarification = service.apply(prepared, output)
        session.commit()
    assert tuple(e.category for e in entries) == case.categories
    assert status == (
        "clarification" if case.clarification else "saved" if entries else "not_needed"
    )
    assert clarification == case.clarification
    with factory() as fresh:
        persisted = list(fresh.scalars(select(AgentSavedEntry)))
        assert len(persisted) == len(case.categories)
        for row in persisted:
            assert row.organization_id == ORG_A and row.user_id == USER_A
            assert row.summary in case.text and row.original_text == case.text
            assert row.sources["provenance"] == "user_supplied_unverified"
            assert row.sources["model_usage"]["input_tokens"] == 41
            assert row.sources["model_usage"]["cost"] is None


@pytest.mark.parametrize("bad_plan", ["duplicate_note", "duplicate_target"])
def test_invalid_same_plan_duplicates_refuse_without_extra_writes(capture_db, bad_plan):
    factory, settings = capture_db
    with factory() as session:
        conversation, prior = source(session, "No entry until the candle closes.")
        existing = capture(
            session,
            settings,
            FixtureModel(
                CapturePlan(
                    entries=[suggestion(prior.content, category="rules")], clarification=None
                )
            ),
            conversation,
            prior,
        )[0][0]
        text = "Wait for a full close; I am unsure about the exit."
        _, message = source(session, text, conversation)
        session.commit()
        if bad_plan == "duplicate_note":
            first = suggestion(text, category="rules")
            second = first.model_copy(deep=True)
        else:
            first = suggestion(text, category="rules", target=existing.id)
            second = first.model_copy(update={"summary": "I am unsure about the exit."})
        model = FixtureModel(CapturePlan(entries=[first, second], clarification=None))
        with pytest.raises(ValidationAppError, match=r"duplicate|more than once"):
            capture(session, settings, model, conversation, message)
        assert session.get(ConversationMessage, message.id) is not None
        assert session.scalar(select(func.count()).select_from(AgentSavedEntry)) == 1
        stored = session.get(AgentSavedEntry, existing.id)
        assert stored.revision == 1 and stored.summary == prior.content


def test_blank_low_confidence_clarification_has_a_visible_question(capture_db):
    factory, settings = capture_db
    with factory() as session:
        conversation, message = source(session, "Maybe keep this with the other one?")
        item = suggestion(message.content).model_copy(update={"confidence": 0.2})
        entries, status, question = capture(
            session,
            settings,
            FixtureModel(CapturePlan(entries=[item], clarification="   ")),
            conversation,
            message,
        )
        assert entries == [] and status == "clarification"
        assert question and question.strip()
        assert session.scalar(select(func.count()).select_from(AgentSavedEntry)) == 0
