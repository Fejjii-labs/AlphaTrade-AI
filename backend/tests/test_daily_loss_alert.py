"""Once-a-day Telegram page when the daily loss budget blocks a new entry."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
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
ACCOUNT = uuid.uuid4()
NOW = datetime(2026, 11, 23, 14, 0, tzinfo=UTC)


def _settings(**updates: object) -> Settings:
    values: dict[str, object] = {
        "telegram_bot_token": "123456:simulated-token",
        "telegram_chat_id": "424242",
        "governed_blofin_demo_organization_id": str(ORG),
        "governed_blofin_demo_account_id": str(ACCOUNT),
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


class _Session:
    def __init__(self, row: object | None, *, fail: bool = False) -> None:
        self.row, self.fail = row, fail

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def scalar(self, _query: object) -> object | None:
        if self.fail:
            raise RuntimeError("database unavailable")
        return self.row

    def expunge(self, _row: object) -> None:
        return None

    def rollback(self) -> None:
        return None


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reset_safety_alert_dedup()
    messages: list[str] = []

    def _deliver(
        self: TelegramAlertDeliveryProvider, payload: Any, *, bypass_enable_check: bool = False
    ) -> AlertDeliveryResult:
        assert threading.current_thread().name.startswith("telegram-alert")
        messages.append(payload.message)
        return AlertDeliveryResult(success=True, channel=AlertDeliveryChannel.TELEGRAM)

    monkeypatch.setattr(TelegramAlertDeliveryProvider, "deliver", _deliver)
    return messages


def _wait(messages: list[str], count: int) -> None:
    deadline = time.monotonic() + 5
    while len(messages) < count and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)  # let any unexpected extra send land


def test_text_states_budget_realized_and_reserved_loss() -> None:
    text = format_daily_loss_limit_text(
        day="2026-11-23",
        max_daily_loss=Decimal("300"),
        actual_daily_loss=Decimal("250.5"),
        reserved_daily_loss=Decimal("60"),
    )
    assert text.splitlines() == [
        "AlphaTrade DEMO: daily loss budget reached.",
        "New entries are blocked for the rest of 2026-11-23 (UTC).",
        "Budget: 300.00 USDT",
        "Realized loss today: 250.50 USDT",
        "Reserved by open entries: 60.00 USDT",
        "Open positions keep their stops and targets.",
    ]


def test_default_on_and_needs_the_existing_bot_token() -> None:
    assert DAILY_LOSS_LIMIT_REACHED in TELEGRAM_SAFETY_DEFAULT_CODES
    assert safety_alert_telegram_allowed(Settings(_env_file=None), DAILY_LOSS_LIMIT_REACHED)
    reset_safety_alert_dedup()
    assert not page_daily_loss_limit_reached(
        None, _settings(telegram_bot_token=""), organization_id=ORG, now=NOW
    )


def test_one_page_per_organization_per_utc_day(sent: list[str]) -> None:
    row = SimpleNamespace(
        max_daily_loss=Decimal("300"),
        actual_daily_loss=Decimal("310"),
        reserved_daily_loss=Decimal("0"),
        exposure_unit="USDT",
    )
    settings = _settings()
    factory = lambda: _Session(row)  # noqa: E731
    # A burst of blocked entries queues one send.
    results = [
        page_daily_loss_limit_reached(factory, settings, organization_id=ORG, now=NOW)
        for _ in range(5)
    ]
    assert results[0] and not any(results[1:])
    _wait(sent, 1)
    assert len(sent) == 1
    assert "Realized loss today: 310.00 USDT" in sent[0]
    # Same day, later: still one page.
    later = NOW + timedelta(hours=6)
    assert not page_daily_loss_limit_reached(factory, settings, organization_id=ORG, now=later)
    # Next UTC day pages again.
    tomorrow = NOW + timedelta(days=1)
    assert page_daily_loss_limit_reached(factory, settings, organization_id=ORG, now=tomorrow)
    _wait(sent, 2)
    assert len(sent) == 2 and "2026-11-24" in sent[1]


def test_unreadable_budget_still_pages_without_numbers(sent: list[str]) -> None:
    assert page_daily_loss_limit_reached(
        lambda: _Session(None, fail=True), _settings(), organization_id=ORG, now=NOW
    )
    _wait(sent, 1)
    assert "Budget: unknown" in sent[0]


def test_other_organization_pages_without_demo_account_numbers(sent: list[str]) -> None:
    other = uuid.uuid4()
    row = SimpleNamespace(
        max_daily_loss=Decimal("1"),
        actual_daily_loss=Decimal("1"),
        reserved_daily_loss=Decimal("1"),
        exposure_unit="USDT",
    )
    assert page_daily_loss_limit_reached(
        lambda: _Session(row), _settings(), organization_id=other, now=NOW
    )
    _wait(sent, 1)
    assert "Budget: unknown" in sent[0]


def test_watcher_pages_when_the_daily_loss_budget_blocks_an_entry(
    monkeypatch: pytest.MonkeyPatch,
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
        lambda self, session, *, candidate, **_: _proof(candidate, "blocked", "daily_loss_limit"),
    )
    pages: list[dict[str, object]] = []
    monkeypatch.setattr(
        "app.services.daily_loss_alert.page_daily_loss_limit_reached",
        lambda session_factory, _settings, **page: (
            pages.append({**page, "factory": session_factory is not None}) or True
        ),
    )
    source = _live_source()
    monitor = _monitor(source, replay=False, clock=lambda: EVALUATED_AT)
    runtime = _runtime_for(factory, source, monitor, replay=False, held=[])
    runtime._settings = settings
    cycle = runtime.run_cycle()
    assert cycle.scans and cycle.scans[0].paper_loop_reason == "daily_loss_limit"
    assert [(page["organization_id"], page["factory"]) for page in pages] == [(WATCH_ORG, True)]
