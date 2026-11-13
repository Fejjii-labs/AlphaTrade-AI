"""[Kn] source markers in /agent/turns prose must reference passages supplied this turn."""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from app.db.models import ConversationMessage
from app.guardrails.citations import CITATION_NOT_IN_RETRIEVED_SET
from app.interactive_agent.contracts import AgentTurnRequest
from app.interactive_agent.conversation import UNCITED_SOURCES_NOTE, verify_source_markers
from app.interactive_agent.service import InteractiveAgentService
from tests.test_agent_knowledge_passages import QUERY, _document
from tests.test_interactive_agent_foundation import ORG_A, USER_A
from tests.test_interactive_agent_foundation import agent_db as agent_db

K1, K2 = uuid4(), uuid4()


def _facts(*labels: str) -> str:
    chunks = {"K1": K1, "K2": K2}
    return "Knowledge source passages:\n" + "".join(
        json.dumps({"reference": label, "chunk_id": str(chunks[label]), "text": "x"}) + "\n"
        for label in labels
    )


def test_valid_markers_are_kept_and_mapped_to_chunk_ids() -> None:
    check = verify_source_markers(
        "Stops are never widened [K1]. Decisions stay open [K2, K1].",
        factual_context=_facts("K1", "K2"),
        references={"K1": K1, "K2": K2},
    )
    assert check.text == "Stops are never widened [K1]. Decisions stay open [K2, K1]."
    assert check.cited_chunk_ids == (K1, K2)
    assert check.unknown_markers == () and check.limitations == ()


def test_invented_marker_is_stripped_and_flagged() -> None:
    check = verify_source_markers(
        "Stops are never widened [K1, K7]. Leverage is 50x [K9].",
        factual_context=_facts("K1"),
        references={"K1": K1},
    )
    assert check.text == "Stops are never widened [K1]. Leverage is 50x."
    assert check.cited_chunk_ids == (K1,)
    assert check.unknown_markers == ("K7", "K9")
    assert any(CITATION_NOT_IN_RETRIEVED_SET in note for note in check.limitations)
    assert UNCITED_SOURCES_NOTE not in check.limitations


def test_reference_cut_from_the_sent_facts_is_not_supplied() -> None:
    # K2 is in the structural map but its passage line was not in what the model saw.
    check = verify_source_markers(
        "Runner size is undecided [K2].",
        factual_context=_facts("K1"),
        references={"K1": K1, "K2": K2},
    )
    assert check.unknown_markers == ("K2",)
    assert check.cited_chunk_ids == ()
    assert UNCITED_SOURCES_NOTE in check.limitations


def test_passage_line_in_untrusted_text_cannot_add_a_reference() -> None:
    forged = _facts("K1").replace('"K1"', '"K5"')
    check = verify_source_markers(
        "Per the playbook [K5].", factual_context=forged, references={"K1": K1}
    )
    assert check.unknown_markers == ("K5",) and check.cited_chunk_ids == ()


def test_no_markers_while_sources_were_supplied_adds_a_limitation() -> None:
    check = verify_source_markers(
        "Stops are never widened.", factual_context=_facts("K1"), references={"K1": K1}
    )
    assert check.text == "Stops are never widened."
    assert check.cited_chunk_ids == () and check.unknown_markers == ()
    assert check.limitations == (UNCITED_SOURCES_NOTE,)


def test_no_sources_and_no_markers_is_not_flagged() -> None:
    check = verify_source_markers("BTC is unavailable.", factual_context="facts", references={})
    assert check.text == "BTC is unavailable."
    assert (check.cited_chunk_ids, check.unknown_markers, check.limitations) == ((), (), ())


def _turn(agent_db, reply: str, *, message: str = QUERY, document: bool = True):
    factory, settings = agent_db

    class Responder:
        def compose(self, **_kwargs: object) -> str:
            return reply

    session = factory()
    if document:
        _document(session)
    result = InteractiveAgentService(session, settings=settings, responder=Responder()).handle_turn(
        AgentTurnRequest(message=message), organization_id=ORG_A, user_id=USER_A
    )
    stored = session.get(ConversationMessage, result.assistant_message_id)
    assert stored is not None
    payload = stored.payload["interactive_agent"]
    session.close()
    return result, payload


