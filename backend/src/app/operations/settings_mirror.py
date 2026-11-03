"""Copy trading configuration from staging into production (operator tool).

Scope: strategies (selected version content), risk-policy profiles, autonomous
trading settings, and the Watcher watchlist for one organization and user.

Rules:
* The bundle has no database ids, credentials, tokens, or exchange keys. A
  bundle containing a secret-looking key is refused on export and import.
* Import is dry-run unless ``apply=True`` and is idempotent: rerunning the same
  bundle changes nothing.
* Risk policy is never applied directly. The importer files a governed
  proposal; a human applies it in the production app (step-up proof), exactly
  as on staging. Arming switches (autonomous paper switch, kill switch, env
  flags) are not part of the bundle.
* Target organization and user must already exist; nothing is created there.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.autonomous_trading_settings import AutonomousTradingSettingsRow
from app.db.models import Organization, User, UserStrategy, UserStrategyVersion
from app.db.risk_policy import RiskPolicyProposal
from app.db.strategy_immutability import strategy_version_content_hash
from app.repositories.watcher_watchlist import WatcherWatchlistRepository
from app.schemas.common import StrategyChangeSource, StrategyId, StrategyLifecycleState
from app.services.audit_service import AuditService
from app.services.autonomous_trading_settings_service import (
    validate_daily_loss_limit_percent,
    validate_max_trades_per_day,
    validate_risk_per_trade_percent,
)
from app.services.risk_policy.governance import PolicyActor, PolicyChannel
from app.services.risk_policy.persistence import RiskPolicyService
from app.services.risk_policy.profile import (
    PolicyKind,
    canonical_profile,
    profile_from_canonical,
    profile_hash,
)
from app.services.strategy_versioning import StrategyVersioningService

BUNDLE_FORMAT = "alphatrade.settings_mirror.v1"
MIRROR_ACTOR_ID = "ops:settings-mirror"
_SECRET_MARKERS = ("secret", "token", "password", "passphrase", "api_key", "apikey", "private")


class MirrorError(ValueError):
    """Refused bundle or target."""


@dataclass
class ImportReport:
    dry_run: bool
    actions: list[dict[str, str]] = field(default_factory=list)

    def add(self, area: str, name: str, action: str, detail: str = "") -> None:
        self.actions.append({"area": area, "name": name, "action": action, "detail": detail})

    @property
    def changes(self) -> int:
        return sum(1 for a in self.actions if a["action"] not in {"unchanged", "skipped"})

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "changes": self.changes}


def assert_no_secrets(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in _SECRET_MARKERS):
                raise MirrorError(f"bundle refused: secret-like key at {path}.{key}")
            assert_no_secrets(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_secrets(child, f"{path}[{index}]")


def _resolve(session: Session, org_name: str, user_email: str) -> tuple[uuid.UUID, uuid.UUID]:
    org = session.scalar(select(Organization).where(Organization.name == org_name))
    user = session.scalar(select(User).where(User.email == user_email.strip().lower()))
    if org is None or user is None:
        raise MirrorError("organization or user not found in this database")
    return org.id, user.id


def _strategy_hash(entry: dict[str, Any]) -> str:
    return strategy_version_content_hash(
        card=entry["card"],
        structured_rules=entry.get("structured_rules"),
        lesson_source_metadata=None,
        pattern_spec=entry.get("pattern_spec"),
    )


def _dec(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


# --------------------------------------------------------------------- export


def export_bundle(session: Session, *, org_name: str, user_email: str) -> dict[str, Any]:
    org_id, user_id = _resolve(session, org_name, user_email)
    versioning = StrategyVersioningService(session)
    strategies: list[dict[str, Any]] = []
    names: dict[uuid.UUID, str] = {}
    rows = session.scalars(
        select(UserStrategy)
        .where(UserStrategy.organization_id == org_id, UserStrategy.user_id == user_id)
        .order_by(UserStrategy.name)
    ).all()
    for strategy in rows:
        names[strategy.id] = strategy.name
        version = versioning.selected_version(strategy)
        if version is None:
            continue
        strategies.append(
            {
                "name": strategy.name,
                "setup_type": strategy.setup_type.value,
                "enabled": bool(strategy.enabled),
                "paper_eligible": bool(strategy.paper_eligible),
                "notes": strategy.notes,
                "card": version.card,
                "structured_rules": version.structured_rules,
                "pattern_spec": version.pattern_spec,
            }
        )

    risk_profiles: list[dict[str, str]] = []
    policy = RiskPolicyService(session, AuditService(session))
    for kind in PolicyKind:
        profile, active, _scheduled = policy.read(organization_id=org_id, kind=kind)
        if active is not None:
            risk_profiles.append(canonical_profile(profile))

    autonomous = None
    row = session.scalar(
        select(AutonomousTradingSettingsRow).where(
            AutonomousTradingSettingsRow.organization_id == org_id,
            AutonomousTradingSettingsRow.user_id == user_id,
        )
    )
    if row is not None:
        autonomous = {
            "daily_loss_limit_percent": _dec(row.daily_loss_limit_percent),
            "max_trades_per_day": row.max_trades_per_day,
            "risk_per_trade_percent": _dec(row.risk_per_trade_percent),
            "strategy_name": names.get(row.strategy_id) if row.strategy_id else None,
            "symbol": row.symbol,
        }

    watchlist = WatcherWatchlistRepository(session).load(org_id)
    bundle = {
        "format": BUNDLE_FORMAT,
        "strategies": strategies,
        "risk_policy_profiles": risk_profiles,
        "autonomous_settings": autonomous,
        "watchlist": [{"symbol": s.symbol, "enabled": bool(s.enabled)} for s in watchlist.slots],
    }
    assert_no_secrets(bundle)
    return bundle


# --------------------------------------------------------------------- import


def import_bundle(
    session: Session,
    bundle: dict[str, Any],
    *,
    org_name: str,
    user_email: str,
    apply: bool = False,
) -> ImportReport:
    """Plan (and with ``apply=True`` perform) the import. Caller owns the commit."""

    if bundle.get("format") != BUNDLE_FORMAT:
        raise MirrorError("unknown bundle format")
    assert_no_secrets(bundle)
    org_id, user_id = _resolve(session, org_name, user_email)
    report = ImportReport(dry_run=not apply)
    strategy_ids = _import_strategies(session, bundle, org_id, user_id, apply, report)
    _import_risk_policy(session, bundle, org_id, apply, report)
    _import_autonomous(session, bundle, org_id, user_id, strategy_ids, apply, report)
    _import_watchlist(session, bundle, org_id, apply, report)
    if apply:
        session.flush()
    return report


def _import_strategies(
    session: Session,
    bundle: dict[str, Any],
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    apply: bool,
    report: ImportReport,
) -> dict[str, uuid.UUID]:
    versioning = StrategyVersioningService(session)
    ids: dict[str, uuid.UUID] = {}
    for entry in bundle.get("strategies") or []:
        name = str(entry["name"])
        wanted = _strategy_hash(entry)
        strategy = session.scalar(
            select(UserStrategy).where(
                UserStrategy.organization_id == org_id,
                UserStrategy.user_id == user_id,
                UserStrategy.name == name,
            )
        )
        if strategy is None:
            report.add("strategy", name, "create", "version 1 from staging")
            if apply:
                strategy = UserStrategy(
                    id=uuid.uuid4(),
                    organization_id=org_id,
                    user_id=user_id,
                    name=name,
                    setup_type=StrategyId(entry["setup_type"]),
                    current_version=1,
                    enabled=bool(entry.get("enabled", True)),
                    notes=entry.get("notes"),
                    paper_eligible=bool(entry.get("paper_eligible", False)),
                )
                session.add(strategy)
                session.flush()
                version = UserStrategyVersion(
                    id=uuid.uuid4(),
                    strategy_id=strategy.id,
                    version=1,
                    card=entry["card"],
                    structured_rules=entry.get("structured_rules"),
                    pattern_spec=entry.get("pattern_spec"),
                    change_source=StrategyChangeSource.MIGRATION,
                    actor_user_id=user_id,
                    change_reason="settings mirror from staging",
                    content_hash=wanted,
                )
                session.add(version)
                session.flush()
                versioning.append_lifecycle(
                    organization_id=org_id,
                    strategy_id=strategy.id,
                    strategy_version_id=version.id,
                    new_state=(
                        StrategyLifecycleState.STRUCTURED
                        if entry.get("structured_rules")
                        else StrategyLifecycleState.DRAFT
                    ),
                    actor_user_id=user_id,
                    reason="settings mirror from staging",
                )
                ids[name] = strategy.id
            continue
        ids[name] = strategy.id
        current = versioning.selected_version(strategy)
        current_hash = (
            None
            if current is None
            else strategy_version_content_hash(
                card=current.card,
                structured_rules=current.structured_rules,
                lesson_source_metadata=None,
                pattern_spec=current.pattern_spec,
            )
        )
        if current_hash == wanted:
            report.add("strategy", name, "unchanged")
            continue
        if current is None:
            report.add("strategy", name, "skipped", "target has no selected version")
            continue
        report.add("strategy", name, "new_version", f"fork from v{current.version}")
        if apply:
            versioning.fork_semantic_update(
                strategy,
                parent=current,
                card=entry["card"],
                structured_rules=entry.get("structured_rules"),
                lesson_source_metadata=current.lesson_source_metadata,
                actor_user_id=user_id,
                source=StrategyChangeSource.MIGRATION,
                reason="settings mirror from staging",
                pattern_spec=entry.get("pattern_spec"),
            )
    return ids


def _import_risk_policy(
    session: Session,
    bundle: dict[str, Any],
    org_id: uuid.UUID,
    apply: bool,
    report: ImportReport,
) -> None:
    policy = RiskPolicyService(session, AuditService(session))
    for payload in bundle.get("risk_policy_profiles") or []:
        profile = profile_from_canonical(payload)
        wanted = profile_hash(profile)
        current, active, _ = policy.read(organization_id=org_id, kind=profile.kind)
        if active is not None and profile_hash(current) == wanted:
            report.add("risk_policy", profile.kind.value, "unchanged")
            continue
        pending = session.scalar(
            select(RiskPolicyProposal.id).where(
                RiskPolicyProposal.organization_id == org_id,
                RiskPolicyProposal.kind == profile.kind.value,
                RiskPolicyProposal.content_hash == wanted,
            )
        )
        if pending is not None:
            report.add("risk_policy", profile.kind.value, "unchanged", "proposal already filed")
            continue
        report.add(
            "risk_policy", profile.kind.value, "propose", "human must apply in production app"
        )
        if apply:
            policy.propose(
                organization_id=org_id,
                actor=PolicyActor(
                    actor_id=MIRROR_ACTOR_ID, channel=PolicyChannel.BOT, authenticated=True
                ),
                profile=profile,
                reason="Mirror of the staging risk profile (settings mirror import).",
            )


def _import_autonomous(
    session: Session,
    bundle: dict[str, Any],
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    strategy_ids: dict[str, uuid.UUID],
    apply: bool,
    report: ImportReport,
) -> None:
    data = bundle.get("autonomous_settings")
    if not data:
        return
    risk_raw = data.get("risk_per_trade_percent")
    values = {
        "daily_loss_limit_percent": validate_daily_loss_limit_percent(
            Decimal(str(data["daily_loss_limit_percent"]))
        ),
        "max_trades_per_day": validate_max_trades_per_day(int(data["max_trades_per_day"])),
        "risk_per_trade_percent": validate_risk_per_trade_percent(
            None if risk_raw is None else Decimal(str(risk_raw))
        ),
        "strategy_id": None,
        "symbol": data.get("symbol"),
    }
    strategy_name = data.get("strategy_name")
    if strategy_name:
        strategy_id = strategy_ids.get(strategy_name)
        if strategy_id is None and apply:
            report.add("autonomous_settings", "strategy", "skipped", "strategy missing")
            return
        values["strategy_id"] = strategy_id
    row = session.scalar(
        select(AutonomousTradingSettingsRow).where(
            AutonomousTradingSettingsRow.organization_id == org_id,
            AutonomousTradingSettingsRow.user_id == user_id,
        )
    )
    pending_strategy = bool(strategy_name) and values["strategy_id"] is None
    if (
        row is not None
        and not pending_strategy
        and all(getattr(row, key) == value for key, value in values.items())
    ):
        report.add("autonomous_settings", "user", "unchanged")
        return
    report.add("autonomous_settings", "user", "create" if row is None else "update")
    if not apply:
        return
    if row is None:
        session.add(AutonomousTradingSettingsRow(organization_id=org_id, user_id=user_id, **values))
    else:
        for key, value in values.items():
            setattr(row, key, value)


def _import_watchlist(
    session: Session,
    bundle: dict[str, Any],
    org_id: uuid.UUID,
    apply: bool,
    report: ImportReport,
) -> None:
    slots = [(str(s["symbol"]), bool(s["enabled"])) for s in bundle.get("watchlist") or []]
    if not slots:
        return
    repo = WatcherWatchlistRepository(session)
    current = repo.load(org_id)
    if [(s.symbol, bool(s.enabled)) for s in current.slots] == slots:
        report.add("watchlist", "organization", "unchanged")
        return
    report.add("watchlist", "organization", "replace", f"revision {current.revision} -> next")
    if apply:
        repo.replace(org_id, slots, expected_revision=current.revision)
