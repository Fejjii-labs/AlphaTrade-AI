"""Combined Agent reads and sealed paper proposals share one conversation safely."""

import pytest
from sqlalchemy import func, select

from app.core.errors import ValidationAppError
from app.db.models import ConversationMessage, ExecutionFillFact, JournalTrade
from app.interactive_agent.actions import ActionRequest
from app.interactive_agent.contracts import AgentCapability, AgentTurnRequest, ProposalLifecycle
from app.interactive_agent.service import InteractiveAgentService
from app.schemas.strategy_analytics import StrategyAnalyticsFilters
from tests.support.phase7_postgres import ORG_ID, USER_ID
from tests.support.postgres_persistence import requires_postgres
from tests.test_agent_paper_execution_v4 import (
    count,
    interactive_confirm,
    interactive_prepare,
    interactive_service,
)
from tests.test_agent_paper_execution_v4 import interactive_world as execution_world
from tests.test_agent_paper_execution_v4 import paper_database as execution_paper_database
from tests.test_agent_paper_execution_v4 import paper_world as execution_paper_world
from tests.test_interactive_agent_foundation import ORG_A, USER_A
from tests.test_interactive_agent_foundation import agent_db as foundation_agent_db

# Register dependencies locally even when their source modules are also collected.
paper_database = execution_paper_database
paper_world = execution_paper_world
interactive_world = execution_world
agent_db = foundation_agent_db


class ForbiddenResponder:
    def compose(self, **kwargs):
        pytest.fail("Canonical read or paper preparation reached the narrative model.")


@requires_postgres
def test_daily_review_and_analytics_preserve_pending_paper_confirmation(interactive_world):
    w = interactive_world
    proposal = interactive_prepare(w)
    service = interactive_service(w)
    service._responder = ForbiddenResponder()
    review = service.handle_turn(
        AgentTurnRequest(message="What happened today?", conversation_id=proposal.conversation_id),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )
    assert review.capability is AgentCapability.DAILY_REVIEW
    assert review.daily_review is not None
    assert not review.proposals
    analytics = service.handle_turn(
        AgentTurnRequest(
            message="How did Nested perform?", conversation_id=proposal.conversation_id
        ),
        organization_id=ORG_ID,
        user_id=USER_ID,
    )
    assert analytics.capability is AgentCapability.STRATEGY_ANALYTICS
    assert not analytics.proposals
    assert analytics.daily_review is None
    w.session.commit()
    assert count(w, ExecutionFillFact) == count(w, JournalTrade) == 0
    assert interactive_confirm(w, proposal).status is ProposalLifecycle.APPLIED
    assert count(w, ExecutionFillFact) == count(w, JournalTrade) == 1
    assert interactive_confirm(w, proposal).status is ProposalLifecycle.APPLIED
    assert count(w, ExecutionFillFact) == count(w, JournalTrade) == 1


@pytest.mark.parametrize(
    "action",
    [
        ActionRequest(name="journal.create", arguments={"text": "Record a journal draft"}),
        ActionRequest(name="daily_review.read"),
    ],
)
def test_analytics_filters_cannot_reinterpret_a_typed_action(agent_db, action):
    factory, settings = agent_db
    with factory() as session:
        before = session.scalar(select(func.count()).select_from(ConversationMessage))
        service = InteractiveAgentService(session, settings=settings)
        with pytest.raises(ValidationAppError, match="read-only"):
            service.handle_turn(
                AgentTurnRequest(
                    message="Read metrics",
                    action=action,
                    analytics_filters=StrategyAnalyticsFilters(),
                ),
                organization_id=ORG_A,
                user_id=USER_A,
            )
        assert session.scalar(select(func.count()).select_from(ConversationMessage)) == before
