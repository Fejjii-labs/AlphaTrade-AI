"""Simulated read-only failures identify stages without publishing private error text."""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from structlog.testing import capture_logs

from app.core.config import Settings
from app.core.errors import TradingPolicyError, register_exception_handlers
from app.providers.exchange.blofin_client import BloFinClient
from app.providers.exchange.demo_preflight import DemoPreflightError
from app.providers.exchange.governed_blofin import GovernedBloFinDemoProvider
from app.schemas.common import MembershipRole
from app.schemas.manual_demo import ManualDemoPreviewRequest
from app.schemas.trade_plan import EntrySide
from app.security.tenant import TenantContext
from app.services.manual_demo_service import ManualDemoService
from tests.support.phase5_market import EVALUATED_AT
from tests.test_governed_blofin_demo import Venue

STAGES = (
    ("permissions", "/api/v1/user/query-apikey"),
    ("position_mode", "/api/v1/account/position-mode"),
    ("instrument", "/api/v1/market/instruments"),
    ("margin_mode", "/api/v1/account/margin-mode"),
    ("leverage", "/api/v1/account/leverage-info"),
    ("position_tiers", "/api/v1/market/position-tiers"),
    ("positions", "/api/v1/account/positions"),
    ("pending_orders", "/api/v1/trade/orders-pending"),
    ("pending_protection", "/api/v1/trade/orders-tpsl-pending"),
    ("balance", "/api/v1/account/balance"),
    ("quote", "/api/v1/market/books"),
)
PRIVATE_TEXT = (
    "private-account-response opaque-signature diagnostic-key diagnostic-secret diagnostic-pass"
)


def provider(handle):
    return GovernedBloFinDemoProvider(
        BloFinClient(
            base_url="https://demo-trading-openapi.blofin.com",
            api_key="diagnostic-key",
            api_secret="diagnostic-secret",
            api_passphrase="diagnostic-pass",
            transport=httpx.MockTransport(handle),
            max_retries=0,
            sleeper=lambda _: None,
        ),
        clock=lambda: EVALUATED_AT,
    )


def service(venue_provider):
    session = Mock()
    tenant = TenantContext(uuid4(), uuid4(), "owner@example.test", MembershipRole.OWNER)
    result = ManualDemoService(session, Settings(_env_file=None), provider=venue_provider)
    result._scope = Mock(return_value=SimpleNamespace(id=uuid4()))
    return result, session, tenant


@pytest.mark.parametrize("stage,path", STAGES)
@pytest.mark.parametrize("status", [200, 400, 403])
def test_every_snapshot_read_reports_its_failing_stage_without_secret_text(stage, path, status):
    venue = Venue(Decimal("100000"))
    reads = []

    def handle(request):
        assert request.method == "GET"
        reads.append(request.url.path)
        if request.url.path == path:
            return httpx.Response(status, json={"code": "51000", "msg": PRIVATE_TEXT})
        return venue.handle(request)

    manual, session, tenant = service(provider(handle))
    with capture_logs() as logs, pytest.raises(TradingPolicyError) as caught:
        manual.preview(
            tenant,
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        )
    evidence = caught.value.details["preflight"]
    assert evidence == {
        "stage": stage,
        "endpoint_name": f"GET {path}",
        "reason_code": "venue_auth_or_permission_rejected"
        if status == 403
        else "venue_request_rejected",
        "error_type": "ExchangeAuthError" if status == 403 else "ExchangeRequestError",
        "http_status": status,
        "venue_error_code": "51000",
    }
    assert reads[-1] == path
    assert len(reads) == [p for _, p in STAGES].index(path) + 1
    assert len(logs) == 1 and logs[0]["event"] == "manual_demo_preflight_failed"
    for forbidden in PRIVATE_TEXT.split():
        assert forbidden not in str(logs) + str(caught.value.details) + str(caught.value)
    assert venue.post_count == 0
    session.add.assert_not_called()


