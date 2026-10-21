"""New feature flags from the PR 249 stack stay off unless an operator sets them."""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.blofin_activity_config import BloFinActivitySettings

# Flags added on the 249 stack. Each one gates a new capability and must load disabled.
PR249_SETTINGS_FLAGS: dict[str, str] = {
    "knowledge_indexing_enabled": "KNOWLEDGE_INDEXING_ENABLED",
    "agent_realtime_enabled": "AGENT_REALTIME_ENABLED",
    "trendpulse_screening_enabled": "TRENDPULSE_SCREENING_ENABLED",
}
ACTIVITY_ENABLED_ENV = "BLOFIN_ACTIVITY_ENABLED"


@pytest.mark.parametrize("field_name", sorted(PR249_SETTINGS_FLAGS))
def test_pr249_settings_flags_default_off(field_name: str) -> None:
    assert Settings.model_fields[field_name].default is False


def test_pr249_settings_flags_load_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    for env_name in PR249_SETTINGS_FLAGS.values():
        monkeypatch.delenv(env_name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.knowledge_indexing_enabled is False
    assert settings.agent_realtime_enabled is False
    assert settings.trendpulse_screening_enabled is False


def test_blofin_activity_flag_defaults_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert BloFinActivitySettings.model_fields["enabled"].default is False
    monkeypatch.delenv(ACTIVITY_ENABLED_ENV, raising=False)
    assert BloFinActivitySettings(_env_file=None).enabled is False
