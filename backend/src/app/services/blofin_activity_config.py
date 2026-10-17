"""Separate disarmed activity settings; dedicated read-only credentials only."""

from dataclasses import dataclass
from uuid import UUID

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.blofin_identity import connection_binding
from app.core.config import Settings
from app.core.errors import ExchangeDemoInactiveError
from app.providers.exchange.blofin_activity import identifier


class BloFinActivitySettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BLOFIN_ACTIVITY_", extra="ignore")

    enabled: bool = False
    organization_id: UUID | None = None
    expected_uid: str = ""
    lookback_days: int = Field(default=7, ge=1, le=30)
    overlap_seconds: int = Field(default=300, ge=60, le=3600)
    max_pages: int = Field(default=4, ge=1, le=10)
    page_size: int = Field(default=100, ge=1, le=100)
    budget_seconds: int = Field(default=30, ge=1, le=60)
    stale_seconds: int = Field(default=300, ge=30, le=3600)
    poll_seconds: int = Field(default=60, ge=30, le=3600)


@dataclass(frozen=True)
class ActivityScope:
    organization_id: UUID
    account_uid: str
    environment: str = "demo"

    def key(self) -> tuple[UUID, str, str]:
        return self.organization_id, self.environment, self.account_uid


def configured_scope(config: BloFinActivitySettings) -> ActivityScope:
    if config.organization_id is None or not config.expected_uid:
        raise ExchangeDemoInactiveError(
            "BloFin activity organization and native UID pins required."
        )
    return ActivityScope(config.organization_id, identifier(config.expected_uid))


def credential_binding(settings: Settings) -> str:
    """Opaque binding selector only. UID remains the trading account identity."""
    return connection_binding(settings, readonly=True)


def get_activity_settings() -> BloFinActivitySettings:
    return BloFinActivitySettings()
