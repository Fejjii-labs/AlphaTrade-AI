"""Production WatcherScanEvidencePort backed by the live/read-only assembler.

The live monitor gates current-quote and trade-stream freshness. Canonical
``FirstSliceEvidenceAssembler`` is the sole CanonicalEvidenceWindowV1 authority.
Wired into the paper Watcher worker through ``default_paper_evidence_factory``.
Staging/production Watcher flags stay false.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import NoReturn

import structlog
from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.evidence_pipeline.assembler import FirstSliceEvidenceAssembler
from app.evidence_pipeline.canonical import is_first_slice_read_projection
from app.evidence_pipeline.manual_resistance import persisted_resistance_evidence
from app.evidence_pipeline.types import AssembledCanonicalEvidence
from app.market_contracts.enums import SourceFamily
from app.market_contracts.errors import (
    ContractUnavailableError,
    MarketContractError,
    RegionalProviderFailureError,
    StaleEvidenceError,
)
from app.market_contracts.evidence_diagnostics import (
    diagnostic_from_exception,
    reason_for_exception,
)
from app.market_contracts.request_progress import notify_market_request_progress
from app.market_monitor.monitor import PerpetualMarketMonitor
from app.market_monitor.types import MarketMode, SymbolMonitorSnapshot
from app.market_monitor.watcher_gate import watcher_evidence_error_for_monitor
from app.market_monitor.watcher_port import MarketMonitorWatcherPort
from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
from app.signal_fusion.enums import EvidenceAdapterKind
from app.signal_fusion.errors import StrategyEvaluationPolicyError
from app.signal_fusion.first_slice_types import ManualResistanceEvidence
from app.signal_fusion.strategy_evaluation_policy import ExecutableStrategyPolicy
from app.watcher.contracts import EvaluationCommand
from app.watcher.errors import WatcherEvidenceUnavailableError, WatcherTenantMismatchError
from app.watcher.fusion_evaluation import ExecutablePolicyAuthority, WatcherCanonicalScanEvidence
from app.watcher.ports import WatcherStore

ExecutableResolver = Callable[[EvaluationCommand], ExecutableStrategyPolicy | None]
MonitorPort = PerpetualMarketMonitor | MarketMonitorWatcherPort
logger = structlog.get_logger(__name__)


def resolve_watcher_scan_policy(
    session: Session,
    command: EvaluationCommand,
    *,
    store: WatcherStore,
) -> ExecutableStrategyPolicy | None:
    """Resolve the tenant-scoped approved compiled strategy for one scan."""

    version = store.get_policy_version(command.request.policy_id, command.request.policy_version)
    if version is None or version.strategy_version_id is None:
        return None
    if not version.enabled:
        return None
    if version.identity.organization_id != command.request.organization_id:
        raise WatcherTenantMismatchError(
            "Watcher policy organization_id does not match the scan tenant."
        )
    try:
        return resolve_executable_strategy_policy(
            session,
            organization_id=command.request.organization_id,
            strategy_version_id=version.strategy_version_id,
        )
    except (StrategyEvaluationPolicyError, NotFoundError):
        return None


class AssemblingWatcherScanEvidence:
    """Loads freshly assembled first-slice evidence for one tenant scan.

    Read-projection placeholders never become WatcherCanonicalScanEvidence.
    The optional monitor is the current-quote / stream gate; the assembler is
    the sole CanonicalEvidenceWindowV1 producer.
    """

    def __init__(
        self,
        assembler: FirstSliceEvidenceAssembler,
        *,
        executable_resolver: ExecutableResolver | None = None,
        session: Session | None = None,
        watcher_store: WatcherStore | None = None,
        symbol: str = "BTCUSDT",
        monitor: MonitorPort | None = None,
    ) -> None:
        self._assembler = assembler
        self._executable_resolver = executable_resolver
        self._session = session
        self._store = watcher_store
        self._symbol = symbol
        self._monitor = monitor
        self._load_cache: dict[tuple[str, str, str], WatcherCanonicalScanEvidence | None] = {}
        self._last_assembly: tuple[AssembledCanonicalEvidence, ExecutableStrategyPolicy] | None = (
            None
        )

    def load(self, command: EvaluationCommand) -> WatcherCanonicalScanEvidence | None:
        """Return one assembled snapshot twice for evaluate and Candidate persist.

        The key is the scan's evaluation input, organization, and symbol. A
        later bar or tenant does not reuse this entry. Assessment still
        recomputes from the cached evidence.
        """

        notify_market_request_progress()
        key = (
            str(command.request.organization_id),
            command.evaluation_input_hash,
            self._symbol,
        )
        if key in self._load_cache:
            return self._load_cache[key]
        self._last_assembly = None
        # Evaluation and Candidate persistence share one scan, not every historical scan.
        self._load_cache.clear()
        loaded = self._load_uncached(command)
        self._load_cache[key] = loaded
        return loaded

    def last_assembly(self) -> tuple[AssembledCanonicalEvidence, ExecutableStrategyPolicy] | None:
        """Last successful assembly for this scan. Empty after a failed load."""

        return self._last_assembly

    def candidate_episode(self):
        """Lock the durable Brain episode under the existing Candidate fence."""
        if self._last_assembly is None:
            return None
        assembled, executable = self._last_assembly
        from app.schemas.nested_continuation import NestedContinuationSpec
        from app.strategy_brain.detector import detect_nested
        from app.strategy_brain.records import lock_candidate_episode
        from app.strategy_brain.sfp.contracts import SfpSpec

        if not isinstance(executable.authored_spec, (NestedContinuationSpec, SfpSpec)):
            return None
        if self._session is None:
            raise ValueError("Brain Candidate persistence requires a database session")
        if isinstance(executable.authored_spec, SfpSpec):
            from app.signal_fusion.enums import EvidenceRole
            from app.strategy_brain.sfp.detector import detect_sfp
            from app.strategy_brain.sfp_runtime.assessment import select_detection

            observations = tuple(
                o
                for o, role in zip(
                    assembled.assessment_command.public_observations,
                    assembled.assessment_command.selected_roles,
                    strict=True,
                )
                if role is EvidenceRole.STRUCTURE
            )
            scan = detect_sfp(
                assembled.bundle.bars_15m,
                observations,
                executable.authored_spec,
                evaluated_at=assembled.evaluated_at,
            )
            detection = select_detection(scan, assembled.bundle.bars_15m)
            events = (detection,) if detection is not None else ()
        else:
            events = detect_nested(
                assembled.bundle.bars_15m,
                executable.authored_spec,
                evaluated_at=assembled.evaluated_at,
            )
        if not events:
            raise ValueError("No Brain structural episode")
        return lock_candidate_episode(
            self._session,
            organization_id=executable.organization_id,
            version_id=executable.strategy_version_id,
            detection=events[-1],
        )

    def _load_uncached(self, command: EvaluationCommand) -> WatcherCanonicalScanEvidence | None:
        organization_id = command.request.organization_id
        production_authority = self._session is not None and self._store is not None
        if production_authority and self._monitor is None:
            raise WatcherEvidenceUnavailableError(
                "Production Watcher Candidate authority requires a market monitor.",
                reason_code="missing_monitor",
            )
        executable = self._resolve_executable(command)
        from app.strategy_brain.trendpulse_1r.contracts import TrendPulseSpec

        if executable is not None and isinstance(executable.authored_spec, TrendPulseSpec):
            raise WatcherEvidenceUnavailableError(
                "TrendPulse requires its bounded two-timeframe screening receipts.",
                reason_code="trendpulse_screening_receipts_required",
            )
        from app.schemas.nested_continuation import NestedContinuationSpec
        from app.strategy_brain.sfp.contracts import SfpSpec

        ohlcv_family = executable is not None and isinstance(
            executable.authored_spec, (NestedContinuationSpec, SfpSpec)
        )
        monitor_snapshot = None if ohlcv_family else self._monitor_snapshot()
        if monitor_snapshot is not None:
            gated = watcher_evidence_error_for_monitor(monitor_snapshot)
            if gated is not None:
                raise gated
        authority = (
            ExecutablePolicyAuthority.PERSISTED_APPROVED_COMPILED
            if self._session is not None and self._store is not None
            else ExecutablePolicyAuthority.IN_MEMORY_TEST_HELPER
        )
        if executable is None:
            return None
        if executable.organization_id != organization_id:
            raise WatcherTenantMismatchError(
                "Executable strategy policy organization_id does not match the scan tenant."
            )
        if is_first_slice_read_projection(
            strategy_version_id=executable.strategy_version_id,
            setup_definition_id=executable.compiled_setup_definition_id,
        ):
            return None
        policy = executable.fusion_policy
        if policy.organization_id != organization_id:
            raise WatcherTenantMismatchError(
                "Fusion policy organization_id does not match the scan tenant."
            )
        resistances: tuple[ManualResistanceEvidence, ...] = ()
        if self._session is not None:
            resistances = persisted_resistance_evidence(
                self._session,
                organization_id=organization_id,
                symbol=self._symbol,
            )
        try:
            if ohlcv_family:
                from app.strategy_brain.assembly import assemble_nested
                from app.strategy_brain.sfp_runtime.assembly import assemble_sfp

                assemble_family = (
                    assemble_sfp
                    if isinstance(executable.authored_spec, SfpSpec)
                    else assemble_nested
                )
                assembled = assemble_family(
                    self._assembler,
                    executable=executable,
                    organization_id=organization_id,
                    session=self._session,
                )
            else:
                assembled = self._assembler.assemble(
                    organization_id=organization_id,
                    symbol=self._symbol,
                    policy=policy,
                    adapter_kind=EvidenceAdapterKind.WATCHER,
                    resistances=resistances,
                )
        except Exception as exc:
            diagnostic = diagnostic_from_exception(exc)
            if diagnostic is not None:
                raise WatcherEvidenceUnavailableError(
                    diagnostic.watcher_reason,
                    reason_code=diagnostic.watcher_reason,
                    diagnostics=self._assembler.diagnostics,
                ) from exc
            return self._raise_undiagnosed(exc)
        if assembled.organization_id != organization_id:
            raise WatcherTenantMismatchError(
                "Assembled evidence belongs to a different organization."
            )
        if assembled.assessment_command.organization_id != organization_id:
            raise WatcherTenantMismatchError(
                "Assessment command organization_id does not match the scan tenant."
            )
        _reconcile_monitor_and_assembled(
            monitor_snapshot,
            assembled.replay,
            assembled.identity.source.family,
        )
        self._last_assembly = (assembled, executable)
        return WatcherCanonicalScanEvidence(
            organization_id=organization_id,
            policy=policy,
            executable_policy=executable,
            assessment_command=assembled.assessment_command,
            evidence=assembled.bundle,
            evaluated_at=assembled.evaluated_at,
            policy_authority=authority,
            evidence_diagnostics=() if ohlcv_family else self._assembler.diagnostics,
        )

    @staticmethod
    def _raise_undiagnosed(exc: Exception) -> NoReturn:
        # The wrapper retains its public reason codes. Log only the finite
        # diagnostic category, never exception text, provider payloads or URLs.
        logger.warning(
            "canonical_evidence_contract_rejected",
            failure_reason=reason_for_exception(exc).value,
        )
        if isinstance(exc, ContractUnavailableError):
            raise WatcherEvidenceUnavailableError(
                "Selected perpetual contract is unavailable.", reason_code=exc.reason
            ) from exc
        if isinstance(exc, StaleEvidenceError):
            raise WatcherEvidenceUnavailableError(
                "Canonical scan evidence is stale.",
                reason_code="stale_evidence",
            ) from exc
        if isinstance(exc, RegionalProviderFailureError):
            raise WatcherEvidenceUnavailableError(
                "Perpetual market provider is unavailable.",
                reason_code="provider_outage",
            ) from exc
        if isinstance(exc, MarketContractError):
            raise WatcherEvidenceUnavailableError(
                "Canonical scan evidence is unavailable.",
                reason_code="canonical_contract_invalid_contract",
            ) from exc
        raise WatcherEvidenceUnavailableError(
            "Canonical contract failed with an unexpected error.",
            reason_code="canonical_contract_unexpected_error",
        ) from exc

    def _resolve_executable(self, command: EvaluationCommand) -> ExecutableStrategyPolicy | None:
        if self._session is not None and self._store is not None:
            return resolve_watcher_scan_policy(self._session, command, store=self._store)
        if self._executable_resolver is None:
            return None
        # Injected resolvers are test helpers. They never become persisted authority.
        resolved = self._executable_resolver(command)
        if resolved is None:
            return None
        if is_first_slice_read_projection(
            strategy_version_id=resolved.strategy_version_id,
            setup_definition_id=resolved.compiled_setup_definition_id,
        ):
            return None
        return resolved

    def _monitor_snapshot(self) -> SymbolMonitorSnapshot | None:
        if self._monitor is None:
            return None
        try:
            snapshot = self._monitor.latest(self._symbol)
        except ContractUnavailableError as exc:
            raise WatcherEvidenceUnavailableError(
                "Selected perpetual contract is unavailable.", reason_code=exc.reason
            ) from exc
        if not isinstance(snapshot, SymbolMonitorSnapshot):
            raise WatcherEvidenceUnavailableError(
                "Canonical market monitor contract is invalid.",
                reason_code="canonical_market_monitor_invalid_contract",
            )
        return snapshot


def _reconcile_monitor_and_assembled(
    monitor_snapshot: SymbolMonitorSnapshot | None,
    assembled_replay: bool,
    assembled_family: SourceFamily | None = None,
) -> None:
    """Shared source must not split live vs replay authority or venue."""

    if monitor_snapshot is None:
        return
    monitor_replay = monitor_snapshot.mode is MarketMode.REPLAY
    if assembled_family is None:
        assembled_family = (
            SourceFamily.REPLAY_FIXTURE
            if assembled_replay
            else SourceFamily.BINANCE_USDM_FUTURES_PUBLIC
        )
    family_matches = monitor_snapshot.source_family is assembled_family
    if monitor_replay != assembled_replay or not family_matches:
        raise WatcherEvidenceUnavailableError(
            "Watcher monitor mode does not match canonical evidence replay flag.",
            reason_code="wrong_source",
        )
