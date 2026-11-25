"""#494 follow-up: venue-absence release is for governed demo plans only.

``release_venue_absent_send`` is an operator recovery for governed worker sends.
A manual demo plan, or a missing plan, is refused before any lock or state change.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.core.errors import TradingPolicyError
from app.services.venue_submit_dispatcher import VENUE_ABSENCE_MARGIN, VenueSubmitDispatcher

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _dispatcher(plan: object | None) -> tuple[VenueSubmitDispatcher, MagicMock]:
    session = MagicMock()
    session.get.return_value = plan
    epochs = MagicMock()
    epochs._settings = Settings(_env_file=None)
    dispatcher = VenueSubmitDispatcher(session, epochs, clock=lambda: NOW)
    command = SimpleNamespace(revision_id=uuid4(), organization_id=uuid4(), account_id=uuid4())
    dispatcher._commands = MagicMock(get=lambda _command_id: command)  # type: ignore[assignment]
    return dispatcher, epochs


@pytest.mark.parametrize(
    "plan",
    [
        SimpleNamespace(execution_policy_version="manual-blofin-demo/v1"),
        SimpleNamespace(execution_policy_version="paper-internal/v1"),
        None,
    ],
)
def test_only_a_governed_demo_plan_can_be_released(plan) -> None:
    if plan is not None:
        plan.valid_until = NOW - VENUE_ABSENCE_MARGIN - timedelta(hours=1)
    dispatcher, epochs = _dispatcher(plan)
    with pytest.raises(TradingPolicyError, match="Only a governed demo plan"):
        dispatcher.release_venue_absent_send(command_id=uuid4(), venue_checked_at=NOW)
    epochs.lock_epoch.assert_not_called()


def test_a_governed_plan_passes_the_policy_check() -> None:
    plan = SimpleNamespace(
        execution_policy_version="governed-blofin-demo/v1",
        valid_until=NOW - VENUE_ABSENCE_MARGIN - timedelta(hours=1),
    )
    dispatcher, epochs = _dispatcher(plan)
    # Past the policy and timing checks it locks the epoch before reading the effect.
    epochs.lock_epoch.side_effect = RuntimeError("reached the epoch lock")
    with pytest.raises(RuntimeError, match="reached the epoch lock"):
        dispatcher.release_venue_absent_send(command_id=uuid4(), venue_checked_at=NOW)
