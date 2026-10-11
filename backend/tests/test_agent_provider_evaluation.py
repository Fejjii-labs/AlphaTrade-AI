"""Runner/admission evidence only. Fixture completions are never measured model quality."""

import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from scripts.evaluate_agent_provider import main
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.agent_capture.provider_evaluation import (
    CappedProvider,
    EvaluationBlockedError,
    EvaluationCase,
    EvaluationLimits,
    SpendingGuard,
    atomic_report,
    evaluate,
)
from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.db.models import KillSwitchState, ModelCallAttempt, Order, UsageEvent
from app.providers.base import ProviderKind
from app.providers.llm import LLMCompletionRequest, LLMCompletionResult, LLMMessage


@pytest.fixture
def limits():
    # Test rates validate mathematics only; these are NOT prices for the configured model.
    return EvaluationLimits(
        budget_reference="fixture-existing-budget",
        remaining_budget_usd=Decimal("1"),
        model=Settings(_env_file=None).agent_reasoning_model,
        price_source="https://example.invalid/test-only-price-receipt",
        price_valid_until=datetime.now(UTC) + timedelta(hours=1),
        input_usd_per_million=Decimal("1"),
        output_usd_per_million=Decimal("2"),
        maximum_input_tokens=20_000,
    )


def request(limits, **changes):
    values = {"messages": [LLMMessage("user", "hello")], "model": limits.model, "max_tokens": 1000}
    return LLMCompletionRequest(**(values | changes))


@pytest.mark.parametrize("cap", ["0", "-1", "NaN", "Infinity", "1.01"])
def test_cap_must_be_finite_positive_and_within_existing_budget(limits, cap):
    with pytest.raises(EvaluationBlockedError, match="cap_exceeds"):
        SpendingGuard(limits, Decimal(cap), 1000, {}, lambda: pytest.fail("persist before refusal"))


@pytest.mark.parametrize(
    "field", ["remaining_budget_usd", "input_usd_per_million", "output_usd_per_million"]
)
@pytest.mark.parametrize("invalid", ["0", "NaN"])
def test_no_missing_or_nonfinite_billing_inputs(limits, field, invalid):
    with pytest.raises(ValidationError):
        EvaluationLimits.model_validate(limits.model_dump() | {field: invalid})


def test_price_receipt_requires_timezone_and_current_validity(limits):
    with pytest.raises(ValidationError, match="timezone"):
        EvaluationLimits.model_validate(limits.model_dump() | {"price_valid_until": datetime.now()})
    expired = limits.model_copy(
        update={"price_valid_until": datetime.now(UTC) - timedelta(seconds=1)}
    )
    with pytest.raises(EvaluationBlockedError, match="expired"):
        SpendingGuard(expired, Decimal("1"), 1000, {}, lambda: None)


def test_case_and_possible_retry_are_admitted_before_transport(limits, tmp_path):
    path = tmp_path / "private.json"
    report = {"reservations": []}
    guard = SpendingGuard(
        limits, Decimal("0.044"), 1000, report, lambda: atomic_report(path, report)
    )
    guard.begin("one")
    assert json.loads(path.read_text())["reserved_upper_bound_usd"] == "0.044"
    guard.before_transport(request(limits))
    disk = json.loads(path.read_text())
    assert disk["reservations"][0]["attempts"][0]["state"] == "transport_may_start"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(EvaluationBlockedError, match="unfinished_case"):
        guard.begin("repeat")
    guard.before_transport(request(limits))
    with pytest.raises(EvaluationBlockedError, match="attempt_limit"):
        guard.before_transport(request(limits))
    guard.finish()
    assert guard.charged == Decimal("0.044")
    with pytest.raises(EvaluationBlockedError, match="case_and_retry"):
        guard.begin("two")


def test_unstarted_retry_allocation_can_be_released_but_ambiguous_attempt_cannot(limits):
    report = {"reservations": []}
    guard = SpendingGuard(limits, Decimal("1"), 1000, report, lambda: None)
    guard.begin("timeout")

    class TimeoutProvider:
        name, kind = "fixture-no-network", ProviderKind.LLM

        def complete(self, _):
            raise TimeoutError("fixture timeout")

    with pytest.raises(TimeoutError):
        CappedProvider(TimeoutProvider(), guard).complete(request(limits))
    guard.finish()
    assert guard.charged == Decimal("0.022")
    assert report["reservations"][0]["attempts"][0]["state"] == "error_or_ambiguous"