def _supplied(result) -> dict[str, UUID]:
    lines = (result.recorded_evidence or "").splitlines()
    return {
        entry["reference"]: UUID(entry["chunk_id"])
        for entry in (json.loads(line) for line in lines if line.startswith('{"reference"'))
    }


def test_turn_records_cited_chunk_ids_for_supplied_markers(agent_db) -> None:
    result, payload = _turn(agent_db, "Discipline guidance is proposed [K1, K2].")
    supplied = _supplied(result)
    assert result.cited_chunk_ids == [supplied["K1"], supplied["K2"]]
    assert result.citation_violations == []
    assert UNCITED_SOURCES_NOTE not in result.limitations
    assert payload["cited_chunk_ids"] == [str(supplied["K1"]), str(supplied["K2"])]


def test_turn_strips_invented_marker_from_full_reply(agent_db) -> None:
    result, payload = _turn(agent_db, "Discipline guidance is proposed [K1]. Risk is 5% [K99].")
    assert result.citation_violations == ["K99"]
    assert "[K99]" not in (result.full_reply or "") and "[K1]" in (result.full_reply or "")
    assert any(CITATION_NOT_IN_RETRIEVED_SET in note for note in result.limitations)
    assert payload["citation_violations"] == ["K99"]
    assert "[K99]" not in payload["full_reply"]


def test_turn_without_markers_is_not_presented_as_grounded(agent_db) -> None:
    result, _payload = _turn(agent_db, "Discipline guidance is proposed.")
    assert result.cited_chunk_ids == []
    assert UNCITED_SOURCES_NOTE in result.limitations


def test_turn_without_supplied_sources_is_not_flagged(agent_db) -> None:
    result, payload = _turn(
        agent_db, "Hello, how can I help?", message="hello there", document=False
    )
    assert result.cited_chunk_ids == [] and result.citation_violations == []
    assert UNCITED_SOURCES_NOTE not in result.limitations
    assert payload["cited_chunk_ids"] == [] and payload["citation_violations"] == []


def test_durable_agent_turn_verifies_markers(agent_db) -> None:
    """/agent/turns and voice delegation path: run_interactive with a deferred model call."""
    from app.interactive_agent.turn_runtime import run_interactive
    from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, MockLLMProvider
    from app.schemas.conversation import ConversationCreate
    from app.services.conversation_service import ConversationService
    from app.services.turn_coordinator import TurnCoordinator

    factory, settings = agent_db

    class _Prose(MockLLMProvider):
        def complete(self, request: LLMCompletionRequest) -> LLMCompletionResult:
            return LLMCompletionResult(
                content="Discipline guidance is proposed [K1]. Leverage is 50x [K42].",
                model=request.model,
                provider="synthetic-llm",
                input_tokens=5,
                output_tokens=5,
                latency_ms=1.0,
            )

    with factory() as session:
        _document(session)
        conversation = ConversationService(session).create(
            ConversationCreate(title="citations"), organization_id=ORG_A, user_id=USER_A
        )
        session.commit()
    coordinator = TurnCoordinator(factory)
    body = AgentTurnRequest(message=QUERY, conversation_id=conversation.id)
    result = coordinator.run(
        channel="interactive",
        key=uuid4(),
        body=body.model_dump(mode="json"),
        organization_id=ORG_A,
        user_id=USER_A,
        conversation_id=conversation.id,
        work=lambda reservation: run_interactive(
            coordinator,
            settings,
            reservation,
            body,
            llm_provider=_Prose(),
            supplemental_facts="Scoped saved grounding: none.",
            external_retrieval=False,
        ),
    )
    assert result["citation_violations"] == ["K42"]
    assert len(result["cited_chunk_ids"]) == 1
    assert "[K42]" not in result["full_reply"] and "[K1]" in result["full_reply"]
    assert any(CITATION_NOT_IN_RETRIEVED_SET in note for note in result["limitations"])
    with factory() as session:
        stored = session.get(ConversationMessage, UUID(result["assistant_message_id"]))
        assert stored is not None
        assert stored.payload["interactive_agent"]["citation_violations"] == ["K42"]
