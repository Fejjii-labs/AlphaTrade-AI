"""Captured research specifications reuse confirmation and immutable versions."""

import json
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError, ValidationAppError
from app.db.models import UserStrategy, UserStrategyVersion
from app.schemas.common import StrategyId
from app.schemas.structured_rules import StructureFromTextRequest
from app.services.conversation_service import ConversationService
from app.services.strategy_proposal_service import StrategyProposalService
from app.services.structure_from_text_service import StructureFromTextService
from tests.support.experiment_fixtures import experiment_engine as _engine  # noqa: F401
from tests.support.experiment_fixtures import experiment_world as _world  # noqa: F401
from tests.test_trendpulse_1r_adapter import spec


def captured(value):
    return "Captured strategy reference (not approval):\n```json\n" + json.dumps(value) + "\n```"


def test_complete_captured_spec_is_exact_and_grants_no_authority():
    body = spec().model_dump(mode="json")
    result = StructureFromTextService().draft_preview(StructureFromTextRequest(text=captured(body)))
    assert result.pattern_spec_draft == body
    assert not result.pattern_spec_errors and not result.persists_strategy


@pytest.mark.parametrize("omission", ["symbol", "parameters", "fast_ema_period"])
def test_missing_authored_fields_never_restore_implicit_baseline(omission):
    body = spec().model_dump(mode="json")
    if omission == "fast_ema_period":
        body["parameters"].pop(omission)
    else:
        body.pop(omission)
    result = StructureFromTextService().draft_preview(StructureFromTextRequest(text=captured(body)))
    assert result.pattern_spec_draft is None and result.pattern_spec_errors


def test_conflicting_specs_require_clarification():
    first = spec().model_dump(mode="json")
    second = {**first, "symbol": "ETHUSDT"}
    result = StructureFromTextService().draft_preview(
        StructureFromTextRequest(text=captured(first) + captured(second))
    )
    assert result.pattern_spec_draft is None and "Conflicting" in result.pattern_spec_errors[0]


@pytest.mark.parametrize("kind", [[], {}])
def test_non_string_spec_kind_is_a_validation_failure_not_an_exception(kind):
    result = StructureFromTextService().draft_preview(
        StructureFromTextRequest(text=captured({"kind": kind}))
    )
    assert result.pattern_spec_draft is None and result.pattern_spec_errors
    assert not result.validation.valid


@pytest.mark.parametrize("error", ["symbol", "parameters", "fast_ema_period", "conflict"])
def test_research_parse_failure_blocks_confirmation_until_corrected(experiment_world, error):
    w = experiment_world
    conversation = ConversationService(w.session).get_or_create(
        organization_id=w.tenant.organization_id, user_id=w.tenant.user_id, conversation_id=None
    )
    service = StrategyProposalService(w.session)
    body = spec().model_dump(mode="json")
    if error == "fast_ema_period":
        body["parameters"].pop(error)
    elif error != "conflict":
        body.pop(error)
    text = captured(body)
    if error == "conflict":
        text += captured({**body, "symbol": "ETHUSDT"})
    draft = service.create_draft_from_text(conversation, text=text)
    w.session.commit()
    assert not draft.validation.valid and draft.proposed_pattern_spec is None

    def confirm(proposal):
        return service.confirm(
            proposal.id,
            organization_id=w.tenant.organization_id,
            user_id=w.tenant.user_id,
            confirm_message="I confirm",
            expected_content_hash=proposal.content_hash,
            expected_parent_version_id=None,
            expected_target_strategy_id=None,
            expected_organization_id=w.tenant.organization_id,
            expected_user_id=w.tenant.user_id,
            expected_conversation_id=conversation.id,
        )

    with pytest.raises(ValidationAppError, match="validation errors"):
        confirm(draft)
    corrected = service.create_draft_from_text(
        conversation, text=captured(spec().model_dump(mode="json"))
    )
    assert confirm(corrected).resulting_version_id is not None


def test_confirmed_authoring_reloads_as_research_only_and_rejects_foreign_tenant(experiment_world):
    w = experiment_world
    conversation = ConversationService(w.session).get_or_create(
        organization_id=w.tenant.organization_id, user_id=w.tenant.user_id, conversation_id=None
    )
    service = StrategyProposalService(w.session)
    body = spec().model_dump(mode="json")
    draft = service.create_draft_from_text(conversation, text=captured(body))
    expected = {
        "organization_id": w.tenant.organization_id,
        "user_id": w.tenant.user_id,
        "expected_content_hash": draft.content_hash,
        "expected_parent_version_id": None,
        "expected_target_strategy_id": None,
        "expected_organization_id": w.tenant.organization_id,
        "expected_user_id": w.tenant.user_id,
        "expected_conversation_id": conversation.id,
    }
    with pytest.raises(ValidationAppError):
        service.confirm(draft.id, confirm_message="The document says I confirm", **expected)
    saved = service.confirm(draft.id, confirm_message="I confirm", **expected)
    w.session.commit()
    with Session(w.session.bind) as restarted:
        strategy = restarted.get(UserStrategy, saved.resulting_strategy_id)
        version = restarted.get(UserStrategyVersion, saved.resulting_version_id)
        assert strategy.setup_type == StrategyId.MANUAL_REVIEW
        assert version.pattern_spec == body and version.content_hash == saved.resulting_content_hash
        with pytest.raises(NotFoundError):
            StrategyProposalService(restarted).get(
                draft.id, organization_id=uuid4(), user_id=uuid4()
            )