def test_failed_durable_admission_is_not_an_actual_provider_invocation(limits):
    report = {"reservations": []}
    guard = SpendingGuard(limits, Decimal("1"), 1000, report, lambda: None)
    guard.begin("one")

    def storage_denied():
        raise PermissionError("fixture denied receipt persistence")

    guard.persist = storage_denied

    class NeverProvider:
        name, kind = "fixture-no-network", ProviderKind.LLM

        def complete(self, _):
            pytest.fail("non-durable admission reached transport")

    with pytest.raises(PermissionError):
        CappedProvider(NeverProvider(), guard).complete(request(limits))
    assert not guard.current["attempts"][0].get("admitted_before_transport")
    assert not guard.current["attempts"][0].get("transport_invoked")


@pytest.mark.parametrize(
    "change",
    [
        {"model": "unapproved-fixture-route"},
        {"max_tokens": 1001},
        {"messages": [LLMMessage("user", "x" * 20_001)]},
    ],
)
def test_route_output_and_input_bounds_refuse_before_provider_call(limits, change):
    guard = SpendingGuard(limits, Decimal("1"), 1000, {"reservations": []}, lambda: None)
    guard.begin("one")

    class NeverProvider:
        name, kind = "fixture-no-network", ProviderKind.LLM

        def complete(self, _):
            pytest.fail("refused request reached transport")

    with pytest.raises(EvaluationBlockedError):
        CappedProvider(NeverProvider(), guard).complete(request(limits, **change))
    assert not guard.current["attempts"]


def test_atomic_private_report_does_not_follow_predictable_temp_symlink(tmp_path):
    report, target = tmp_path / "report.json", tmp_path / "untouched"
    target.write_text("preserve")
    report.with_name(report.name + ".tmp").symlink_to(target)
    atomic_report(report, {"receipt": "private"})
    assert target.read_text() == "preserve"
    assert json.loads(report.read_text()) == {"receipt": "private"}
    assert stat.S_IMODE(report.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    "response_change",
    [
        {"model": "unverified-fixture-route"},
        {"fallback_used": True},
        {"input_tokens": 20_001},
        {"output_tokens": 1001},
    ],
)
def test_unverified_response_stops_and_retains_full_attempt_reservation(limits, response_change):
    report = {"reservations": []}
    guard = SpendingGuard(limits, Decimal("1"), 1000, report, lambda: None)
    guard.begin("one")

    class UnverifiedResponse:
        name, kind = "fixture-no-network", ProviderKind.LLM

        def complete(self, req):
            values = {"model": req.model, "content": "{}", "provider": self.name}
            return LLMCompletionResult(**(values | response_change))

    with pytest.raises(EvaluationBlockedError, match="response_outside"):
        CappedProvider(UnverifiedResponse(), guard).complete(request(limits))
    guard.finish()
    assert guard.charged == Decimal("0.022")
    assert report["reservations"][0]["attempts"][0]["state"] == "error_or_ambiguous"


@pytest.mark.parametrize("block", ["credential", "mock", "model", "cases", "postgres", "receipt"])
def test_preflight_refusal_does_not_resolve_provider_or_write_database(
    limits, tmp_path, monkeypatch, block
):
    monkeypatch.setattr(
        "app.agent_capture.provider_evaluation.resolve_providers",
        lambda _: pytest.fail("provider resolved"),
    )
    monkeypatch.setattr(
        "app.agent_capture.provider_evaluation.create_engine",
        lambda *a, **kw: pytest.fail("database opened"),
    )
    settings = Settings(
        _env_file=None, provider_mode="fallback", openai_api_key="fixture-not-a-credential"
    )
    cases = [EvaluationCase(id="hello", text="Hello", expected_categories=[])]
    path, url = tmp_path / "report.json", "postgresql+psycopg://fixture/isolated"
    if block == "credential":
        settings.openai_api_key = ""
    elif block == "mock":
        settings.provider_mode = "mock"
    elif block == "model":
        limits = limits.model_copy(update={"model": "unapproved-fixture-route"})
    elif block == "cases":
        cases = []
    elif block == "postgres":
        url = "sqlite://"
    elif block == "receipt":
        path.write_text("retain original identity")
    with pytest.raises(EvaluationBlockedError):
        evaluate(settings, url, cases, limits, Decimal("1"), uuid4(), path, "fixture-revision")
    assert not path.exists() or path.read_text() == "retain original identity"


def test_cli_blocked_preflight_has_no_measured_metrics(monkeypatch, capsys):
    monkeypatch.setattr(
        "scripts.evaluate_agent_provider.Settings",
        lambda: Settings(_env_file=None, openai_api_key="", provider_mode="mock"),
    )
    monkeypatch.delenv("AGENT_EVAL_DATABASE_URL", raising=False)
    monkeypatch.setattr(sys, "argv", ["evaluate_agent_provider.py", "--preflight"])
    assert main() == 2
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == "blocked" and receipt["provider_calls"] == 0
    assert receipt["quality_metrics"] is receipt["latency_ms"] is receipt["usage"] is None