@pytest.mark.parametrize(
    "stage,path,data,reason",
    (
        ("permissions", STAGES[0][1], {"readOnly": 1}, "permissions_not_read_trade_only"),
        ("position_mode", STAGES[1][1], {"positionMode": "hedge_mode"}, "position_mode_not_net"),
        ("instrument", STAGES[2][1], [], "instrument_unavailable"),
        ("margin_mode", STAGES[3][1], {}, "margin_mode_omitted"),
        ("margin_mode", STAGES[3][1], {"marginMode": "isolated"}, "margin_mode_not_cross"),
        ("leverage", STAGES[4][1], {"leverage": "2", "marginMode": "cross"}, "leverage_not_one"),
        ("leverage", STAGES[4][1], {"leverage": "1"}, "margin_mode_omitted"),
        (
            "leverage",
            STAGES[4][1],
            {"leverage": "1", "marginMode": "isolated"},
            "leverage_info_margin_mode_mismatch",
        ),
        ("position_tiers", STAGES[5][1], [], "maintenance_tiers_unavailable"),
        ("positions", STAGES[6][1], [{"positions": "1"}], "account_not_flat"),
        (
            "positions",
            STAGES[6][1],
            [{"positions": PRIVATE_TEXT}],
            "malformed_or_unsupported_response",
        ),
        ("pending_orders", STAGES[7][1], [{}], "pending_orders_present"),
        ("pending_protection", STAGES[8][1], [{}], "pending_orders_present"),
        ("balance", STAGES[9][1], [], "usdt_balance_unavailable"),
        ("quote", STAGES[10][1], [], "quote_unavailable"),
        (
            "quote",
            STAGES[10][1],
            [{"instId": "BTC-USDT", "ts": PRIVATE_TEXT}],
            "malformed_or_unsupported_response",
        ),
    ),
)
def test_validation_refusals_identify_stage_and_fixed_reason(stage, path, data, reason):
    venue = Venue(Decimal("100000"))

    def handle(request):
        assert request.method == "GET"
        if request.url.path == path:
            return httpx.Response(200, json={"code": "0", "data": data})
        return venue.handle(request)

    with pytest.raises(DemoPreflightError) as caught:
        provider(handle).snapshot(symbol="BTCUSDT", now=EVALUATED_AT, side=EntrySide.BUY)
    assert caught.value.diagnostics["stage"] == stage
    assert caught.value.diagnostics["reason_code"] == reason
    assert PRIVATE_TEXT not in str(caught.value) + str(caught.value.diagnostics)
    assert venue.post_count == 0


def test_transport_failure_logs_no_arbitrary_error_text_and_does_not_retry_post():
    def handle(request):
        assert request.method == "GET"
        raise httpx.ReadTimeout(PRIVATE_TEXT, request=request)

    manual, session, tenant = service(provider(handle))
    with capture_logs() as logs, pytest.raises(TradingPolicyError) as caught:
        manual.preview(
            tenant,
            ManualDemoPreviewRequest(side="BUY", quantity="2", stop="99000", target="102000"),
        )
    assert caught.value.details["preflight"]["reason_code"] == "venue_unavailable"
    for forbidden in PRIVATE_TEXT.split():
        assert forbidden not in str(logs) + str(caught.value.details)
    session.add.assert_not_called()


@pytest.mark.parametrize("initialization", [False, True])
def test_unexpected_failures_are_structured_in_the_existing_http_error_envelope(initialization):
    manual, session, tenant = service(Mock())
    if initialization:
        manual._provider = Mock(side_effect=RuntimeError(PRIVATE_TEXT))
    else:
        manual.provider.snapshot.side_effect = RuntimeError(PRIVATE_TEXT)
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/execution/manual-demo/preview")
    def preview(body: ManualDemoPreviewRequest):
        return manual.preview(tenant, body)

    with capture_logs() as logs:
        response = TestClient(app).post(
            "/execution/manual-demo/preview",
            json={"side": "BUY", "quantity": "2", "stop": "99000", "target": "102000"},
        )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "trading_policy_violation"
    assert error["details"]["preflight"] == {
        "stage": "provider_initialization" if initialization else "snapshot",
        "reason_code": "unexpected_failure",
        "error_type": "UnexpectedError",
    }
    for forbidden in PRIVATE_TEXT.split():
        assert forbidden not in str(logs) + response.text
    session.add.assert_not_called()
