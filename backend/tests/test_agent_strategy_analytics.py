"""Brain Agent canonical analytics reads, provenance and authorization boundaries."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    ConversationMessage,
    JournalTrade,
    Order,
    StrategyConversationProposal,
    TradeJournal,
    UserStrategy,
    UserStrategyVersion,
)
from app.db.strategy_brain import BrainSetupEventRow, BrainSetupRow
from app.interactive_agent.classify import classify_turn
from app.interactive_agent.contracts import AgentCapability, TurnOperation
from app.interactive_agent.conversation import ModelConversationalResponder
from app.interactive_agent.strategy_analytics import read_strategy_analytics
from app.schemas.strategy_analytics import StrategyAnalyticsDimension, StrategyAnalyticsFilters
from app.services.performance_service import PerformanceService
from app.services.strategy_analytics_service import StrategyAnalyticsService
from tests.test_strategy_analytics_api_v2 import (
    TRADER,
    VIEWER,
    _Api,
)
from tests.test_strategy_analytics_api_v2 import (
    api as analytics_api,
)
from tests.test_strategy_analytics_api_v2 import (
    client as analytics_client,
)
from tests.test_strategy_analytics_api_v2 import (
    records as analytics_records,
)
from tests.test_strategy_analytics_foundation import (
    ORG,
    OTHER_ORG,
    OTHER_USER,
    START,
    USER,
    brain,
    strategy,
    trade,
)

# Register existing fixture functions under their original dependency names.
api = analytics_api
client = analytics_client
records = analytics_records

PATH = "/agent/turns"


@pytest.mark.parametrize(
    "message",
    [
        "Compare my approved Nested and SFP strategies. What evidence is missing before execution?",
        "What is the difference between my Nested and SFP strategies?",
        "Compare the entry rules of my strategies.",
    ],
)
def test_definition_comparisons_read_strategy_library(message: str) -> None:
    result = classify_turn(message)
    assert result.capability is AgentCapability.STRATEGY_RETRIEVAL
    assert result.operation is TurnOperation.READ


@pytest.mark.parametrize(
    "message",
    ["Compare Nested and SFP win rate", "Compare strategy expectancy", "Compare N2 versus N3"],
)
def test_explicit_metric_comparisons_still_use_canonical_analytics(message: str) -> None:
    assert classify_turn(message).capability is AgentCapability.STRATEGY_ANALYTICS


GROUP_BY = (
    StrategyAnalyticsDimension.STRATEGY,
    StrategyAnalyticsDimension.STRATEGY_VERSION,
    StrategyAnalyticsDimension.NESTED_MATURITY_STAGE,
)


def _without_time(report: dict) -> dict:
    return {key: value for key, value in report.items() if key != "generated_at"}


def _authority_counts(session: Session) -> dict[str, int]:
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model))
        for model in (
            JournalTrade,
            TradeJournal,
            UserStrategy,
            UserStrategyVersion,
            BrainSetupRow,
            BrainSetupEventRow,
            Order,
            StrategyConversationProposal,
        )
    }


@pytest.mark.parametrize(
    "message",
    [
        "How did Nested perform?",
        "How did N2 perform?",
        "What is the win rate for N2?",
        "Compare N2 versus N3.",
        "How did BTC setups perform?",
        "What is expectancy for this strategy version?",
        "What are average R and median R?",
        "What is profit factor?",
        "How many samples exist?",
        "What are MAE and MFE?",
        "Which results have insufficient evidence?",
    ],
)
def test_performance_questions_choose_read_only_analytics(message: str) -> None:
    result = classify_turn(message)
    assert result.capability is AgentCapability.STRATEGY_ANALYTICS
    assert result.operation is TurnOperation.READ
    assert result.action_kind.value == "none"


@pytest.mark.parametrize(
    "message",
    [
        "Which Nested setups are forming?",
        "Is this N1, N2, N3 or N4 plus?",
    ],
)
def test_setup_state_questions_still_read_brain(message: str) -> None:
    assert classify_turn(message).capability is AgentCapability.STRATEGY_BRAIN


async def test_comparison_preserves_canonical_reports_and_only_writes_transcript(
    api: _Api,
    client: AsyncClient,
    records: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Analytics must not invoke a narrative model or rebuild paper performance")

    monkeypatch.setattr(ModelConversationalResponder, "compose", forbidden)
    monkeypatch.setattr(PerformanceService, "build_report", forbidden)
    with api.factory() as session:
        before = _authority_counts(session)
        expected = [
            StrategyAnalyticsService(session, max_rows=100)
            .compute(
                organization_id=ORG,
                user_id=USER,
                filters=StrategyAnalyticsFilters(
                    strategy_id=records["strategy_id"], nested_maturity_stage=stage
                ),
                group_by=GROUP_BY,
            )
            .model_dump(mode="json")
            for stage in ("N2", "N3")
        ]
    response = await client.post(
        PATH,
        headers=api.headers(),
        json={
            "message": "Compare N2 versus N3.",
            "strategy_id": records["strategy_id"],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["operation"] == "read"
    assert body["proposals"] == []
    assert body["execution_attempted"] is body["authority_mutated"] is False
    assert [_without_time(item) for item in body["strategy_analytics"]] == [
        _without_time(item) for item in expected
    ]
    assert "N2" in body["reply"] and "N3" in body["reply"]
    assert records["strategy_version_id"] in body["reply"]
    assert "insufficient evidence" in body["reply"]
    assert "cannot establish profitability" in body["reply"]
    assert "StrategyAnalyticsService.compute" in body["reply"]
    with api.factory() as session:
        assert _authority_counts(session) == before
        message = session.get(ConversationMessage, UUID(body["assistant_message_id"]))
        assert (
            message.payload["interactive_agent"]["strategy_analytics"] == body["strategy_analytics"]
        )


async def test_filters_and_version_are_preserved_from_authenticated_canonical_api(
    api: _Api,
    client: AsyncClient,
    records: dict[str, str],
) -> None:
    filters = {
        **records,
        "symbol": "BTCUSDT",
        "timeframe": "15m",
        "source": "paper_execution",
        "market_regime": "trending_up",
        "date_from": START.isoformat(),
        "date_to": (START + timedelta(hours=1)).isoformat(),
        "nested_maturity_stage": "N2",
    }
    response = await client.post(
        PATH,
        headers=api.headers(),
        json={
            "message": "What are expectancy, average R, median R, profit factor, MAE and MFE?",
            "analytics_filters": filters,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    canonical = await client.get(
        "/strategy-analytics/report",
        headers=api.headers(),
        params={
            **filters,
            "group_by": [dimension.value for dimension in GROUP_BY],
        },
    )
    assert canonical.status_code == 200, canonical.text
    assert _without_time(body["strategy_analytics"][0]) == _without_time(canonical.json())
    assert (
        body["strategy_analytics"][0]["overall"]["metric_samples"]["average_r"]["sample_count"] == 1
    )
    for label in (
        "expectancy",
        "average R",
        "median R",
        "profit factor",
        "MAE",
        "MFE",
        "Filters",
        "confidence",
        "Missing fields",
    ):
        assert label in body["reply"]


async def test_empty_and_missing_history_stays_unavailable(api: _Api, client: AsyncClient) -> None:
    for missing_trade in (False, True):
        if missing_trade:
            with api.factory() as session:
                trade(
                    session,
                    net_pnl=None,
                    gross_pnl=None,
                    planned_risk_amount=None,
                    mae_amount=None,
                    mfe_amount=None,
                    fees=None,
                    funding=None,
                    slippage=None,
                )
        response = await client.post(
            PATH, headers=api.headers(), json={"message": "What are MAE and MFE?"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        metrics = body["strategy_analytics"][0]["overall"]
        assert metrics["trade_count"] == int(missing_trade)
        for field in (
            "expectancy",
            "average_r",
            "median_r",
            "profit_factor",
            "average_mae_amount",
            "average_mfe_amount",
        ):
            assert metrics[field] is None
            assert metrics["metric_samples"][field]["available"] is False
            assert metrics["metric_samples"][field]["sample_count"] == 0
        assert "MAE (recorded amount): unavailable" in body["reply"]
        assert metrics["insufficient_history"] is True
        if missing_trade:
            assert metrics["missing_fields"]["net_pnl"] == 1
            assert "costs_unverified" in body["reply"]


async def test_unresolved_and_conflicting_selections_never_fall_back(
    api: _Api,
    client: AsyncClient,
    records: dict[str, str],
) -> None:
    for payload in (
        {"message": "How did Nested perform?"},  # fixture has multiple Nested strategies
        {"message": "How did NoSuchStrategy perform?"},
        {"message": "How did NoSuchStrategy perform?", "strategy_id": records["strategy_id"]},
        {
            "message": "Compare strategy version 1 versus version 2.",
            "strategy_id": records["strategy_id"],
        },
        {
            "message": "What is expectancy for this strategy version?",
            "strategy_id": records["strategy_id"],
        },
        {"message": "How did BTC setups perform last month?"},
        {"message": "How did BTC and ETH setups perform?"},
        {"message": "How did BTCUSDT and ETH setups perform?"},
        {"message": "How did BTC setups perform?", "analytics_filters": {"symbol": "ETHUSDT"}},
        {
            "message": "Compare N2 versus N3.",
            "strategy_id": records["strategy_id"],
            "analytics_filters": {"nested_maturity_stage": "N2"},
        },
        {
            "message": "What is expectancy?",
            "analytics_filters": {"strategy_version_id": str(uuid4())},
        },
    ):
        response = await client.post(PATH, headers=api.headers(), json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["strategy_analytics"] == []
        assert "clarification" in response.json()["reply"]
        assert response.json()["proposals"] == []


async def test_names_and_version_numbers_resolve_only_owned_exact_targets(
    api: _Api,
    client: AsyncClient,
) -> None:
    with api.factory() as session:
        owner, version = strategy(session)
        owner.name = "Nested research"
        session.commit()
        journal = trade(session, user_strategy_id=owner.id, strategy_version_id=version.id)
        brain(session, journal, owner=owner, version=version)
        owner_id, version_id = str(owner.id), str(version.id)
    for payload in (
        {"message": "How did Nested perform?"},
        {"message": "How did Nested research perform?"},
        {"message": "How did N2 perform?"},
        {"message": "What is expectancy for version 1?", "strategy_id": owner_id},
    ):
        response = await client.post(PATH, headers=api.headers(), json=payload)
        assert response.status_code == 200, response.text
        report = response.json()["strategy_analytics"][0]
        assert report["filters"]["strategy_id"] == owner_id
        assert report["buckets"][0]["dimensions"]["strategy_version"] == version_id
        if "version 1" in payload["message"]:
            assert report["filters"]["strategy_version_id"] == version_id


async def test_zero_comparison_side_is_an_empty_canonical_report(
    api: _Api,
    client: AsyncClient,
    records: dict[str, str],
) -> None:
    response = await client.post(
        PATH,
        headers=api.headers(),
        json={
            "message": "Compare N2 versus N3.",
            "strategy_id": records["strategy_id"],
            "analytics_filters": {"strategy_version_id": records["strategy_version_id"]},
        },
    )
    assert response.status_code == 200, response.text
    empty = response.json()["strategy_analytics"][1]
    assert empty["filters"]["nested_maturity_stage"] == "N3"
    assert empty["overall"]["trade_count"] == 0
    assert empty["overall"]["expectancy"] is None
    assert empty["buckets"] == []


async def test_principal_scope_cannot_be_overridden_and_foreign_lineage_is_not_exposed(
    api: _Api,
    client: AsyncClient,
    records: dict[str, str],
) -> None:
    with api.factory() as session:
        trade(session, organization_id=OTHER_ORG, user_id=OTHER_USER, net_pnl=Decimal("999999"))
        trade(session, user_id=TRADER, net_pnl=Decimal("777777"))
    owner = await client.post(
        PATH, headers=api.headers(), json={"message": "How did BTC setups perform?"}
    )
    assert owner.status_code == 200
    report = owner.json()["strategy_analytics"][0]
    assert report["user_id"] == str(USER) and report["organization_id"] == str(ORG)
    assert report["overall"]["trade_count"] == 3
    assert Decimal(report["overall"]["net_pnl_total"]) == 300
    for foreign_user, org in ((TRADER, ORG), (OTHER_USER, OTHER_ORG)):
        foreign = await client.post(
            PATH,
            headers=api.headers(foreign_user, org),
            json={
                "message": "What is expectancy?",
                "analytics_filters": records,
            },
        )
        assert foreign.status_code == 200
        assert foreign.json()["strategy_analytics"] == []
        assert "unavailable in your scope" in foreign.json()["reply"]
        bound = await client.post(
            PATH,
            headers=api.headers(foreign_user, org),
            json={
                "message": "How did Nested perform?",
                "strategy_id": records["strategy_id"],
            },
        )
        assert bound.status_code == 404
        conversation = await client.post(
            PATH,
            headers=api.headers(foreign_user, org),
            json={
                "message": "What is expectancy?",
                "conversation_id": owner.json()["conversation_id"],
            },
        )
        assert conversation.status_code == 404
    for spoof in (
        {"organization_id": str(OTHER_ORG)},
        {"user_id": str(OTHER_USER)},
        {"analytics_filters": {"user_id": str(OTHER_USER)}},
    ):
        response = await client.post(
            PATH, headers=api.headers(), json={"message": "What is expectancy?", **spoof}
        )
        assert response.status_code == 422


async def test_agent_http_requires_auth_and_preserves_existing_role_boundary(
    api: _Api,
    client: AsyncClient,
) -> None:
    payload = {"message": "How many samples exist?"}
    assert (await client.post(PATH, json=payload)).status_code == 401
    assert (
        await client.post(PATH, headers={"Authorization": "Bearer invalid"}, json=payload)
    ).status_code == 401
    assert (await client.post(PATH, headers=api.headers(VIEWER), json=payload)).status_code == 403
    assert (
        await client.post(PATH, headers=api.headers(USER, OTHER_ORG), json=payload)
    ).status_code == 401
    assert (await client.post(PATH, headers=api.headers(TRADER), json=payload)).status_code == 200


async def test_analytics_filters_cannot_route_mutations(api: _Api, client: AsyncClient) -> None:
    for message in ("Create a strategy", "Execute this paper trade", "Enable real trading"):
        response = await client.post(
            PATH,
            headers=api.headers(),
            json={
                "message": message,
                "analytics_filters": {},
            },
        )
        assert response.status_code == 422, response.text


def test_adapter_read_does_not_autoflush_pending_authority_changes(api: _Api) -> None:
    with api.factory() as session:
        owner, _version = strategy(session)
        original = owner.name
        owner.name = "Pending mutation"
        statements: list[str] = []

        def capture(_conn: object, _cursor: object, statement: str, *_args: object) -> None:
            statements.append(statement.lstrip().split()[0].upper())

        event.listen(api.engine, "before_cursor_execute", capture)
        try:
            reports, _reply, _limits = read_strategy_analytics(
                session,
                organization_id=ORG,
                user_id=USER,
                message="How did Nested perform?",
                strategy_id=owner.id,
            )
        finally:
            event.remove(api.engine, "before_cursor_execute", capture)
        assert len(reports) == 1
        assert statements and set(statements) == {"SELECT"}
        assert owner in session.dirty
        session.rollback()
        assert owner.name == original


async def test_service_failure_is_unavailable_not_empty_history(
    api: _Api,
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(StrategyAnalyticsService, "compute", fail)
    response = await client.post(
        PATH, headers=api.headers(), json={"message": "What is profit factor?"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["strategy_analytics"] == []
    assert "unavailable" in response.json()["reply"]
    assert "Closed samples: 0" not in response.json()["reply"]


async def test_row_cap_and_metric_coverage_cannot_be_presented_as_complete_history(
    api: _Api,
    client: AsyncClient,
) -> None:
    api.settings.journal_stats_max_rows = 2
    with api.factory() as session:
        for index in range(3):
            trade(
                session,
                planned_risk_amount=None,
                mae_amount=None,
                exit_time=START + timedelta(hours=index + 1),
            )
    response = await client.post(
        PATH,
        headers=api.headers(),
        json={
            "message": "Which results have insufficient evidence?",
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()["strategy_analytics"][0]
    assert report["max_rows"] == report["scanned_trade_count"] == 2
    assert report["truncated"] is True
    assert report["overall"]["metric_samples"]["expectancy"]["sample_count"] == 2
    assert report["overall"]["metric_samples"]["average_r"]["sample_count"] == 0
    assert report["overall"]["missing_fields"]["planned_risk_amount"] == 2
    assert "result_truncated" in response.json()["reply"]
    assert "truncated: True" in response.json()["reply"]
    assert "average R: unavailable (n=0; insufficient evidence)" in response.json()["reply"]
    assert "unassigned" in response.json()["reply"]
