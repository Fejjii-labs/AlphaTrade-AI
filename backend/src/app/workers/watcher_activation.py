"""Controlled staging activation for paper Watcher monitoring.

The arm defaults off. This module does not deploy, does not edit environment
files, and does not enable real trading. The dedicated worker and the
supervised paper worker call :func:`run_staging_activation` and scan only after
preflight clears. When the controlled paper package is armed, that path
installs the Telegram projection hook. The hook enqueues. It does not deliver.
The API process never autostarts this path.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Environment, ExchangeMode, ExecutionMode, Settings
from app.market_activation.profile import REPLAY_MODES
from app.market_contracts.adapters.factory import perpetual_source_is_replay
from app.workers.watcher_paper import WatcherPaperRuntime

logger = structlog.get_logger("workers.watcher_activation")

ActivationPhase = Literal["preflight", "runtime"]

REASON_PRIORITY: tuple[str, ...] = (
    "real_trading_enabled",
    "paper_execution_required",
    "production_forbidden",
    "staging_only",
    "telegram_forbidden",
    "telegram_projection_refused",
    "legacy_watcher_forbidden",
    "activation_disarmed",
    "activation_probe_failed",
    "replay_evidence_while_live_required",
    "provider_unavailable",
    "freshness_fail_closed",
    "strategy_lineage_invalid",
    "migration_unhealthy",
    "postgres_lease_required",
    "worker_identity_not_unique",
    "fencing_required",
    "restart_recovery_required",
    "idempotency_required",
    "approved_compiled_required",
    "canonical_live_evidence_required",
    "candidate_authority_not_confirmed_setup",
    "risk_block_not_final",
    "kill_switch_not_preserved",
    "runtime_heartbeat_stale",
)

_PAPER_EXCHANGES = frozenset(
    {ExchangeMode.PAPER_INTERNAL.value, ExchangeMode.PAPER_EXCHANGE_DEMO.value}
)
_PROVIDER_DOWN = frozenset(
    {
        "provider_unavailable",
        "provider_error",
        "spot_rejected",
        "symbol_mismatch",
        "unrecoverable_gap",
        "out_of_order",
        "duplicate_conflict",
    }
)

ROLLBACK_COMMANDS: tuple[str, ...] = (
    "Send SIGTERM to the dedicated paper Watcher process and confirm it has exited.",
    "Keep WATCHER_PAPER_STAGING_ACTIVATION unset or false.",
    "Keep WATCHER_ORCHESTRATION_ENABLED false.",
    "Keep TELEGRAM_ALERTS_ENABLED, TELEGRAM_INTERACTION_ENABLED, and "
    "AUTOMATIC_TELEGRAM_DELIVERY_ENABLED false.",
    "Keep ENABLE_REAL_TRADING false and EXECUTION_MODE=paper.",
    "Leave the kill switch unchanged. Do not clear it as part of rollback.",
    "Do not deploy, place orders, or edit staging environment files.",
    "Re-run preflight before any later start attempt.",
)


@dataclass(frozen=True, slots=True)
class WatcherPaperActivationConfig:
    """Static arm. Default construction is disarmed and cannot start a scan."""

    environment: str
    armed: bool
    orchestration_enabled: bool
    execution_mode: str
    enable_real_trading: bool
    real_trading_enabled: bool
    telegram_alerts_enabled: bool
    telegram_interaction_enabled: bool
    automatic_telegram_delivery_enabled: bool
    market_watcher_enabled: bool
    market_watcher_bridge_enabled: bool
    market_watcher_bridge_auto_tick: bool
    evidence_source: str
    exchange_mode: str
    configured_worker_id: str
    telegram_paper_activation_armed: bool = False
    telegram_inbound_mode: str = "off"
    telegram_network_permitted: bool = False
    production_demo: bool = False


@dataclass(frozen=True, slots=True)
class ActivationObservations:
    """Probes and architecture pins. Missing or false pins fail closed."""

    migration_revision: str | None
    expected_migration_revision: str | None
    provider_state: str
    lineage_valid: bool
    lease_backend: str
    worker_instance_id: str
    fencing_enabled: bool
    restart_recovery_enabled: bool
    idempotency_enabled: bool
    approved_compiled_only: bool
    canonical_live_evidence: bool
    freshness_fail_closed: bool
    confirmed_setup_only: bool
    risk_block_final: bool
    paper_execution_only: bool
    kill_switch_preserved: bool
    probe_failed: bool = False
    runtime_heartbeat_age_seconds: float | None = None
    runtime_heartbeat_limit_seconds: int = 90


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    primary_reason: str
    phase: ActivationPhase


@dataclass(frozen=True, slots=True)
class WatcherRollbackPlan:
    """Operator plan. This process never applies the plan."""

    triggered: bool
    reason_codes: tuple[str, ...]
    commands: tuple[str, ...]
    modifies_environment: bool = False
    deploys: bool = False
    activates_telegram: bool = False
    enables_real_trading: bool = False


@dataclass(frozen=True, slots=True)
class ActivationSmokeReport:
    passed: bool
    failures: tuple[str, ...]


def evidence_source_is_replay(source: str) -> bool:
    return source.strip().lower() in REPLAY_MODES


def worker_identity_is_unique(configured_id: str, instance_id: str) -> bool:
    """True when the process id is the configured prefix plus a 16-hex suffix."""

    prefix = configured_id.strip()[:80]
    if not prefix or instance_id == prefix or instance_id == configured_id.strip():
        return False
    marker = f"{prefix}:"
    if not instance_id.startswith(marker):
        return False
    suffix = instance_id[len(marker) :]
    return len(suffix) == 16 and all(character in "0123456789abcdef" for character in suffix)


def activation_config_from_settings(settings: Settings) -> WatcherPaperActivationConfig:
    return WatcherPaperActivationConfig(
        environment=settings.environment.value,
        armed=bool(settings.watcher_paper_staging_activation),
        orchestration_enabled=bool(settings.watcher_orchestration_enabled),
        execution_mode=settings.execution_mode.value,
        enable_real_trading=bool(settings.enable_real_trading),
        real_trading_enabled=bool(settings.real_trading_enabled),
        telegram_alerts_enabled=bool(settings.telegram_alerts_enabled),
        telegram_interaction_enabled=bool(settings.telegram_interaction_enabled),
        automatic_telegram_delivery_enabled=bool(settings.automatic_telegram_delivery_enabled),
        market_watcher_enabled=bool(settings.market_watcher_enabled),
        market_watcher_bridge_enabled=bool(settings.market_watcher_bridge_enabled),
        market_watcher_bridge_auto_tick=bool(settings.market_watcher_bridge_auto_tick),
        evidence_source=settings.perpetual_evidence_source.strip().lower(),
        exchange_mode=settings.exchange_mode.value,
        configured_worker_id=settings.watcher_paper_worker_id,
        telegram_paper_activation_armed=bool(settings.telegram_paper_activation_armed),
        telegram_inbound_mode=settings.telegram_inbound_mode.value,
        telegram_network_permitted=bool(settings.telegram_network_permitted),
        production_demo=bool(settings.production_demo_active),
    )


def staging_static_arm_ok(settings: Settings) -> bool:
    """Settings-level pins only. Preflight still has to pass before a scan."""

    if not settings.paper_runtime_environment:
        return False
    config = activation_config_from_settings(settings)
    if not _paper_runtime_config(config) or not config.armed or not config.orchestration_enabled:
        return False
    if config.enable_real_trading or config.real_trading_enabled:
        return False
    if config.execution_mode != ExecutionMode.PAPER.value:
        return False
    if config.exchange_mode not in _PAPER_EXCHANGES:
        return False
    if _telegram_enabled(config) or _legacy_watcher_enabled(config):
        return False
    return not evidence_source_is_replay(config.evidence_source)


def provider_state_for(*, availability: str, reason: str, is_live: bool) -> str:
    """Classify a monitor snapshot for the activation gate."""

    availability_token = availability.strip().lower()
    reason_token = reason.strip().lower()
    if reason_token in _PROVIDER_DOWN or availability_token == "unavailable":
        return "unavailable"
    if (
        availability_token == "replay"
        or reason_token in {"replay_fixture", "wrong_source"}
        or not is_live
    ):
        return "replay"
    if availability_token == "stale" or reason_token == "stale_stream":
        return "stale"
    if availability_token == "degraded":
        return "degraded"
    if availability_token == "fresh" and is_live:
        return "available"
    return "unavailable"


def evaluate_activation(
    config: WatcherPaperActivationConfig,
    observations: ActivationObservations,
    *,
    phase: ActivationPhase = "preflight",
) -> ActivationDecision:
    """Fail closed. ``allowed`` is true only when every pin holds."""

    found: set[str] = set()
    found.update(_environment_reasons(config))
    found.update(_safety_reasons(config, observations))
    found.update(_evidence_reasons(config, observations))
    found.update(_authority_reasons(observations))
    found.update(_lease_reasons(config, observations))
    if phase == "runtime":
        found.update(_runtime_reasons(observations))
    ordered = _ordered(found)
    return ActivationDecision(
        allowed=not ordered,
        reason_codes=ordered,
        primary_reason="cleared" if not ordered else ordered[0],
        phase=phase,
    )


def start_if_allowed(
    config: WatcherPaperActivationConfig,
    observations: ActivationObservations,
    start: Callable[[], None],
    *,
    phase: ActivationPhase = "preflight",
) -> ActivationDecision:
    """Call ``start`` only when preflight or the runtime gate allows it."""

    decision = evaluate_activation(config, observations, phase=phase)
    if decision.allowed:
        start()
    return decision


def rollback_plan_for(decision: ActivationDecision) -> WatcherRollbackPlan:
    if decision.allowed:
        return WatcherRollbackPlan(triggered=False, reason_codes=(), commands=())
    return WatcherRollbackPlan(
        triggered=True,
        reason_codes=decision.reason_codes,
        commands=ROLLBACK_COMMANDS,
    )


def execute_rollback_process(
    *,
    decision: ActivationDecision,
    stop: Callable[[], None] | None = None,
) -> WatcherRollbackPlan:
    """Stop a local runtime when asked. Does not change env, deploy, or trade."""

    plan = rollback_plan_for(decision)
    if plan.triggered and stop is not None:
        stop()
    return plan


def runtime_health_decision(
    config: WatcherPaperActivationConfig,
    observations: ActivationObservations,
) -> ActivationDecision:
    """Same pins as preflight, plus heartbeat age when the caller supplies it."""

    return evaluate_activation(config, observations, phase="runtime")


def evaluate_post_activation_smoke(snapshot: Mapping[str, object]) -> ActivationSmokeReport:
    """Read a status snapshot. Does not start the worker."""

    failures: list[str] = []

    def need(key: str, expected: object, code: str) -> None:
        if snapshot.get(key) is not expected:
            failures.append(code)

    need("paper_only", True, "paper_only")
    need("real_trading_enabled", False, "real_trading_enabled")
    need("telegram_enabled", False, "telegram_enabled")
    need("evidence_source", "live", "evidence_source")
    need("migration_healthy", True, "migration_unhealthy")
    need("provider_available", True, "provider_unavailable")
    need("lineage_valid", True, "strategy_lineage_invalid")
    need("lease_backend", "postgres", "postgres_leases")
    need("fencing_enabled", True, "fencing")
    need("restart_recovery_enabled", True, "restart_recovery")
    need("idempotency_enabled", True, "idempotency")
    need("confirmed_setup_only", True, "confirmed_setup")
    need("risk_block_final", True, "risk_block")
    need("kill_switch_preserved", True, "kill_switch")
    need("freshness_fail_closed", True, "freshness")
    need("running", True, "not_running")
    need("activation_armed", True, "not_armed")
    worker = snapshot.get("worker_id")
    configured = snapshot.get("configured_worker_id")
    if (
        not isinstance(worker, str)
        or not isinstance(configured, str)
        or not worker_identity_is_unique(configured, worker)
    ):
        failures.append("worker_identity")
    return ActivationSmokeReport(passed=not failures, failures=tuple(failures))


def expected_migration_head() -> str | None:
    """Single Alembic head. Multiple heads or a missing script tree fail closed."""

    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = Path(__file__).resolve().parents[3] / "alembic.ini"
    if not ini.is_file():
        return None
    try:
        config = Config(str(ini))
        config.set_main_option("script_location", str(ini.parent / "src/app/db/migrations"))
        heads = ScriptDirectory.from_config(config).get_heads()
    except Exception:
        return None
    if len(heads) != 1:
        return None
    return heads[0]


def read_migration_revision(session: Session) -> str | None:
    """Return the lone ``alembic_version`` row, or None when the chain is unhealthy."""

    try:
        rows = list(
            session.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
        )
    except Exception:
        session.rollback()
        return None
    if len(rows) != 1:
        return None
    value = str(rows[0]).strip()
    return value or None


def collect_live_observations(
    settings: Settings,
    *,
    session_factory: sessionmaker[Session],
    worker_instance_id: str,
    monitor: object | None = None,
    freshness_out: dict[str, float | None] | None = None,
) -> ActivationObservations:
    """Read migration, lineage, and the live monitor. Probe errors fail closed."""

    expected = expected_migration_head()
    revision: str | None = None
    lineage_valid = False
    probe_failed = False
    session = session_factory()
    try:
        revision = read_migration_revision(session)
        lineage_valid = _probe_lineage(session, settings)
    except Exception:
        probe_failed = True
        logger.warning(
            "watcher_activation_database_probe_failed",
            worker_instance_id=worker_instance_id,
        )
    finally:
        session.close()

    provider_state = "unavailable"
    try:
        provider_state = _probe_provider(settings, monitor, freshness_out=freshness_out)
    except Exception:
        probe_failed = True
        provider_state = "unavailable"
        logger.warning(
            "watcher_activation_provider_probe_failed",
            worker_instance_id=worker_instance_id,
        )

    pins = _architecture_pins()
    paper_only = (
        settings.execution_mode is ExecutionMode.PAPER
        and not settings.enable_real_trading
        and not settings.real_trading_enabled
        and settings.exchange_mode.value in _PAPER_EXCHANGES
    )
    return ActivationObservations(
        migration_revision=revision,
        expected_migration_revision=expected,
        provider_state=provider_state,
        lineage_valid=lineage_valid and not probe_failed,
        lease_backend="postgres" if pins.postgres_leases else "other",
        worker_instance_id=worker_instance_id,
        fencing_enabled=pins.fencing_enabled,
        restart_recovery_enabled=pins.restart_recovery_enabled,
        idempotency_enabled=pins.idempotency_enabled,
        approved_compiled_only=pins.approved_compiled_only,
        canonical_live_evidence=pins.canonical_assembler
        and not evidence_source_is_replay(settings.perpetual_evidence_source),
        freshness_fail_closed=pins.freshness_fail_closed,
        confirmed_setup_only=pins.confirmed_setup_only,
        risk_block_final=pins.risk_block_final,
        paper_execution_only=paper_only,
        kill_switch_preserved=pins.kill_switch_preserved,
        probe_failed=probe_failed,
    )


def run_staging_activation(
    settings: Settings,
    *,
    observations: ActivationObservations | None = None,
    start: Callable[[], None] | None = None,
    session_factory: sessionmaker[Session] | None = None,
    worker_instance_id: str | None = None,
) -> ActivationDecision:
    """Start the dedicated paper worker only when preflight allows it.

    Injected ``observations`` and ``start`` keep tests off the network. The
    production path builds the existing runtime and installs the health gate.
    """

    from app.workers.watcher_paper import new_worker_instance_id

    try:
        instance_id = worker_instance_id or new_worker_instance_id(settings.watcher_paper_worker_id)
    except ValueError:
        instance_id = ""
    config = activation_config_from_settings(settings)
    if observations is None:
        factory = session_factory if session_factory is not None else _default_session_factory()
        try:
            observations = collect_live_observations(
                settings,
                session_factory=factory,
                worker_instance_id=instance_id,
            )
        except Exception:
            logger.warning("watcher_activation_collect_failed", worker_instance_id=instance_id)
            observations = _probe_failed_observations(instance_id)
    decision = evaluate_activation(config, observations)
    if not decision.allowed:
        plan = rollback_plan_for(decision)
        logger.warning(
            "watcher_paper_activation_refused",
            reasons=list(decision.reason_codes),
            primary_reason=decision.primary_reason,
            rollback_triggered=plan.triggered,
            modifies_environment=plan.modifies_environment,
            deploys=plan.deploys,
            paper_only=True,
        )
        return decision
    if start is not None:
        start()
        return decision
    from app.telegram_activation.errors import TelegramActivationError

    try:
        _run_cleared_runtime(settings, config, instance_id, session_factory)
    except TelegramActivationError as exc:
        logger.warning(
            "watcher_paper_activation_refused",
            reasons=["telegram_projection_refused"],
            primary_reason="telegram_projection_refused",
            projection_reason=exc.reason,
            paper_only=True,
        )
        return ActivationDecision(
            allowed=False,
            reason_codes=("telegram_projection_refused",),
            primary_reason="telegram_projection_refused",
            phase="preflight",
        )
    return decision


def main(argv: Sequence[str] | None = None) -> int:
    """Operator commands. None of them arm staging or start the worker."""

    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["--self-check"]:
        return _self_check()
    if args == ["--rollback-plan"]:
        for command in ROLLBACK_COMMANDS:
            print(command)
        print("NOT APPLIED")
        return 0
    if len(args) == 2 and args[0] == "--smoke-json":
        return _smoke_json(args[1])
    print(
        "Watcher paper activation is not started by this command. "
        "Preflight lives in python -m app.workers.watcher_paper.",
        file=sys.stderr,
    )
    return 2


def _self_check() -> int:
    from app.workers.watcher_paper import new_worker_instance_id

    head = expected_migration_head()
    if head is None:
        print(f"FAIL: migration head {head!r}", file=sys.stderr)
        return 1
    config = _sample_config()
    worker_id = new_worker_instance_id(config.configured_worker_id)
    healthy = _sample_observations(worker_id, revision=head)
    cleared = evaluate_activation(config, healthy)
    if not cleared.allowed:
        print(f"FAIL: healthy preflight {cleared.reason_codes}", file=sys.stderr)
        return 1
    pair = _replace_config(
        config,
        telegram_paper_activation_armed=True,
        telegram_interaction_enabled=True,
        telegram_inbound_mode="polling",
        telegram_network_permitted=True,
    )
    if not evaluate_activation(pair, healthy).allowed:
        print("FAIL: controlled telegram pair was refused", file=sys.stderr)
        return 1
    proofs = (
        (
            "replay",
            _replace_config(config, evidence_source="replay"),
            healthy,
            "replay_evidence_while_live_required",
        ),
        (
            "provider",
            config,
            _replace_obs(healthy, provider_state="unavailable"),
            "provider_unavailable",
        ),
        ("lineage", config, _replace_obs(healthy, lineage_valid=False), "strategy_lineage_invalid"),
        (
            "real_trading",
            _replace_config(config, enable_real_trading=True),
            healthy,
            "real_trading_enabled",
        ),
        (
            "legacy_telegram",
            _replace_config(config, telegram_alerts_enabled=True),
            healthy,
            "telegram_forbidden",
        ),
        (
            "partial_telegram",
            _replace_config(config, telegram_interaction_enabled=True),
            healthy,
            "telegram_forbidden",
        ),
        (
            "migration",
            config,
            _replace_obs(healthy, migration_revision="not-a-head"),
            "migration_unhealthy",
        ),
    )
    for name, proof_config, proof_obs, reason in proofs:
        started = False

        def _start() -> None:
            nonlocal started
            started = True

        decision = start_if_allowed(proof_config, proof_obs, _start)
        if decision.allowed or decision.primary_reason != reason or started:
            print(f"FAIL: {name} {decision.primary_reason} started={started}", file=sys.stderr)
            return 1
        plan = execute_rollback_process(decision=decision, stop=_start)
        if (
            not plan.triggered
            or plan.modifies_environment
            or plan.deploys
            or plan.activates_telegram
            or plan.enables_real_trading
        ):
            print(f"FAIL: rollback plan for {name}", file=sys.stderr)
            return 1
    print("watcher paper activation self-check passed")
    return 0


def _smoke_json(raw: str) -> int:
    payload = raw if raw.lstrip().startswith("{") else _read_text(raw)
    try:
        snapshot = json.loads(payload)
    except json.JSONDecodeError:
        print("FAIL: smoke snapshot is not JSON", file=sys.stderr)
        return 2
    if not isinstance(snapshot, dict):
        print("FAIL: smoke snapshot must be an object", file=sys.stderr)
        return 2
    report = evaluate_post_activation_smoke(snapshot)
    if report.passed:
        print("watcher paper activation smoke passed")
        return 0
    print("FAIL: " + ",".join(report.failures), file=sys.stderr)
    return 1


def _read_text(path: str) -> str:
    from pathlib import Path

    return Path(path).read_text(encoding="utf-8")


@dataclass(frozen=True, slots=True)
class StagingWatcherSession:
    """Cleared Watcher runtime. ``close`` releases the market monitor."""

    runtime: WatcherPaperRuntime
    close: Callable[[], None]


def open_staging_watcher_session(
    settings: Settings,
    config: WatcherPaperActivationConfig,
    instance_id: str,
    session_factory: sessionmaker[Session] | None = None,
) -> StagingWatcherSession:
    """Build the cleared paper Watcher. Does not loop and does not deliver."""

    from app.controlled_activation.projection import build_controlled_scan_hook
    from app.workers.watcher_paper import build_watcher_paper_runtime

    factory = session_factory if session_factory is not None else _default_session_factory()
    projection = build_controlled_scan_hook(settings, factory)
    runtime_box: dict[str, WatcherPaperRuntime] = {}
    monitor_box: dict[str, object] = {}
    freshness_box: dict[str, float | None] = {}

    def gate() -> ActivationDecision:
        monitor = monitor_box.get("monitor")
        if monitor is None:
            monitor = _build_monitor(settings)
            monitor_box["monitor"] = monitor
        try:
            current = collect_live_observations(
                settings,
                session_factory=factory,
                worker_instance_id=instance_id,
                monitor=monitor,
                freshness_out=freshness_box,
            )
        except Exception:
            logger.warning(
                "watcher_activation_runtime_probe_failed", worker_instance_id=instance_id
            )
            current = _probe_failed_observations(instance_id)
        runtime = runtime_box.get("runtime")
        if runtime is not None:
            runtime.note_market_observation(
                source=settings.perpetual_evidence_source,
                freshness_seconds=freshness_box.get("seconds"),
            )
        current = _with_heartbeat(current, runtime, settings)
        return runtime_health_decision(config, current)

    runtime = build_watcher_paper_runtime(
        settings,
        factory,
        enabled=True,
        activation_cleared=True,
        worker_id=instance_id,
        activation_gate=gate,
        scan_notification_hook=None if projection is None else projection.hook,
    )
    runtime_box["runtime"] = runtime

    def close() -> None:
        _close_monitor(monitor_box.get("monitor"))

    return StagingWatcherSession(runtime=runtime, close=close)


def _run_cleared_runtime(
    settings: Settings,
    config: WatcherPaperActivationConfig,
    instance_id: str,
    session_factory: sessionmaker[Session] | None,
) -> None:
    session = open_staging_watcher_session(
        settings,
        config,
        instance_id,
        session_factory,
    )
    try:
        session.runtime.run_forever()
    finally:
        session.close()


def _with_heartbeat(
    observations: ActivationObservations,
    runtime: object | None,
    settings: Settings,
) -> ActivationObservations:
    from dataclasses import replace
    from datetime import UTC, datetime

    if runtime is None:
        return observations
    snapshot = runtime.snapshot()  # type: ignore[attr-defined]
    if snapshot.cycles_completed <= 0 or snapshot.last_cycle_at is None:
        return observations
    age = (datetime.now(UTC) - snapshot.last_cycle_at).total_seconds()
    return replace(
        observations,
        runtime_heartbeat_age_seconds=age,
        runtime_heartbeat_limit_seconds=settings.watcher_heartbeat_stale_after_seconds,
    )


def _default_session_factory() -> sessionmaker[Session]:
    from app.db.session import get_session_factory

    return get_session_factory()


def _probe_failed_observations(worker_instance_id: str) -> ActivationObservations:
    return ActivationObservations(
        migration_revision=None,
        expected_migration_revision=expected_migration_head(),
        provider_state="unavailable",
        lineage_valid=False,
        lease_backend="other",
        worker_instance_id=worker_instance_id,
        fencing_enabled=False,
        restart_recovery_enabled=False,
        idempotency_enabled=False,
        approved_compiled_only=False,
        canonical_live_evidence=False,
        freshness_fail_closed=False,
        confirmed_setup_only=False,
        risk_block_final=False,
        paper_execution_only=True,
        kill_switch_preserved=True,
        probe_failed=True,
    )


def _probe_lineage(session: Session, settings: Settings) -> bool:
    from app.workers.watcher_paper_targets import lineage_targets_are_valid, list_paper_scan_targets

    scoped_org = (
        UUID(settings.watcher_paper_organization_id)
        if settings.watcher_paper_organization_id
        else None
    )
    try:
        targets = list_paper_scan_targets(
            session,
            symbols=settings.watcher_paper_symbols,
            organization_id=scoped_org,
            limit=settings.watcher_paper_max_scopes_per_cycle,
        )
    except Exception:
        session.rollback()
        return False
    return lineage_targets_are_valid(targets)


def _probe_provider(
    settings: Settings,
    monitor: object | None,
    *,
    freshness_out: dict[str, float | None] | None = None,
) -> str:
    if perpetual_source_is_replay(settings) or bool(getattr(monitor, "replay", False)):
        return "replay"
    resolved = monitor if monitor is not None else _build_monitor(settings)
    try:
        snapshot = resolved.latest("BTCUSDT")  # type: ignore[attr-defined]
    finally:
        # A probe-only monitor owns HTTP clients; preflight may now repeat.
        if monitor is None:
            _close_monitor(resolved)
    last_update = getattr(snapshot, "last_update", None)
    evaluated_at = getattr(snapshot, "evaluated_at", None)
    if freshness_out is not None and last_update is not None and evaluated_at is not None:
        freshness_out["seconds"] = max(0.0, (evaluated_at - last_update).total_seconds())
    return provider_state_for(
        availability=str(snapshot.availability.value),
        reason=str(snapshot.reason.value),
        is_live=bool(snapshot.is_live),
    )


def _close_monitor(monitor: object | None) -> None:
    if monitor is None:
        return
    close = getattr(monitor, "close", None)
    if callable(close):
        close()
    source = getattr(monitor, "_source", None)
    source_close = getattr(source, "close", None)
    if callable(source_close):
        source_close()


def _build_monitor(settings: Settings) -> object:
    from app.market_contracts.adapters.factory import resolve_perpetual_evidence_source
    from app.market_monitor.factory import build_perpetual_market_monitor

    source = resolve_perpetual_evidence_source(settings)
    try:
        return build_perpetual_market_monitor(settings, source=source)
    except BaseException:
        # The source owns HTTP clients; a half-built monitor must not leak them.
        close = getattr(source, "close", None)
        try:
            if callable(close):
                close()
        except Exception:
            logger.warning("watcher_activation_monitor_close_failed", exc_info=True)
        raise


@dataclass(frozen=True, slots=True)
class _ArchitecturePins:
    postgres_leases: bool
    fencing_enabled: bool
    restart_recovery_enabled: bool
    idempotency_enabled: bool
    approved_compiled_only: bool
    canonical_assembler: bool
    freshness_fail_closed: bool
    confirmed_setup_only: bool
    risk_block_final: bool
    kill_switch_preserved: bool


def _architecture_pins() -> _ArchitecturePins:
    import inspect

    from app.market_monitor.watcher_gate import watcher_evidence_error_for_monitor
    from app.persistence.watcher_postgres import PostgresWatcherStore
    from app.schemas.common import RiskAction
    from app.services.risk.engine import _ACTION_RANK
    from app.watcher.orchestrator import _CONFIRMED_SETUP
    from app.workers import watcher_paper
    from app.workers.watcher_market import SymbolMarketFactory
    from app.workers.watcher_paper_targets import list_watchlist_scan_targets

    postgres = "build_postgres_watcher_store" in inspect.getsource(
        watcher_paper.build_watcher_paper_runtime
    ) and hasattr(PostgresWatcherStore, "claim_lease")
    risk_final = (
        _ACTION_RANK[RiskAction.BLOCK]
        > _ACTION_RANK[RiskAction.WARN]
        >= _ACTION_RANK[RiskAction.ALLOW]
    )
    target_source = inspect.getsource(list_watchlist_scan_targets)
    return _ArchitecturePins(
        postgres_leases=postgres,
        fencing_enabled=postgres,
        restart_recovery_enabled=postgres,
        idempotency_enabled=callable(
            getattr(watcher_paper.WatcherPaperRuntime, "_idempotency_key", None)
        ),
        approved_compiled_only="resolve_executable_strategy_policy" in target_source,
        canonical_assembler=(
            "SymbolMarketFactory" in inspect.getsource(watcher_paper.default_paper_evidence_factory)
            and "FirstSliceEvidenceAssembler" in inspect.getsource(SymbolMarketFactory.__call__)
            and "AssemblingWatcherScanEvidence" in inspect.getsource(SymbolMarketFactory.__call__)
        ),
        freshness_fail_closed=callable(watcher_evidence_error_for_monitor),
        confirmed_setup_only=_CONFIRMED_SETUP == "confirmed_setup",
        risk_block_final=risk_final,
        kill_switch_preserved=(
            "automated_paper_actions_blocked"
            in inspect.getsource(watcher_paper.WatcherPaperRuntime._scan_target)
            and "read_kill_switch_active"
            in inspect.getsource(watcher_paper.WatcherPaperRuntime._kill_switch_is_active)
            and "return True"
            in inspect.getsource(watcher_paper.WatcherPaperRuntime._kill_switch_is_active)
        ),
    )


def _paper_runtime_config(config: WatcherPaperActivationConfig) -> bool:
    """Staging, or production under the production_demo profile."""

    if config.environment == Environment.STAGING.value:
        return True
    return config.environment == Environment.PRODUCTION.value and config.production_demo


def _environment_reasons(config: WatcherPaperActivationConfig) -> set[str]:
    found: set[str] = set()
    if _paper_runtime_config(config):
        pass
    elif config.environment == Environment.PRODUCTION.value:
        found.add("production_forbidden")
    elif config.environment != Environment.STAGING.value:
        found.add("staging_only")
    if not config.armed or not config.orchestration_enabled:
        found.add("activation_disarmed")
    return found


def _safety_reasons(
    config: WatcherPaperActivationConfig,
    observations: ActivationObservations,
) -> set[str]:
    found: set[str] = set()
    if config.enable_real_trading or config.real_trading_enabled:
        found.add("real_trading_enabled")
    if (
        config.execution_mode != ExecutionMode.PAPER.value
        or config.exchange_mode not in _PAPER_EXCHANGES
        or not observations.paper_execution_only
    ):
        found.add("paper_execution_required")
    if _telegram_enabled(config):
        found.add("telegram_forbidden")
    if _legacy_watcher_enabled(config):
        found.add("legacy_watcher_forbidden")
    if not observations.kill_switch_preserved:
        found.add("kill_switch_not_preserved")
    if not observations.risk_block_final:
        found.add("risk_block_not_final")
    return found


def _evidence_reasons(
    config: WatcherPaperActivationConfig,
    observations: ActivationObservations,
) -> set[str]:
    found: set[str] = set()
    if observations.probe_failed:
        found.add("activation_probe_failed")
    if evidence_source_is_replay(config.evidence_source) or observations.provider_state == "replay":
        found.add("replay_evidence_while_live_required")
    if observations.provider_state == "unavailable":
        found.add("provider_unavailable")
    if (
        observations.provider_state in {"stale", "degraded"}
        or not observations.freshness_fail_closed
    ):
        found.add("freshness_fail_closed")
    if not observations.canonical_live_evidence:
        found.add("canonical_live_evidence_required")
    if not observations.lineage_valid:
        found.add("strategy_lineage_invalid")
    if (
        not observations.migration_revision
        or not observations.expected_migration_revision
        or observations.migration_revision != observations.expected_migration_revision
    ):
        found.add("migration_unhealthy")
    return found


def _authority_reasons(observations: ActivationObservations) -> set[str]:
    found: set[str] = set()
    if not observations.approved_compiled_only:
        found.add("approved_compiled_required")
    if not observations.confirmed_setup_only:
        found.add("candidate_authority_not_confirmed_setup")
    return found


def _lease_reasons(
    config: WatcherPaperActivationConfig,
    observations: ActivationObservations,
) -> set[str]:
    found: set[str] = set()
    if observations.lease_backend != "postgres":
        found.add("postgres_lease_required")
    if not worker_identity_is_unique(config.configured_worker_id, observations.worker_instance_id):
        found.add("worker_identity_not_unique")
    if not observations.fencing_enabled:
        found.add("fencing_required")
    if not observations.restart_recovery_enabled:
        found.add("restart_recovery_required")
    if not observations.idempotency_enabled:
        found.add("idempotency_required")
    return found


def _runtime_reasons(observations: ActivationObservations) -> set[str]:
    age = observations.runtime_heartbeat_age_seconds
    if age is None:
        return set()
    if age < 0 or age > float(observations.runtime_heartbeat_limit_seconds):
        return {"runtime_heartbeat_stale"}
    return set()


def _controlled_telegram_pair(config: WatcherPaperActivationConfig) -> bool:
    """Paper projection. Staging inbound is polling, and the network is on."""

    return (
        config.telegram_paper_activation_armed
        and config.telegram_interaction_enabled
        and config.telegram_inbound_mode == "polling"
        and config.telegram_network_permitted
        and not config.telegram_alerts_enabled
        and not config.automatic_telegram_delivery_enabled
    )


def _enrollment_posture(config: WatcherPaperActivationConfig) -> bool:
    """Enrollment on the Telegram process. It does not arm the projection."""

    return (
        not config.telegram_paper_activation_armed
        and config.telegram_interaction_enabled
        and config.telegram_inbound_mode == "polling"
        and config.telegram_network_permitted
        and not config.telegram_alerts_enabled
        and not config.automatic_telegram_delivery_enabled
    )


def _telegram_enabled(config: WatcherPaperActivationConfig) -> bool:
    if config.telegram_alerts_enabled or config.automatic_telegram_delivery_enabled:
        return True
    if _controlled_telegram_pair(config) or _enrollment_posture(config):
        return False
    return bool(
        config.telegram_interaction_enabled
        or config.telegram_paper_activation_armed
        or config.telegram_inbound_mode != "off"
        or config.telegram_network_permitted
    )


def _legacy_watcher_enabled(config: WatcherPaperActivationConfig) -> bool:
    return bool(
        config.market_watcher_enabled
        or config.market_watcher_bridge_enabled
        or config.market_watcher_bridge_auto_tick
    )


def _ordered(found: set[str]) -> tuple[str, ...]:
    known = tuple(code for code in REASON_PRIORITY if code in found)
    extra = tuple(sorted(found.difference(REASON_PRIORITY)))
    return known + extra


def _sample_config() -> WatcherPaperActivationConfig:
    return WatcherPaperActivationConfig(
        environment="staging",
        armed=True,
        orchestration_enabled=True,
        execution_mode="paper",
        enable_real_trading=False,
        real_trading_enabled=False,
        telegram_alerts_enabled=False,
        telegram_interaction_enabled=False,
        automatic_telegram_delivery_enabled=False,
        market_watcher_enabled=False,
        market_watcher_bridge_enabled=False,
        market_watcher_bridge_auto_tick=False,
        evidence_source="binance_usdm",
        exchange_mode="paper_internal",
        configured_worker_id="watcher-paper-1",
    )


def _sample_observations(worker_id: str, *, revision: str) -> ActivationObservations:
    return ActivationObservations(
        migration_revision=revision,
        expected_migration_revision=revision,
        provider_state="available",
        lineage_valid=True,
        lease_backend="postgres",
        worker_instance_id=worker_id,
        fencing_enabled=True,
        restart_recovery_enabled=True,
        idempotency_enabled=True,
        approved_compiled_only=True,
        canonical_live_evidence=True,
        freshness_fail_closed=True,
        confirmed_setup_only=True,
        risk_block_final=True,
        paper_execution_only=True,
        kill_switch_preserved=True,
    )


def _replace_config(
    config: WatcherPaperActivationConfig, **updates: object
) -> WatcherPaperActivationConfig:
    from dataclasses import replace

    return replace(config, **updates)  # type: ignore[arg-type]


def _replace_obs(observations: ActivationObservations, **updates: object) -> ActivationObservations:
    from dataclasses import replace

    return replace(observations, **updates)  # type: ignore[arg-type]


if __name__ == "__main__":
    raise SystemExit(main())
