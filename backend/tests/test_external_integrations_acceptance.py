"""External acceptance and missing integration safety cases; no live connectivity."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from structlog.testing import capture_logs

from app.core.blofin_readonly_access import BloFinReadOnlyClient, get_readonly_account_provider
from app.core.config import Settings, TelegramInboundMode
from app.core.errors import ExchangeDemoInactiveError
from app.db.models import BloFinDemoSyncSnapshot, PaperValidationCandidate, TradeProposal
from app.external_integrations.acceptance import FAIL, NOT_CONFIGURED, PASS, ApiAcceptance, run
from app.providers.exchange.errors import ExchangeError
from app.providers.exchange.factory import resolve_exchange_execution_provider
from app.schemas.paper_signal_orchestration import APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM
from app.security.tradingview_webhook import compute_tradingview_signature
from tests.test_at037_tradingview_blofin import (
    WEBHOOK_SECRET,
    _payload,
)
from tests.test_at037_tradingview_blofin import (
    client as tv_client,
)
from tests.test_at038_paper_signal_orchestration import (
    ORG_A,
    USER_A,
    _ingest,
    _login,
)
from tests.test_at038_paper_signal_orchestration import (
    client as pso_client,
)

# Imported fixtures keep the same foreign-key-enabled, authenticated test apps.
__all__ = ["pso_client", "tv_client"]

SECRETS = ("opaque-sync-key-17", "opaque-sync-secret-19", "opaque-sync-pass-23")


def readonly_settings(**overrides: Any) -> Settings:
    values = {
        "_env_file": None,
        "environment": "local",
        "provider_mode": "mock",
        "rate_limit_use_redis": False,
        "market_data_cache_use_redis": False,
        "execution_mode": "paper",
        "enable_real_trading": False,
        "exchange_mode": "paper_internal",
        "blofin_demo_enabled": False,
        "blofin_readonly_sync_enabled": True,
        "blofin_readonly_api_key": SECRETS[0],
        "blofin_readonly_api_secret": SECRETS[1],
        "blofin_readonly_api_passphrase": SECRETS[2],
        "blofin_demo_rest_base_url": "https://demo-trading-openapi.blofin.com",
        "blofin_max_retries": 0,
    }
    values.update(overrides)
    return Settings(**values)


def account_transport(
    requests: list[httpx.Request], *, permissions: Any = None
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "demo-trading-openapi.blofin.com"
        data = {
            "/api/v1/user/query-apikey": {"readOnly": 1, "uid": "acceptance-native-uid"}
            if permissions is None
            else permissions,
            "/api/v1/account/balance": [
                {"currency": "USDT", "balance": "1000", "available": "900"}
            ],
            "/api/v1/account/positions": [
                {
                    "instId": "BTC-USDT",
                    "positions": "0.1",
                    "positionSide": "long",
                    "averagePrice": "65000",
                }
            ],
        }[request.url.path]
        return httpx.Response(200, json={"code": "0", "data": data})

    return httpx.MockTransport(handle)


def test_readonly_sync_in_paper_internal_keeps_execution_sealed(tv_client, monkeypatch) -> None:
    client, _factory, settings = tv_client
    configured = readonly_settings()
    for field in (
        "blofin_readonly_sync_enabled",
        "blofin_readonly_api_key",
        "blofin_readonly_api_secret",
        "blofin_readonly_api_passphrase",
        "blofin_demo_rest_base_url",
    ):
        setattr(settings, field, getattr(configured, field))
    seen: list[httpx.Request] = []
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(cfg, transport=account_transport(seen)),
    )
    from tests.test_at037_tradingview_blofin import ORG_A as TV_ORG
    from tests.test_at037_tradingview_blofin import _login as tv_login

    client.headers.update(tv_login(client, "at037-a@test.example"))
    with capture_logs() as logs:
        report = ApiAcceptance(client, TV_ORG, WEBHOOK_SECRET).blofin()
    assert report["status"] == PASS
    assert len(seen) == 3
    assert resolve_exchange_execution_provider(settings) is None
    assert client.get("/exchange/blofin/sync/latest").json()["exchange_mode"] == "paper_internal"
    serialized = json.dumps({"report": report, "logs": logs}, default=str) + repr(settings)
    assert all(value not in serialized for value in SECRETS)


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("POST", "/api/v1/trade/order", {}),
        ("POST", "/api/v1/trade/cancel-order", {}),
        ("GET", "/api/v1/trade/order", None),
        ("GET", "/api/v1/asset/withdraw", None),
        ("GET", "/api/v1/asset/transfer", None),
        ("GET", "/api/v1/account/balance", {}),
        ("GET", "https://demo-trading-openapi.blofin.com/api/v1/account/balance", None),
    ],
)
def test_readonly_transport_refuses_all_other_capabilities(method, path, body) -> None:
    seen: list[httpx.Request] = []
    client = BloFinReadOnlyClient(
        base_url="https://demo-trading-openapi.blofin.com",
        api_key=SECRETS[0],
        api_secret=SECRETS[1],
        api_passphrase=SECRETS[2],
        transport=account_transport(seen),
    )
    with pytest.raises(ExchangeDemoInactiveError):
        client.request(method, path, body=body)
    assert not seen


@pytest.mark.parametrize(
    "overrides",
    [
        {"blofin_readonly_sync_enabled": False},
        {"blofin_readonly_api_key": ""},
        {"blofin_readonly_api_secret": ""},
        {"blofin_readonly_api_passphrase": ""},
    ],
)
def test_sync_does_not_use_stored_execution_credentials(overrides) -> None:
    settings = readonly_settings(
        blofin_api_key="execution-key",
        blofin_api_secret="execution-secret",
        blofin_api_passphrase="execution-pass",
        **overrides,
    )
    with pytest.raises(ExchangeDemoInactiveError):
        get_readonly_account_provider(settings, transport=account_transport([]))


def test_opaque_credentials_echoed_by_venue_are_redacted() -> None:
    def failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": "401", "msg": " ".join(SECRETS)})

    provider = get_readonly_account_provider(
        readonly_settings(), transport=httpx.MockTransport(failed)
    )
    with capture_logs() as logs, pytest.raises(ExchangeError) as caught:
        provider.get_account_permissions()
    diagnostic = json.dumps(logs, default=str) + str(caught.value) + repr(caught.value.details)
    assert all(secret not in diagnostic for secret in SECRETS)


@pytest.mark.parametrize("age,health", [(301, "ok"), (-30, "ok"), (0, "degraded")])
@pytest.mark.parametrize(
    "configuration", ["configured", "disabled", "missing_secret", "missing_origin"]
)
def test_orchestration_rechecks_snapshot_age_and_health(
    pso_client, age, health, configuration
) -> None:
    client, factory, settings = pso_client
    settings.blofin_readonly_sync_enabled = configuration != "disabled"
    settings.blofin_readonly_api_key = SECRETS[0]
    settings.blofin_readonly_api_secret = "" if configuration == "missing_secret" else SECRETS[1]
    settings.blofin_readonly_api_passphrase = SECRETS[2]
    settings.blofin_demo_rest_base_url = (
        "" if configuration == "missing_origin" else "https://demo-trading-openapi.blofin.com"
    )
    signal_id = _ingest(client, alert_id="freshness-regression")
    with factory() as session:
        session.add(
            BloFinDemoSyncSnapshot(
                organization_id=ORG_A,
                user_id=USER_A,
                synced_at=datetime.now(UTC) - timedelta(seconds=age),
                health_status=health,
                provider="blofin_demo",
                exchange_mode="paper_internal",
                account_snapshot={},
                positions_snapshot={},
                market_context={},
                provenance={},
                is_stale=False,
                position_count=0,
                balance_count=0,
            )
        )
        session.commit()
    headers = _login(client, "at038-a@test.example")
    response = client.post(
        f"/paper-signal-orchestration/signals/{signal_id}/orchestrate", headers=headers
    )
    assert response.status_code == 200
    decision = response.json()["decision"]
    if configuration == "configured":
        assert decision["status"] == "blocked"
        assert "market_context_unavailable" in decision["reason_codes"]
    else:
        assert decision["status"] == "eligible"
        assert "market_context_unavailable" not in decision["reason_codes"]
    with factory() as session:
        assert session.scalar(select(TradeProposal)) is None


@pytest.mark.parametrize("configuration", ["configured", "missing_secret", "missing_origin"])
def test_missing_blofin_configuration_does_not_block_internal_paper_signals(
    pso_client, configuration
) -> None:
    client, factory, settings = pso_client
    settings.blofin_readonly_sync_enabled = True
    settings.blofin_readonly_api_key = SECRETS[0]
    settings.blofin_readonly_api_secret = "" if configuration == "missing_secret" else SECRETS[1]
    settings.blofin_readonly_api_passphrase = SECRETS[2]
    settings.blofin_demo_rest_base_url = (
        "" if configuration == "missing_origin" else "https://demo-trading-openapi.blofin.com"
    )
    client.headers.update(_login(client, "at038-a@test.example"))
    api = ApiAcceptance(client, ORG_A, settings.tradingview_webhook_secret)
    if configuration != "configured":
        assert api.blofin()["status"] == NOT_CONFIGURED
    assert api.tradingview()["status"] == PASS
    if configuration == "configured":
        with pytest.raises(ValueError, match="Signal must be eligible"):
            api.orchestration()
    else:
        assert api.orchestration()["status"] == PASS
    with factory() as session:
        assert session.scalar(select(BloFinDemoSyncSnapshot)) is None
        assert session.scalar(select(TradeProposal)) is None


def test_archived_candidate_cannot_authorize_signal_proposal(pso_client) -> None:
    client, factory, settings = pso_client
    settings.paper_signal_orchestration_mode = "approval_required"
    signal = _ingest(client, alert_id="archived-regression")
    headers = _login(client, "at038-a@test.example")
    decision = client.post(
        f"/paper-signal-orchestration/signals/{signal}/orchestrate", headers=headers
    ).json()["decision"]
    with factory() as session:
        candidate = session.get(PaperValidationCandidate, UUID(decision["links"]["candidate_id"]))
        candidate.candidate_status = "archived"
        session.commit()
    response = client.post(
        f"/paper-signal-orchestration/decisions/{decision['id']}/approve-paper-proposal",
        headers=headers,
        json={"confirm": APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM},
    )
    assert response.status_code == 422
    with factory() as session:
        assert session.scalar(select(TradeProposal)) is None


def test_acceptance_api_tradingview_and_governed_orchestration(pso_client) -> None:
    client, factory, settings = pso_client
    client.headers.update(_login(client, "at038-a@test.example"))
    api = ApiAcceptance(client, ORG_A, settings.tradingview_webhook_secret)
    api.safety()
    assert api.blofin()["status"] == NOT_CONFIGURED
    with factory() as session:
        assert session.scalar(select(BloFinDemoSyncSnapshot)) is None
    assert api.tradingview()["status"] == PASS
    assert api.orchestration()["status"] == PASS
    with factory() as session:
        assert session.scalar(select(TradeProposal)) is None


def test_harness_rejects_wrong_token_organization_before_writes(pso_client) -> None:
    client, factory, settings = pso_client
    client.headers.update(_login(client, "at038-b@test.example"))
    with pytest.raises(ValueError, match="Token organization"):
        ApiAcceptance(client, ORG_A, settings.tradingview_webhook_secret).safety()
    with factory() as session:
        assert session.scalar(select(PaperValidationCandidate)) is None


def test_missing_api_credentials_never_pass_and_public_outages_are_fail() -> None:
    seen = []

    def failure(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        return httpx.Response(451, json={"msg": "deterministic regional refusal"})

    report = run(
        readonly_settings(),
        api_url="",
        token="",
        organization_id=None,
        market_transport=httpx.MockTransport(failure),
    )
    assert [item["status"] for item in report["results"]] == [FAIL, FAIL] + [NOT_CONFIGURED] * 3
    assert len(seen) == 2


@pytest.mark.parametrize(
    "field,value",
    [("telegram_network_permitted", True), ("telegram_inbound_mode", TelegramInboundMode.WEBHOOK)],
)
def test_unsafe_harness_does_no_network(field, value) -> None:
    settings = readonly_settings()
    setattr(settings, field, value)

    def forbidden(request: httpx.Request) -> httpx.Response:
        raise AssertionError("Unsafe settings must prevent all probes")

    report = run(
        settings,
        api_url="https://staging.example",
        token="secret",
        organization_id=ORG_A,
        api_transport=httpx.MockTransport(forbidden),
        market_transport=httpx.MockTransport(forbidden),
    )
    assert all(item["status"] == FAIL for item in report["results"])


def test_harness_rejects_telegram_inbound_before_deployed_writes(pso_client) -> None:
    client, factory, settings = pso_client
    settings.telegram_inbound_mode = TelegramInboundMode.WEBHOOK
    client.headers.update(_login(client, "at038-a@test.example"))
    with pytest.raises(ValueError, match="Telegram disarmed"):
        ApiAcceptance(client, ORG_A, settings.tradingview_webhook_secret).safety()
    with factory() as session:
        assert session.scalar(select(PaperValidationCandidate)) is None


def test_signed_tenant_tamper_and_future_timestamp_are_rejected(tv_client) -> None:
    client, _factory, _settings = tv_client
    raw = json.dumps(_payload()).encode()
    timestamp = int(time.time())
    signature = compute_tradingview_signature(raw, timestamp=timestamp, secret=WEBHOOK_SECRET)
    changed = json.dumps(_payload(organization_id=str(ORG_A))).encode()
    for body, moment, sig in (
        (changed, timestamp, signature),
        (
            raw,
            timestamp + 3601,
            compute_tradingview_signature(raw, timestamp=timestamp + 3601, secret=WEBHOOK_SECRET),
        ),
    ):
        response = client.post(
            "/webhooks/tradingview",
            content=body,
            headers={"X-AT-Timestamp": str(moment), "X-AT-Signature": sig},
        )
        assert response.status_code == 401


@pytest.mark.parametrize("signature", ["é" * 64, "g" * 64, "sha256=" + "!" * 64])
def test_non_hex_signature_fails_closed_without_type_error(signature) -> None:
    from app.security.tradingview_webhook import verify_tradingview_signature

    assert (
        verify_tradingview_signature(
            b"{}",
            signature_header=signature,
            timestamp_header="100",
            secret="opaque-secret",
            max_skew_seconds=30,
            now=100,
        )
        is False
    )


def test_future_signal_event_time_does_not_extend_authority(pso_client) -> None:
    client, _factory, _settings = pso_client
    signal = _ingest(
        client,
        alert_id="future-event",
        occurred_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    headers = _login(client, "at038-a@test.example")
    response = client.post(
        f"/paper-signal-orchestration/signals/{signal}/orchestrate", headers=headers
    )
    assert response.status_code == 200
    assert response.json()["decision"]["status"] == "rejected"
    assert "signal_future_time" in response.json()["decision"]["reason_codes"]


@pytest.mark.parametrize(
    "permission",
    [{"readOnly": 0}, {"permissions": "read,withdraw"}, {"permissions": "read,transfer"}],
)
def test_readonly_sync_refuses_unsafe_permission_before_account_fetch(
    tv_client, monkeypatch, permission
) -> None:
    client, _factory, settings = tv_client
    configured = readonly_settings()
    for field in (
        "blofin_readonly_sync_enabled",
        "blofin_readonly_api_key",
        "blofin_readonly_api_secret",
        "blofin_readonly_api_passphrase",
        "blofin_demo_rest_base_url",
    ):
        setattr(settings, field, getattr(configured, field))
    seen = []
    monkeypatch.setattr(
        "app.services.blofin_sync_service.get_readonly_account_provider",
        lambda cfg: get_readonly_account_provider(
            cfg, transport=account_transport(seen, permissions=[permission])
        ),
    )
    from tests.test_at037_tradingview_blofin import _login as tv_login

    response = client.post(
        "/exchange/blofin/sync", headers=tv_login(client, "at037-a@test.example")
    )
    assert response.status_code == 200
    assert response.json()["snapshot"]["health_status"] == "unavailable"
    assert [r.url.path for r in seen] == ["/api/v1/user/query-apikey"]


def test_risk_is_rechecked_after_signal_review_before_proposal(pso_client) -> None:
    from app.db.models import KillSwitchState

    client, factory, settings = pso_client
    settings.paper_signal_orchestration_mode = "approval_required"
    signal = _ingest(client, alert_id="review-risk-race")
    headers = _login(client, "at038-a@test.example")
    decision = client.post(
        f"/paper-signal-orchestration/signals/{signal}/orchestrate", headers=headers
    ).json()["decision"]
    with factory() as session:
        state = session.scalar(
            select(KillSwitchState).where(KillSwitchState.organization_id == ORG_A)
        )
        assert state is not None
        state.active = True
        state.reason = "risk changed after review"
        session.commit()
    response = client.post(
        f"/paper-signal-orchestration/decisions/{decision['id']}/approve-paper-proposal",
        headers=headers,
        json={"confirm": APPROVE_PAPER_SIGNAL_PROPOSAL_CONFIRM},
    )
    assert response.status_code == 422
    with factory() as session:
        assert session.scalar(select(TradeProposal)) is None


@pytest.mark.parametrize("warmup", [0, 2])
def test_acceptance_market_happy_path_uses_real_adapters_and_proof(monkeypatch, warmup) -> None:
    from app.external_integrations import acceptance
    from app.market_contracts.enums import VenueId
    from tests.test_market_intelligence_oi_funding import contract_payload

    now = datetime(2026, 10, 1, 12, 10, 2, tzinfo=UTC)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(acceptance, "datetime", FrozenDatetime)
    if warmup:
        from itertools import count
        from types import SimpleNamespace

        clock = count()
        monkeypatch.setattr(
            acceptance,
            "time",
            SimpleNamespace(
                monotonic=lambda: next(clock),
                sleep=lambda _seconds: None,
            ),
        )
    seen = []
    end = now - timedelta(seconds=2)
    print_times = [
        end - timedelta(minutes=10, seconds=1),
        end - timedelta(minutes=10),
        end - timedelta(minutes=5),
        end - timedelta(seconds=1),
        end + timedelta(seconds=1),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        assert "access-key" not in request.headers
        bybit = request.url.host == "api.bybit.com"
        path, params = request.url.path, request.url.params
        if path.endswith(("exchangeInfo", "instruments-info")):
            return httpx.Response(
                200, json=contract_payload(VenueId.BYBIT if bybit else VenueId.BINANCE)
            )
        if path.endswith(("klines", "kline")):
            interval = params["interval"]
            seconds = 900 if interval in {"15", "15m"} else 14400
            closed_end = int(now.timestamp()) // seconds * seconds
            limit = int(params["limit"])
            rows = []
            for index in range(limit):
                start = (closed_end - (limit - index) * seconds) * 1000
                rows.append(
                    [str(start), "100", "101", "99", "100", "1", "100"]
                    if bybit
                    else [
                        start,
                        "100",
                        "101",
                        "99",
                        "100",
                        "1",
                        start + seconds * 1000 - 1,
                        "100",
                        1,
                    ]
                )
            data = (
                {
                    "retCode": 0,
                    "result": {"category": "linear", "symbol": "BTCUSDT", "list": rows[::-1]},
                }
                if bybit
                else rows
            )
        elif path.endswith(("aggTrades", "recent-trade")):
            rows = []
            for index, at in enumerate(print_times):
                stamp = int(at.timestamp() * 1000)
                if bybit:
                    rows.append(
                        {
                            "execId": f"exec-{index}",
                            "symbol": "BTCUSDT",
                            "price": "100",
                            "size": "1",
                            "side": "Buy",
                            "time": str(stamp),
                        }
                    )
                elif int(params["startTime"]) <= stamp <= int(params["endTime"]):
                    rows.append({"a": index + 1, "p": "100", "q": "1", "m": False, "T": stamp})
            data = (
                {
                    "retCode": 0,
                    "result": {"category": "linear", "symbol": "BTCUSDT", "list": rows[::-1]},
                }
                if bybit
                else rows
            )
        elif path.endswith(("openInterest", "open-interest")):
            stamp = int(now.timestamp() * 1000)
            row = {
                "symbol": "BTCUSDT",
                "openInterest": "123",
                "timestamp" if bybit else "time": stamp,
            }
            data = (
                {"retCode": 0, "result": {"category": "linear", "symbol": "BTCUSDT", "list": [row]}}
                if bybit
                else row
            )
        elif path.endswith(("fundingRate", "funding/history")):
            row = {
                "symbol": "BTCUSDT",
                "fundingRate": "0.0001",
                "fundingRateTimestamp" if bybit else "fundingTime": int(now.timestamp() * 1000),
            }
            data = (
                {"retCode": 0, "result": {"category": "linear", "list": [row]}} if bybit else [row]
            )
        else:
            raise AssertionError(f"Unexpected endpoint {path}")
        return httpx.Response(200, json=data)

    report = run(
        readonly_settings(),
        api_url="",
        token="",
        organization_id=None,
        market_transport=httpx.MockTransport(handle),
        bybit_warmup_seconds=warmup,
    )
    assert [r["status"] for r in report["results"][:2]] == [PASS, PASS], report
    assert all(r["coverage_content_hash"] for r in report["results"][:2])
    assert {r.url.host for r in seen} == {"fapi.binance.com", "api.bybit.com"}


def test_invalid_cli_settings_emit_fail_without_secret_echo(monkeypatch, capsys) -> None:
    from app.external_integrations import acceptance

    monkeypatch.setattr("sys.argv", ["external-acceptance"])

    def invalid():
        raise ValueError("opaque-secret-in-validator-input")

    monkeypatch.setattr(acceptance, "Settings", invalid)
    assert acceptance.main() == 1
    output = capsys.readouterr().out
    assert "opaque-secret-in-validator-input" not in output
    assert len(json.loads(output)["results"]) == 5
    assert all(r["status"] == FAIL for r in json.loads(output)["results"])


@pytest.mark.parametrize(
    "statuses,exit_code",
    [([PASS] * 5, 0), ([PASS, PASS] + [NOT_CONFIGURED] * 3, 0), ([FAIL] + [PASS] * 4, 1)],
)
def test_cli_missing_optional_configuration_is_reported_without_failing_gate(
    monkeypatch, capsys, statuses, exit_code
) -> None:
    from app.external_integrations import acceptance

    monkeypatch.setattr("sys.argv", ["external-acceptance"])
    monkeypatch.setattr(acceptance, "Settings", readonly_settings)
    monkeypatch.setattr(
        acceptance,
        "run",
        lambda *args, **kwargs: {
            "results": [
                acceptance.result(name, status, "test evidence")
                for name, status in zip(acceptance.INTEGRATIONS, statuses, strict=True)
            ]
        },
    )
    assert acceptance.main() == exit_code
    assert [r["status"] for r in json.loads(capsys.readouterr().out)["results"]] == statuses


def test_disabled_webhook_is_not_configured(pso_client) -> None:
    client, _factory, settings = pso_client
    settings.tradingview_webhook_enabled = False
    client.headers.update(_login(client, "at038-a@test.example"))
    api = ApiAcceptance(client, ORG_A, settings.tradingview_webhook_secret)
    assert api.tradingview()["status"] == NOT_CONFIGURED
