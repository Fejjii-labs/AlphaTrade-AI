"""One-off staging job: create, compile and approve the live D-line (d_line/v2) versions
and add them to the autonomy strategy list. Dry run by default (everything rolled back).

Nested/SFP versions were approved through the app, not by a migration or seed, so this
uses the same services: create_template -> compile_version -> approve_version, then
AutonomousTradingSettingsService.update(strategy_ids=existing + D-line).

Run from backend/ against staging only:

    uv run --frozen python ../scripts/approve_dline_demo_staging.py \
        --organization-id <org uuid> --user-id <user uuid>            # dry run
    uv run --frozen python ../scripts/approve_dline_demo_staging.py \
        --organization-id <org uuid> --user-id <user uuid> --apply    # commit

Refuses when real trading is on or the environment is not staging. Prints no secrets,
URLs or connection strings. D-line trades only with GOVERNED_DEMO_DATA_MODE_ENABLED,
RISK_MODEL_V2_ENABLED and GOVERNED_DEMO_PARTIAL_EXITS_ENABLED (see the report).
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid

SYMBOLS = ("BTCUSDT", "ETHUSDT", "HYPEUSDT", "TAOUSDT", "ZECUSDT")
TIMEFRAMES = ("15m", "30m", "1h")
DIRECTIONS = ("long", "short")
MAX_STRATEGY_IDS = 50


def out(tag: str, payload: object) -> None:
    print(f"DLINE|{tag}|{json.dumps(payload, default=str)[:2000]}", flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--organization-id", type=uuid.UUID, required=True)
    parser.add_argument("--user-id", type=uuid.UUID, required=True)
    parser.add_argument("--symbols", default=",".join(SYMBOLS))
    parser.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    parser.add_argument("--directions", default=",".join(DIRECTIONS))
    parser.add_argument("--apply", action="store_true", help="commit (default: dry run)")
    args = parser.parse_args(argv)

    from app.core.config import get_settings
    from app.db.session import get_session_factory
    from app.schemas.autonomous_trading_settings import AutonomousTradingSettingsUpdate
    from app.schemas.common import Timeframe, TradeDirection
    from app.services.audit_service import AuditService
    from app.services.autonomous_trading_settings_service import (
        AutonomousTradingSettingsService,
    )
    from app.services.canonical_strategy_evaluation import resolve_executable_strategy_policy
    from app.services.compiled_setup_service import CompiledSetupService
    from app.strategy_brain.dline.live import DlineLiveSpec
    from app.strategy_brain.service import create_template

    settings = get_settings()
    out(
        "flags",
        {
            "apply": args.apply,
            "environment": settings.environment,
            "real_trading": settings.enable_real_trading or settings.real_trading_enabled,
            "demo_data_mode": settings.governed_demo_data_mode_enabled,
            "risk_model_v2": settings.risk_model_v2_enabled,
            "demo_partial_exits": settings.governed_demo_partial_exits_enabled,
            "watcher_max_scopes_per_cycle": settings.watcher_paper_max_scopes_per_cycle,
        },
    )
    if (
        settings.enable_real_trading
        or settings.real_trading_enabled
        or settings.environment != "staging"
    ):
        out("ABORT", "staging with real trading off only")
        return 1
    org, user = args.organization_id, args.user_id
    session = get_session_factory()()
    try:
        compiled = CompiledSetupService(session)
        created: list[uuid.UUID] = []
        for symbol in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
            for timeframe in [t.strip() for t in args.timeframes.split(",") if t.strip()]:
                for direction in [d.strip() for d in args.directions.split(",") if d.strip()]:
                    spec = DlineLiveSpec(
                        symbol=symbol,
                        trigger_timeframe=Timeframe(timeframe),
                        direction=TradeDirection(direction),
                    )
                    template = create_template(
                        session, organization_id=org, user_id=user, spec=spec
                    )
                    version_id = template["version_id"]
                    result = compiled.compile_version(version_id, organization_id=org, user_id=user)
                    if result.status != "executable":
                        raise RuntimeError(f"{symbol} {timeframe} {direction}: {result.status}")
                    compiled.approve_version(
                        version_id, organization_id=org, user_id=user, confirm_message="I confirm"
                    )
                    policy = resolve_executable_strategy_policy(
                        session, organization_id=org, strategy_version_id=version_id
                    )
                    created.append(template["strategy_id"])
                    out(
                        "approved",
                        {
                            "symbol": symbol,
                            "timeframe": timeframe,
                            "direction": direction,
                            "strategy_id": template["strategy_id"],
                            "version_id": version_id,
                            "lifecycle": policy.lifecycle_state.value,
                        },
                    )
        audit = AuditService(session)
        autonomy = AutonomousTradingSettingsService(session, audit)
        before = autonomy.get(organization_id=org, user_id=user)
        ids = list(dict.fromkeys([*before.strategy_ids, *created]))
        out(
            "strategy_ids",
            {"before": len(before.strategy_ids), "dline": len(created), "after": len(ids)},
        )
        if len(ids) > MAX_STRATEGY_IDS:
            out("ABORT", f"more than {MAX_STRATEGY_IDS} strategy ids; use scope all_eligible")
            session.rollback()
            return 1
        view = autonomy.update(
            AutonomousTradingSettingsUpdate(strategy_ids=ids, strategy_scope="selected"),
            organization_id=org,
            user_id=user,
        )
        selected = {str(i) for i in created}
        approved = [
            o for o in view.strategy_options if str(o.strategy_id) in selected and o.approved
        ]
        if len(approved) != len(created):
            raise RuntimeError(f"only {len(approved)} of {len(created)} D-line listed approved")
        out("autonomy", {"scope": view.strategy_scope, "strategy_ids": len(view.strategy_ids)})
        if len(ids) > settings.watcher_paper_max_scopes_per_cycle:
            out(
                "WARN",
                "WATCHER_PAPER_MAX_SCOPES_PER_CYCLE is below the approved strategy count; "
                "later strategies (D-line) are not scanned until it is raised",
            )
    except BaseException as exc:
        session.rollback()
        out("ERROR", type(exc).__name__ + ": " + str(exc)[:500])
        return 1
    if args.apply:
        session.commit()
        out("COMMITTED", True)
    else:
        session.rollback()
        out("DRY_RUN_ROLLED_BACK", True)
    session.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