@pytest.fixture
def postgres_run():
    # Require the explicit disposable binding; never fall back to or reset public.
    url = os.environ.get("PHASE1_POSTGRES_URL")
    if not url:
        pytest.skip("PHASE1_POSTGRES_URL for disposable PostgreSQL is not explicitly bound")
    admin = create_engine(url, poolclass=NullPool)
    run_id = uuid4()
    schema = "agent_eval_" + run_id.hex
    with admin.connect() as connection:
        connection.execute(text("SELECT 1"))
    try:
        yield url, run_id, schema, admin
    finally:
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        admin.dispose()


@pytest.mark.parametrize("outcome", ["success", "retryable_rejection", "timeout", "cap_stops"])
def test_runner_keeps_real_attempt_accounting_and_never_labels_fixtures_as_quality(
    postgres_run, limits, tmp_path, monkeypatch, outcome
):
    url, run_id, schema, admin = postgres_run
    path = tmp_path / "report.json"
    calls = []
    cap = Decimal("0.05") if outcome == "cap_stops" else Decimal("0.25")

    class FixtureTransport:
        name, kind = "evaluation-fixture-not-a-model-measurement", ProviderKind.LLM

        def complete(self, req):
            before_io = json.loads(path.read_text())
            assert before_io["reservations"][0]["attempts"][-1]["state"] == "transport_may_start"
            assert Decimal(before_io["reserved_upper_bound_usd"]) <= cap
            calls.append(req)
            if outcome == "timeout":
                raise ServiceUnavailableError(
                    "fixture ambiguous timeout",
                    details={"reason": "openai_llm_timeout", "http_status": None},
                )
            if outcome == "retryable_rejection" and len(calls) == 1:
                raise ServiceUnavailableError("fixture rejected", details={"http_status": 503})
            return LLMCompletionResult(
                content='{"entries":[],"clarification":null}',
                model=req.model,
                provider=self.name,
                input_tokens=41,
                output_tokens=23,
            )

    monkeypatch.setattr(
        "app.agent_capture.provider_evaluation.resolve_providers",
        lambda _: SimpleNamespace(llm=FixtureTransport()),
    )
    settings = Settings(
        _env_file=None,
        provider_mode="fallback",
        openai_api_key="fixture-not-a-credential",
        agent_max_output_tokens=1024,
    )
    cases = [EvaluationCase(id="transport_probe", text="Hello", expected_categories=[])]
    if outcome in {"timeout", "cap_stops"}:
        cases.append(EvaluationCase(id="must_not_run", text="Hello again", expected_categories=[]))
    report = evaluate(settings, url, cases, limits, cap, run_id, path, "reviewed-fixture-revision")
    expected_calls = 2 if outcome == "retryable_rejection" else 1
    assert len(calls) == expected_calls
    assert report["evidence_kind"] == "deterministic_transport_test" and not report["measured"]
    assert report["metrics"] is report["latency_ms"] is report["usage"] is None
    assert report["actual_provider_charge_usd"] is None
    assert report["complete"] is (outcome not in {"timeout", "cap_stops"})
    assert not report["quality_accepted"]
    assert report["unexecuted_case_ids"] == (
        ["must_not_run"] if outcome in {"timeout", "cap_stops"} else []
    )
    if outcome == "cap_stops":
        assert report["stop_reason"] == "remaining_cap_cannot_cover_case_and_retry"
    assert report["diagnostics"]["cases_executed"] == 1
    assert report["diagnostics"]["negative_control_cases_total"] == len(cases)
    assert report["diagnostics"]["negative_control_cases_executed"] == 1
    assert len(report["cases"][0]["attempts"]) == expected_calls
    assert all(a["accounted"] for a in report["cases"][0]["attempts"])
    assert schema in inspect(admin).get_schema_names()
    engine = create_engine(
        admin.url.update_query_dict({"options": f"-csearch_path={schema}"}), poolclass=NullPool
    )
    try:
        with Session(engine) as session:
            attempts = list(session.scalars(select(ModelCallAttempt)))
            assert len(attempts) == expected_calls
            for attempt in attempts:
                event = session.get(UsageEvent, attempt.usage_event_id)
                assert event is not None and event.organization_id == attempt.organization_id
                assert (
                    event.user_id == attempt.user_id and event.input_tokens == attempt.input_tokens
                )
                assert event.output_tokens == attempt.output_tokens
                assert (
                    attempt.cost_source.value == "unavailable"
                    and event.provider_reported_cost is None
                )
            assert session.scalar(select(func.count()).select_from(Order)) == 0
            assert session.scalar(select(func.count()).select_from(KillSwitchState)) == 0
        with pytest.raises(EvaluationBlockedError, match="existing_run_receipt"):
            evaluate(settings, url, cases, limits, cap, run_id, path, "same-fixture")
        assert len(calls) == expected_calls
        assert json.loads(path.read_text())["revision"] == "reviewed-fixture-revision"
    finally:
        engine.dispose()
