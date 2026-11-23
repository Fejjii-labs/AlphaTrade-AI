"""Once-a-day Telegram page when the daily loss budget refuses a new entry."""

from __future__ import annotations

import re
import threading
import uuid
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.core.config import Settings
from app.providers.alert_delivery.base import AlertDeliveryResult
from app.providers.alert_delivery.telegram import TelegramAlertDeliveryProvider
from app.schemas.common import AlertDeliveryChannel
from app.services.daily_loss_alert import (
    DAILY_LOSS_LIMIT_REACHED,
    format_daily_loss_limit_text,
    page_daily_loss_limit_reached,
)
from app.services.safety_alerts import (
    TELEGRAM_SAFETY_DEFAULT_CODES,
    reset_safety_alert_dedup,
    safety_alert_telegram_allowed,
)

ORG = uuid.UUID("1af2eca6-71f3-4a1f-812d-bcd4f5170c84")
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123456:simulated-token",
        "telegram_chat_id": "424242",
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


class _Telegram:
    """Fake send that can be held open to prove a burst queues one page."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.release = threading.Event()
        self.release.set()
        self.sent = threading.Semaphore(0)

    def deliver(
        self, _provider: Any, payload: Any, *, bypass_enable_check: bool = False
    ) -> AlertDeliveryResult:
        assert threading.current_thread().name.startswith("telegram-alert")
        assert self.release.wait(timeout=5)
        self.messages.append(payload.message)
        self.sent.release()
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    def wait(self) -> None:
        assert self.sent.acquire(timeout=5)


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> _Telegram:
    reset_safety_alert_dedup()
    fake = _Telegram()
    monkeypatch.setattr(
        TelegramAlertDeliveryProvider,
        "deliver",
        lambda self, payload, **kw: fake.deliver(self, payload, **kw),
    )
    return fake


def test_text_states_the_refusal_without_amounts() -> None:
    text = format_daily_loss_limit_text(day="2026-11-23")
    assert text.splitlines()[0] == "AlphaTrade DEMO: the daily loss budget refused a new entry."
    assert "2026-11-23 (UTC)" in text
    # Loss-protection pages never carry a P&L amount.
    assert re.findall(r"\d+(?:\.\d+)?", text.replace("2026-11-23", "")) == []


def test_default_on_and_needs_the_existing_bot_token() -> None:
    assert DAILY_LOSS_LIMIT_REACHED in TELEGRAM_SAFETY_DEFAULT_CODES
    assert safety_alert_telegram_allowed(Settings(_env_file=None), DAILY_LOSS_LIMIT_REACHED)
    reset_safety_alert_dedup()
    unconfigured = _settings(telegram_bot_token="")
    assert not page_daily_loss_limit_reached(unconfigured, organization_id=ORG, now=NOW)


def test_one_page_per_organization_per_utc_day(telegram: _Telegram) -> None:
    settings = _settings()
    telegram.release.clear()  # hold the first send open during the burst
    results = [
        page_daily_loss_limit_reached(settings, organization_id=ORG, now=NOW) for _ in range(5)
    ]
    assert results == [True, False, False, False, False]
    telegram.release.set()
    telegram.wait()
    # Same UTC day, after the send: the 24 h dedup holds.
    later = NOW + timedelta(hours=9)
    assert not page_daily_loss_limit_reached(settings, organization_id=ORG, now=later)
    # A non-UTC clock on the same UTC day keys the same day.
    local = NOW.astimezone(timezone(timedelta(hours=-5)))
    assert not page_daily_loss_limit_reached(settings, organization_id=ORG, now=local)
    # Next UTC day pages again; another organization pages on its own.
    tomorrow = NOW + timedelta(days=1)
    assert page_daily_loss_limit_reached(settings, organization_id=ORG, now=tomorrow)
    telegram.wait()
    assert page_daily_loss_limit_reached(settings, organization_id=uuid.uuid4(), now=NOW)
    telegram.wait()
    assert len(telegram.messages) == 3
    assert "2026-11-24 (UTC)" in telegram.messages[1]


@pytest.mark.parametrize("reason", ["daily_loss_limit", "insufficient_daily_loss_allocation"])
def test_watcher_pages_when_the_daily_loss_budget_blocks_an_entry(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    from sqlalchemy import select

    from app.db.models import ExecutionAccount
    from app.services.automated_paper_loop import _proof
    from app.services.governed_blofin_demo import GovernedBloFinDemoLoop
    from tests.support.phase5_market import EVALUATED_AT
    from tests.support.postgres_persistence import phase7_plan_session_factory
    from tests.test_automated_paper_loop import _live_source, _monitor, _runtime_for, _seed
    from tests.test_governed_blofin_demo import demo_settings
    from tests.test_watcher_paper_runtime import ORG as WATCH_ORG

    factory = phase7_plan_session_factory()
    with factory() as session:
        _seed(session)
        account = session.scalar(select(ExecutionAccount))
        assert account is not None
        settings = demo_settings(str(account.id))
        session.commit()
    monkeypatch.setattr(
        GovernedBloFinDemoLoop,
        "continue_confirmed_setup",
        lambda self, session, *, candidate, **_: _proof(candidate, "blocked", reason),
    )
    pages: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.services.daily_loss_alert.page_daily_loss_limit_reached",
        lambda _settings, **page: pages.append(page) or True,
    )
    source = _live_source()
    monitor = _monitor(source, replay=False, clock=lambda: EVALUATED_AT)
    runtime = _runtime_for(factory, source, monitor, replay=False, held=[])
    runtime._settings = settings
    cycle = runtime.run_cycle()
    assert cycle.scans and cycle.scans[0].paper_loop_reason == reason
    assert [page["organization_id"] for page in pages] == [WATCH_ORG]
