"""internal_paper_enabled accepts only JSON booleans (Griller #356 review)."""

import pytest
from pydantic import ValidationError

from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate


@pytest.mark.parametrize("value", [True, False, None])
def test_accepts_real_booleans(value):
    update = AutonomousTradingSettingsUpdate(internal_paper_enabled=value)
    assert update.internal_paper_enabled is value


@pytest.mark.parametrize("value", ["yes", "true", "1", 1, 0, "on"])
def test_rejects_truthy_strings_and_ints(value):
    with pytest.raises(ValidationError):
        AutonomousTradingSettingsUpdate(internal_paper_enabled=value)


def test_rejects_yes_from_json():
    with pytest.raises(ValidationError):
        AutonomousTradingSettingsUpdate.model_validate_json('{"internal_paper_enabled": "yes"}')
