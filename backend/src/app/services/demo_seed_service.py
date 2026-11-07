"""Idempotent synthetic demo data for local/staging walkthroughs (Slice 50).

All seeded entities are paper-only, clearly synthetic, and tagged in metadata.
Never enabled in production.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Environment, Settings
from app.core.errors import ForbiddenError, ValidationAppError
from app.db.models import (
    DailyRiskState,
    EmailVerificationToken,
    LessonCandidate,
    MarketWatcherObservation,
    Membership,
    Organization,
    OrganizationInvitation,
    PaperSignal,
    PaperTrade,
    PaperTradeEvent,
    PaperValidationAlert,
    PaperValidationRun,
    PasswordResetToken,
    RefreshToken,
    TradeJournal,
    User,
    UserStrategy,
    UserStrategyVersion,
)
from app.schemas.common import (
    AlertDeliveryChannel,
    AlertDeliveryStatus,
    BacktestStatus,
    LessonCandidateStatus,
    LessonSourceType,
    MarketWatcherObservationStatus,
    MembershipRole,
    PaperAlertSeverity,
    PaperAlertSource,
    PaperAlertType,
    PaperSignalStatus,
    PaperTradeStatus,
    PaperValidationRuntimeMode,
    PaperValidationStatus,
    StrategyId,
    StrategyValidationStatus,
    TradeDirection,
    TradeResult,
)
from app.schemas.risk import UserRiskSettingsUpdate
from app.security.passwords import hash_password, validate_password
from app.services.risk.settings_service import RiskSettingsService

logger = structlog.get_logger(__name__)

DEMO_EMAIL = "demo@alphatrade.ai"
DEMO_ORG_NAME = "AlphaTrade Demo Workspace"
DEMO_SEED_TAG = "demo-seed-v1"
BOOTSTRAP_SEED_EMAIL_RE = re.compile(
    r"^(?:demo-seed-bootstrap|seed-bootstrap)-\d+@example\.com$",
)

# Stable UUIDs — idempotent upserts and targeted cleanup.
DEMO_ORG_ID = uuid.UUID("a1000001-0000-4000-8000-000000000001")
DEMO_USER_ID = uuid.UUID("a1000001-0000-4000-8000-000000000002")

DEMO_STRATEGY_BTC = uuid.UUID("a1000010-0000-4000-8000-000000000010")
DEMO_STRATEGY_ETH = uuid.UUID("a1000011-0000-4000-8000-000000000011")
DEMO_STRATEGY_SOL = uuid.UUID("a1000012-0000-4000-8000-000000000012")

DEMO_VERSION_BTC = uuid.UUID("a1000020-0000-4000-8000-000000000020")
DEMO_VERSION_ETH = uuid.UUID("a1000021-0000-4000-8000-000000000021")
DEMO_VERSION_SOL = uuid.UUID("a1000022-0000-4000-8000-000000000022")

DEMO_RUN_ACTIVE = uuid.UUID("a1000030-0000-4000-8000-000000000030")
DEMO_RUN_PASSED = uuid.UUID("a1000031-0000-4000-8000-000000000031")
DEMO_RUN_FAILED = uuid.UUID("a1000032-0000-4000-8000-000000000032")

DEMO_SIGNAL_ACTIVE = uuid.UUID("a1000050-0000-4000-8000-000000000050")
DEMO_TRADE_OPEN = uuid.UUID("a1000040-0000-4000-8000-000000000040")
DEMO_TRADE_CLOSED = uuid.UUID("a1000041-0000-4000-8000-000000000041")

DEMO_ALERT_RISK = uuid.UUID("a1000060-0000-4000-8000-000000000060")
DEMO_ALERT_WATCHER = uuid.UUID("a1000061-0000-4000-8000-000000000061")
DEMO_ALERT_PAPER = uuid.UUID("a1000062-0000-4000-8000-000000000062")
DEMO_ALERT_DISCIPLINE = uuid.UUID("a1000063-0000-4000-8000-000000000063")

DEMO_LESSON_PENDING_1 = uuid.UUID("a1000070-0000-4000-8000-000000000070")
DEMO_LESSON_PENDING_2 = uuid.UUID("a1000071-0000-4000-8000-000000000071")
DEMO_LESSON_PENDING_3 = uuid.UUID("a1000072-0000-4000-8000-000000000072")
DEMO_LESSON_ACCEPTED_1 = uuid.UUID("a1000073-0000-4000-8000-000000000073")
DEMO_LESSON_ACCEPTED_2 = uuid.UUID("a1000074-0000-4000-8000-000000000074")

DEMO_JOURNAL_1 = uuid.UUID("a1000080-0000-4000-8000-000000000080")
DEMO_JOURNAL_2 = uuid.UUID("a1000081-0000-4000-8000-000000000081")
DEMO_JOURNAL_3 = uuid.UUID("a1000082-0000-4000-8000-000000000082")
DEMO_JOURNAL_4 = uuid.UUID("a1000083-0000-4000-8000-000000000083")

DEMO_OBSERVATION_1 = uuid.UUID("a1000090-0000-4000-8000-000000000090")
DEMO_OBSERVATION_2 = uuid.UUID("a1000091-0000-4000-8000-000000000091")


@dataclass(frozen=True)
class DemoSeedResult:
    organization_id: uuid.UUID
    user_id: uuid.UUID
    email: str
    strategies_seeded: int
    paper_runs_seeded: int
    alerts_seeded: int
    lessons_seeded: int
    journals_seeded: int
    paper_only: bool = True
    synthetic: bool = True


def assert_demo_seed_allowed(settings: Settings) -> None:
    """Refuse demo seed outside local/staging or when explicitly disabled."""
    if settings.environment is Environment.PRODUCTION:
        raise ForbiddenError("Demo seed is not available in production.")
    if settings.real_trading_enabled:
        raise ForbiddenError("Demo seed refused while real trading is enabled.")
    if settings.environment is Environment.STAGING and not settings.demo_seed_enabled:
        raise ForbiddenError(
            "Demo seed on staging requires DEMO_SEED_ENABLED=true.",
        )


def resolve_demo_password(settings: Settings, *, explicit: str | None = None) -> str:
    """Resolve demo password from explicit arg or env — never logged.

    An explicit (request-body) password is honoured only in ``local``; elsewhere
    the server-side ``DEMO_SEED_PASSWORD`` is the single source so an API caller
    can never choose the shared demo account's credentials.
    """
    import os

    if explicit and settings.environment is not Environment.LOCAL:
        logger.warning(
            "demo_seed_explicit_password_ignored", environment=settings.environment.value
        )
        explicit = None
    password = explicit or os.environ.get("DEMO_SEED_PASSWORD")
    if password:
        validate_password(password, settings)
        return password
    if settings.environment is Environment.LOCAL:
        password = "DemoPaper2026!"
        validate_password(password, settings)
        return password
    raise ValidationAppError(
        "Set DEMO_SEED_PASSWORD for staging demo seed (never commit it).",
    )


def assert_demo_seed_requester_allowed(
    session: Session,
    *,
    requester_organization_id: uuid.UUID,
) -> None:
    """API callers may only re-seed from inside the demo org.

    The single exception is first-time bootstrap (demo org absent): the demo
    password still comes from server env, so the caller gains no access to it.
    """
    if requester_organization_id == DEMO_ORG_ID:
        return
    if session.get(Organization, DEMO_ORG_ID) is None:
        return
    raise ForbiddenError("Demo seed is restricted to the demo workspace.")


class DemoSeedService:
    """Seed or refresh synthetic paper-only demo tenant data."""

    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings

    def seed(
        self,
        *,
        password: str | None = None,
        requester_user_id: uuid.UUID | None = None,
        requester_organization_id: uuid.UUID | None = None,
    ) -> DemoSeedResult:
        """Seed the demo tenant.

        ``requester_*`` are set by the API route. When present the caller must be
        in the demo org (or the demo org must not exist yet), and bootstrap
        cleanup only removes the caller's own throwaway bootstrap account. The
        operator CLI (no requester) keeps the full bootstrap sweep.
        """
        assert_demo_seed_allowed(self._settings)
        if self._settings.execution_mode.value != "paper":
            raise ForbiddenError("Demo seed requires execution_mode=paper.")
        if requester_organization_id is not None:
            assert_demo_seed_requester_allowed(
                self._session, requester_organization_id=requester_organization_id
            )
        resolved_password = resolve_demo_password(self._settings, explicit=password)
        org, user = self._ensure_demo_identity(resolved_password)
        self._clear_demo_entities()
        self._seed_strategies(org.id, user.id)
        self._seed_paper_validation(org.id, user.id)
        self._seed_alerts(org.id, user.id)
        self._seed_lessons(org.id, user.id)
        self._seed_journals(org.id, user.id)
        self._seed_risk_settings(org.id, user.id)
        self._seed_market_watcher(org.id, user.id)
        bootstrap_removed = self._cleanup_bootstrap_seed_accounts(
            only_user_id=requester_user_id if requester_organization_id is not None else None,
            api_request=requester_organization_id is not None,
        )
        self._session.commit()
        logger.info(
            "demo_seed_completed",
            organization_id=str(org.id),
            user_id=str(user.id),
            email=DEMO_EMAIL,
            bootstrap_accounts_removed=bootstrap_removed,
        )
        return DemoSeedResult(
            organization_id=org.id,
            user_id=user.id,
            email=DEMO_EMAIL,
            strategies_seeded=3,
            paper_runs_seeded=3,
            alerts_seeded=4,
            lessons_seeded=5,
            journals_seeded=4,
        )

    def _ensure_demo_identity(self, password: str) -> tuple[Organization, User]:
        org = self._session.get(Organization, DEMO_ORG_ID)
        if org is None:
            org = Organization(id=DEMO_ORG_ID, name=DEMO_ORG_NAME)
            self._session.add(org)
        else:
            org.name = DEMO_ORG_NAME

        user = self._session.scalar(select(User).where(User.email == DEMO_EMAIL))
        if user is None:
            user = User(
                id=DEMO_USER_ID,
                email=DEMO_EMAIL,
                hashed_password=hash_password(password, self._settings),
                timezone="Europe/Berlin",
                email_verified=True,
            )
            self._session.add(user)
            self._session.flush()
            self._session.add(
                Membership(
                    user_id=user.id,
                    organization_id=org.id,
                    role=MembershipRole.OWNER,
                )
            )
        else:
            user.hashed_password = hash_password(password, self._settings)
            user.email_verified = True
            user.timezone = "Europe/Berlin"
            membership = self._session.scalar(
                select(Membership).where(
                    Membership.user_id == user.id,
                    Membership.organization_id == org.id,
                )
            )
            if membership is None:
                self._session.add(
                    Membership(
                        user_id=user.id,
                        organization_id=org.id,
                        role=MembershipRole.OWNER,
                    )
                )
        self._session.flush()
        return org, user

    def _cleanup_bootstrap_seed_accounts(
        self,
        *,
        only_user_id: uuid.UUID | None = None,
        api_request: bool = False,
    ) -> int:
        """Remove temporary API seed bootstrap owners; never touches demo tenant.

        API requests may only remove the caller's own bootstrap account
        (``only_user_id``); other tenants' rows are never touched from the API.
        """
        if api_request and only_user_id is None:
            return 0
        removed = 0
        stmt = select(User).where(User.id != DEMO_USER_ID, User.email != DEMO_EMAIL)
        if only_user_id is not None:
            stmt = stmt.where(User.id == only_user_id)
        candidates = self._session.scalars(stmt).all()
        for user in candidates:
            if not BOOTSTRAP_SEED_EMAIL_RE.match(user.email):
                continue
            try:
                with self._session.begin_nested():
                    memberships = self._session.scalars(
                        select(Membership).where(Membership.user_id == user.id)
                    ).all()
                    org_ids = [
                        membership.organization_id
                        for membership in memberships
                        if membership.organization_id != DEMO_ORG_ID
                    ]
                    self._session.execute(
                        delete(RefreshToken).where(RefreshToken.user_id == user.id)
                    )
                    self._session.execute(
                        delete(EmailVerificationToken).where(
                            EmailVerificationToken.user_id == user.id
                        )
                    )
                    self._session.execute(
                        delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
                    )
                    self._session.execute(
                        delete(OrganizationInvitation).where(
                            OrganizationInvitation.invited_by_user_id == user.id
                        )
                    )
                    self._session.execute(delete(Membership).where(Membership.user_id == user.id))
                    for org_id in org_ids:
                        remaining_members = self._session.scalar(
                            select(func.count())
                            .select_from(Membership)
                            .where(Membership.organization_id == org_id)
                        )
                        if remaining_members == 0:
                            self._session.execute(
                                delete(OrganizationInvitation).where(
                                    OrganizationInvitation.organization_id == org_id
                                )
                            )
                            self._session.execute(
                                delete(Organization).where(Organization.id == org_id)
                            )
                    self._session.execute(delete(User).where(User.id == user.id))
                removed += 1
            except Exception as exc:
                logger.warning(
                    "bootstrap_cleanup_skipped",
                    email=user.email,
                    error_type=type(exc).__name__,
                )
        return removed

    def _clear_demo_entities(self) -> None:
        trade_ids = [DEMO_TRADE_OPEN, DEMO_TRADE_CLOSED]
        self._session.execute(
            delete(PaperTradeEvent).where(PaperTradeEvent.paper_trade_id.in_(trade_ids))
        )
        self._session.execute(
            delete(PaperValidationAlert).where(
                PaperValidationAlert.id.in_(
                    [
                        DEMO_ALERT_RISK,
                        DEMO_ALERT_WATCHER,
                        DEMO_ALERT_PAPER,
                        DEMO_ALERT_DISCIPLINE,
                    ]
                )
            )
        )
        self._session.execute(
            delete(MarketWatcherObservation).where(
                MarketWatcherObservation.id.in_([DEMO_OBSERVATION_1, DEMO_OBSERVATION_2])
            )
        )
        self._session.execute(delete(PaperTrade).where(PaperTrade.id.in_(trade_ids)))
        self._session.execute(delete(PaperSignal).where(PaperSignal.id == DEMO_SIGNAL_ACTIVE))
        self._session.execute(
            delete(LessonCandidate).where(
                LessonCandidate.id.in_(
                    [
                        DEMO_LESSON_PENDING_1,
                        DEMO_LESSON_PENDING_2,
                        DEMO_LESSON_PENDING_3,
                        DEMO_LESSON_ACCEPTED_1,
                        DEMO_LESSON_ACCEPTED_2,
                    ]
                )
            )
        )
        self._session.execute(
            delete(TradeJournal).where(
                TradeJournal.id.in_(
                    [DEMO_JOURNAL_1, DEMO_JOURNAL_2, DEMO_JOURNAL_3, DEMO_JOURNAL_4]
                )
            )
        )
        self._session.execute(
            delete(PaperValidationRun).where(
                PaperValidationRun.id.in_([DEMO_RUN_ACTIVE, DEMO_RUN_PASSED, DEMO_RUN_FAILED])
            )
        )
        self._session.execute(
            delete(UserStrategyVersion).where(
                UserStrategyVersion.id.in_([DEMO_VERSION_BTC, DEMO_VERSION_ETH, DEMO_VERSION_SOL])
            )
        )
        self._session.execute(
            delete(UserStrategy).where(
                UserStrategy.id.in_([DEMO_STRATEGY_BTC, DEMO_STRATEGY_ETH, DEMO_STRATEGY_SOL])
            )
        )
        self._session.flush()

    def _strategy_card(
        self,
        *,
        name: str,
        assets: list[str],
        timeframes: list[str],
        entry: list[str],
        confirmation: list[str],
        invalidation: list[str],
        stop: list[str],
        tp: list[str],
        runner: list[str],
        sizing: list[str],
        no_trade: list[str],
        validation: StrategyValidationStatus,
        backtest: BacktestStatus,
        paper_status: PaperValidationStatus,
    ) -> dict:
        return {
            "strategy_name": name,
            "market_type": "crypto_perp",
            "asset_universe": assets,
            "timeframes": timeframes,
            "entry_conditions": entry,
            "confirmation_conditions": confirmation,
            "invalidation": invalidation,
            "stop_loss": stop,
            "take_profit_plan": tp,
            "runner_plan": runner,
            "position_sizing": sizing,
            "add_rules": [],
            "no_trade_rules": no_trade,
            "backtest_rules": ["Synthetic demo backtest — paper only."],
            "success_criteria": ["Follow plan; journal every exit."],
            "validation_status": validation.value,
        }

    def _seed_strategies(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        specs = [
            (
                DEMO_STRATEGY_BTC,
                DEMO_VERSION_BTC,
                "BTC liquidity sweep reversal",
                StrategyId.LIQUIDITY_SWEEP_REVERSAL,
                ["BTCUSDT"],
                ["15m", "1h"],
                StrategyValidationStatus.VALIDATED,
                BacktestStatus.COMPLETED,
                PaperValidationStatus.IN_PROGRESS,
            ),
            (
                DEMO_STRATEGY_ETH,
                DEMO_VERSION_ETH,
                "ETH trend pullback continuation",
                StrategyId.HTF_TREND_PULLBACK,
                ["ETHUSDT"],
                ["1h", "4h"],
                StrategyValidationStatus.VALIDATED,
                BacktestStatus.COMPLETED,
                PaperValidationStatus.PASSED,
            ),
            (
                DEMO_STRATEGY_SOL,
                DEMO_VERSION_SOL,
                "SOL breakout retest strategy",
                StrategyId.PASSIVE_LEVEL_ORDER,
                ["SOLUSDT"],
                ["15m", "1h"],
                StrategyValidationStatus.DRAFT,
                BacktestStatus.NOT_RUN,
                PaperValidationStatus.FAILED,
            ),
        ]
        for strat_id, ver_id, name, setup, assets, tfs, val, bt, pv in specs:
            self._session.add(
                UserStrategy(
                    id=strat_id,
                    organization_id=org_id,
                    user_id=user_id,
                    name=name,
                    setup_type=setup,
                    current_version=1,
                    enabled=True,
                    notes=f"[{DEMO_SEED_TAG}] Synthetic paper-only demo strategy.",
                    paper_eligible=setup != StrategyId.PASSIVE_LEVEL_ORDER,
                )
            )
            self._session.add(
                UserStrategyVersion(
                    id=ver_id,
                    strategy_id=strat_id,
                    version=1,
                    card=self._strategy_card(
                        name=name,
                        assets=assets,
                        timeframes=tfs,
                        entry=[f"{name}: liquidity sweep / pullback trigger (demo)."],
                        confirmation=["Volume expansion and structure reclaim (demo)."],
                        invalidation=["Close beyond invalidation level (demo)."],
                        stop=["1.5R beyond sweep wick (demo)."],
                        tp=["Partial at 1R; runner trail (demo)."],
                        runner=["Trail below last swing (demo)."],
                        sizing=["Risk 0.75% paper balance per trade (demo)."],
                        no_trade=["No trade during major news window (demo)."],
                        validation=val,
                        backtest=bt,
                        paper_status=pv,
                    ),
                    validation_status=val,
                    backtest_status=bt,
                    paper_validation_status=pv,
                )
            )
        self._session.flush()

    def _seed_paper_validation(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        runs = [
            (
                DEMO_RUN_ACTIVE,
                DEMO_STRATEGY_BTC,
                DEMO_VERSION_BTC,
                PaperValidationStatus.IN_PROGRESS,
                PaperValidationRuntimeMode.AUTO_PAPER,
                True,
                None,
                {"win_rate": 0.55, "sample_trades": 12},
            ),
            (
                DEMO_RUN_PASSED,
                DEMO_STRATEGY_ETH,
                DEMO_VERSION_ETH,
                PaperValidationStatus.PASSED,
                PaperValidationRuntimeMode.SCAN_ONLY,
                True,
                now - timedelta(days=2),
                {"win_rate": 0.62, "sample_trades": 24, "net_pnl": "420.50"},
            ),
            (
                DEMO_RUN_FAILED,
                DEMO_STRATEGY_SOL,
                DEMO_VERSION_SOL,
                PaperValidationStatus.FAILED,
                PaperValidationRuntimeMode.SCAN_ONLY,
                False,
                now - timedelta(days=1),
                {"blockers": ["needs_structured_rules", "insufficient_sample"]},
            ),
        ]
        for run_id, strat_id, ver_id, status, mode, eligible, ended, metrics in runs:
            self._session.add(
                PaperValidationRun(
                    id=run_id,
                    strategy_id=strat_id,
                    strategy_version_id=ver_id,
                    organization_id=org_id,
                    user_id=user_id,
                    status=status,
                    runtime_mode=mode,
                    paper_eligible=eligible,
                    notes=f"[{DEMO_SEED_TAG}] Synthetic paper validation run.",
                    metrics=metrics,
                    ended_at=ended,
                    last_scan_at=now - timedelta(hours=1),
                )
            )
        self._session.flush()

        self._session.add(
            PaperSignal(
                id=DEMO_SIGNAL_ACTIVE,
                paper_validation_run_id=DEMO_RUN_ACTIVE,
                strategy_id=DEMO_STRATEGY_BTC,
                strategy_version_id=DEMO_VERSION_BTC,
                organization_id=org_id,
                user_id=user_id,
                symbol="BTCUSDT",
                exchange="mock",
                timeframe="15m",
                direction=TradeDirection.LONG,
                triggered=True,
                status=PaperSignalStatus.CONSUMED,
                confidence=0.72,
                suggested_entry=Decimal("65000"),
                stop_loss=Decimal("64200"),
                reason="Demo liquidity sweep reversal signal (paper only).",
                rule_engine_source="demo_seed",
            )
        )
        self._session.add(
            PaperTrade(
                id=DEMO_TRADE_OPEN,
                paper_validation_run_id=DEMO_RUN_ACTIVE,
                strategy_id=DEMO_STRATEGY_BTC,
                strategy_version_id=DEMO_VERSION_BTC,
                organization_id=org_id,
                user_id=user_id,
                created_from_signal_id=DEMO_SIGNAL_ACTIVE,
                symbol="BTCUSDT",
                exchange="mock",
                timeframe="15m",
                direction=TradeDirection.LONG,
                entry_price=Decimal("65000"),
                entry_time=now - timedelta(hours=2),
                size=Decimal("0.05"),
                stop_loss=Decimal("64200"),
                status=PaperTradeStatus.OPEN,
                rule_engine_source="demo_seed",
            )
        )
        self._session.add(
            PaperTrade(
                id=DEMO_TRADE_CLOSED,
                paper_validation_run_id=DEMO_RUN_PASSED,
                strategy_id=DEMO_STRATEGY_ETH,
                strategy_version_id=DEMO_VERSION_ETH,
                organization_id=org_id,
                user_id=user_id,
                symbol="ETHUSDT",
                exchange="mock",
                timeframe="1h",
                direction=TradeDirection.LONG,
                entry_price=Decimal("3200"),
                entry_time=now - timedelta(days=1, hours=4),
                exit_price=Decimal("3288"),
                exit_time=now - timedelta(days=1),
                size=Decimal("1.2"),
                stop_loss=Decimal("3150"),
                status=PaperTradeStatus.CLOSED,
                exit_reason="tp_hit",
                gross_pnl=Decimal("105.60"),
                net_pnl=Decimal("98.40"),
                fees=Decimal("4.20"),
                slippage=Decimal("3.00"),
                rule_engine_source="demo_seed",
            )
        )
        self._session.flush()

        today = date.today()
        self._session.execute(
            delete(DailyRiskState).where(
                DailyRiskState.organization_id == org_id,
                DailyRiskState.user_id == user_id,
                DailyRiskState.day == today,
            )
        )
        self._session.add(
            DailyRiskState(
                organization_id=org_id,
                user_id=user_id,
                day=today,
                realized_pnl=Decimal("98.40"),
                unrealized_pnl=Decimal("42.50"),
                daily_loss_limit=Decimal("250"),
                daily_target=Decimal("400"),
                max_trades_per_day=4,
                trade_count=2,
                locked=False,
            )
        )

    def _seed_alerts(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        meta = {"demo_seed": True, "paper_only": True, "synthetic": True}
        alerts = [
            (
                DEMO_ALERT_RISK,
                PaperAlertType.DAILY_LOSS_LOCK_WARNING,
                PaperAlertSeverity.WARNING,
                DEMO_STRATEGY_BTC,
                DEMO_RUN_ACTIVE,
                None,
                "Demo: approaching daily loss guard — paper only, no live orders.",
            ),
            (
                DEMO_ALERT_WATCHER,
                PaperAlertType.SETUP_SIGNAL_DETECTED,
                PaperAlertSeverity.INFO,
                DEMO_STRATEGY_ETH,
                DEMO_RUN_PASSED,
                None,
                "Demo market watcher: ETH pullback holding support (read-only observation).",
            ),
            (
                DEMO_ALERT_PAPER,
                PaperAlertType.PAPER_TRADE_OPENED,
                PaperAlertSeverity.INFO,
                DEMO_STRATEGY_BTC,
                DEMO_RUN_ACTIVE,
                DEMO_TRADE_OPEN,
                "Demo paper validation opened a simulated BTC long (paper only).",
            ),
            (
                DEMO_ALERT_DISCIPLINE,
                PaperAlertType.OVERTRADING_WARNING,
                PaperAlertSeverity.WARNING,
                None,
                None,
                None,
                "Demo discipline: slow down after two trades — protect the green day.",
            ),
        ]
        for alert_id, alert_type, severity, strat, run, trade, message in alerts:
            self._session.add(
                PaperValidationAlert(
                    id=alert_id,
                    organization_id=org_id,
                    user_id=user_id,
                    alert_type=alert_type,
                    severity=severity,
                    strategy_id=strat,
                    paper_validation_run_id=run,
                    paper_trade_id=trade,
                    message=message,
                    dedup_key=f"demo-seed:{alert_id}",
                    metadata_json={
                        **meta,
                        "source": PaperAlertSource.PAPER_VALIDATION_RUNTIME.value,
                    },
                    delivery_status=AlertDeliveryStatus.DISABLED,
                    delivery_channel=AlertDeliveryChannel.IN_APP,
                )
            )

    def _seed_lessons(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        pending = [
            (
                DEMO_LESSON_PENDING_1,
                "exiting_winners_early",
                "Exiting winners too early",
                "Demo: partial at 1R then flat before runner — missed follow-through.",
            ),
            (
                DEMO_LESSON_PENDING_2,
                "moving_stop_loss",
                "Moving stop losses",
                "Demo: stop was moved to breakeven before confirmation — review rule.",
            ),
            (
                DEMO_LESSON_PENDING_3,
                "overtrading",
                "Overtrading",
                "Demo: third trade attempt after two losses — wait for A+ setup.",
            ),
        ]
        for lesson_id, mistake, _title, text in pending:
            self._session.add(
                LessonCandidate(
                    id=lesson_id,
                    organization_id=org_id,
                    user_id=user_id,
                    source_type=LessonSourceType.JOURNAL.value,
                    lesson_text=text,
                    mistake_type=mistake,
                    severity="medium",
                    status=LessonCandidateStatus.PENDING_REVIEW.value,
                    related_strategy_id=DEMO_STRATEGY_BTC,
                    analysis_metadata={"demo_seed": True, "paper_only": True},
                )
            )
        accepted = [
            (
                DEMO_LESSON_ACCEPTED_1,
                "green_day_protection",
                "Accepted demo lesson: protect green days",
                "Synthetic reviewed demo — stop trading after daily target unless A+ setup.",
            ),
            (
                DEMO_LESSON_ACCEPTED_2,
                "one_loss_stop",
                "Accepted demo lesson: one-loss stop",
                "Synthetic reviewed demo — end session after first full stop loss.",
            ),
        ]
        for lesson_id, mistake, _title, text in accepted:
            self._session.add(
                LessonCandidate(
                    id=lesson_id,
                    organization_id=org_id,
                    user_id=user_id,
                    source_type=LessonSourceType.HUMAN_VS_SYSTEM.value,
                    lesson_text=text,
                    mistake_type=mistake,
                    severity="high",
                    status=LessonCandidateStatus.ACCEPTED.value,
                    related_strategy_id=DEMO_STRATEGY_ETH,
                    reviewer_notes=(
                        "Synthetic demo lesson — reviewed for portfolio walkthrough only."
                    ),
                    reviewed_at=now - timedelta(days=3),
                    analysis_metadata={
                        "demo_seed": True,
                        "paper_only": True,
                        "accepted_synthetic": True,
                    },
                )
            )

    def _seed_journals(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        entries = [
            (
                DEMO_JOURNAL_1,
                "BTCUSDT",
                "15m",
                TradeDirection.LONG,
                "Demo: waited for sweep + reclaim; took planned 1R partial.",
                TradeResult.WIN,
                Decimal("85"),
                ["calm", "patient"],
                [],
                "Good discipline — followed the card and sized correctly.",
            ),
            (
                DEMO_JOURNAL_2,
                "ETHUSDT",
                "1h",
                TradeDirection.LONG,
                "Demo: valid pullback entry; exited early before runner target.",
                TradeResult.WIN,
                Decimal("45"),
                ["anxious"],
                ["early_exit"],
                "Missed runner — review partial vs trail rules.",
            ),
            (
                DEMO_JOURNAL_3,
                "SOLUSDT",
                "15m",
                TradeDirection.LONG,
                "Demo: skipped marginal breakout — overtrading guard active.",
                TradeResult.BREAKEVEN,
                Decimal("0"),
                ["disciplined"],
                [],
                "Avoided overtrade — no entry after two prior trades.",
            ),
            (
                DEMO_JOURNAL_4,
                "BTCUSDT",
                "1h",
                TradeDirection.SHORT,
                "Demo: short invalidated quickly; stop respected at planned level.",
                TradeResult.LOSS,
                Decimal("-62"),
                ["accepting"],
                [],
                "Loss taken cleanly — stop not moved.",
            ),
        ]
        for entry in entries:
            jid, symbol, tf, direction, rationale, result, pnl, emotions, mistakes, lessons = entry
            self._session.add(
                TradeJournal(
                    id=jid,
                    organization_id=org_id,
                    user_id=user_id,
                    symbol=symbol,
                    timeframe=tf,
                    direction=direction,
                    entry_rationale=rationale,
                    exit_rationale="Synthetic demo exit notes — paper only.",
                    emotions=emotions,
                    mistakes=mistakes,
                    lessons=lessons,
                    result=result,
                    pnl=pnl,
                    tags=[DEMO_SEED_TAG, "paper-only"],
                )
            )

    def _seed_risk_settings(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        from app.services.audit_service import AuditService

        service = RiskSettingsService(self._session, AuditService(self._session))
        service.update(
            UserRiskSettingsUpdate(
                daily_loss_limit=Decimal("250"),
                daily_target=Decimal("400"),
                max_trades_per_day=4,
                max_risk_per_trade_percent=Decimal("0.75"),
                default_account_balance=Decimal("25000"),
                timezone="Europe/Berlin",
                green_day_protection_enabled=True,
                one_loss_stop_enabled=True,
                overtrading_guard_enabled=True,
                notes=f"[{DEMO_SEED_TAG}] Synthetic staging demo risk settings — paper only.",
            ),
            organization_id=org_id,
            user_id=user_id,
        )

    def _seed_market_watcher(self, org_id: uuid.UUID, user_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        observations = [
            (
                DEMO_OBSERVATION_1,
                "BTCUSDT",
                "15m",
                MarketWatcherObservationStatus.FRESH,
                DEMO_STRATEGY_BTC,
                DEMO_RUN_ACTIVE,
                "Demo watcher: liquidity sweep forming near session low (read-only).",
            ),
            (
                DEMO_OBSERVATION_2,
                "ETHUSDT",
                "1h",
                MarketWatcherObservationStatus.STALE,
                DEMO_STRATEGY_ETH,
                DEMO_RUN_PASSED,
                "Demo watcher: trend pullback holding — no execution (read-only).",
            ),
        ]
        for obs_id, symbol, tf, status, strat, run, notes in observations:
            self._session.add(
                MarketWatcherObservation(
                    id=obs_id,
                    organization_id=org_id,
                    symbol=symbol,
                    exchange="mock",
                    timeframe=tf,
                    observed_at=now - timedelta(minutes=20),
                    price=Decimal("65000") if "BTC" in symbol else Decimal("3200"),
                    status=status,
                    related_strategy_id=strat,
                    related_paper_validation_run_id=run,
                    notes=notes,
                    data_freshness="demo_seed",
                )
            )
