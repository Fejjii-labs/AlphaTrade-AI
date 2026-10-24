"""Tests for the health and readiness endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["execution_mode"] == "paper"
    assert body["real_trading_enabled"] is False
    assert body["exchange_mode"] == "paper_internal"
    assert body["market_watcher_enabled"] is False
    assert body["watcher_orchestration_enabled"] is False
    assert body["watcher_paper_staging_activation"] is False
    assert body["telegram_alerts_enabled"] is False
    assert body["telegram_interaction_enabled"] is False
    assert body["telegram_paper_activation_armed"] is False
    assert body["telegram_inbound_mode"] == "off"
    assert body["telegram_network_permitted"] is False
    assert body["perpetual_evidence_source"] == "replay"
    assert body["perpetual_evidence_activation"] == "inactive"
    assert body["perpetual_evidence_intended_staging_source"] == "binance_usdm"
    assert body["perpetual_evidence_rollback_source"] == "replay"
    assert body["live_market_read_only"] is True
    assert body["exchange_credentials_used_for_market_evidence"] is False
    assert body["spot_fallback_permitted"] is False
    assert body["fabricated_fallback_permitted"] is False
    assert body["live_quote_freshness_seconds"] == 10
    assert body["first_perpetual_symbol"] == "BTCUSDT"
    assert body["version"]
    activation = client.get("/health/telegram-paper-activation")
    assert activation.status_code == 200
    posture = activation.json()
    assert posture["verdict"] == "NOT_ARMED"
    assert posture["runtime_armable"] is False
    assert posture["real_trading_enabled"] is False
    assert posture["webhook_mounted"] is False
    assert posture["forbidden"]["mint_candidate"] is False
    assert posture["forbidden"]["create_live_order"] is False
    assert "telegram_webhook_secret" not in posture
    assert "telegram_bot_token" not in posture


def test_health_sets_request_id_header(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers.get("X-Request-ID")


def test_health_propagates_inbound_request_id(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "test-correlation-id"})
    assert response.headers["X-Request-ID"] == "test-correlation-id"


def test_health_includes_git_sha_when_configured(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("GIT_SHA", "d52588eabc1234567890abcdef1234567890abcd")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["git_sha"] == "d52588eabc1234567890abcdef1234567890abcd"


def test_health_git_sha_null_when_unconfigured(client: TestClient, monkeypatch) -> None:
    for key in ("GIT_SHA", "RENDER_GIT_COMMIT", "SOURCE_VERSION"):
        monkeypatch.delenv(key, raising=False)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["git_sha"] is None


def test_health_git_sha_from_render_env(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("GIT_SHA", raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "089739b")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["git_sha"] == "089739b"


def test_readiness_ready_with_mock_providers(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200

    body = response.json()
    assert body["ready"] is True
    assert body["providers_unavailable"] == 0
    assert body["providers_total"] > 0
    assert body["degraded_providers"] == []
