"""Run authorized quality measurement with existing provider settings and a finite cap.

No model/provider switch is accepted. Limits must identify the existing approved test
budget and verified, unexpired prices/context ceiling for the configured model; the
repository's placeholder prices cannot admit billed calls. Keep output/schema after
interruption; never automatically resume a possibly billed request.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from app.agent_capture.provider_evaluation import EvaluationCase, EvaluationLimits, evaluate
from app.core.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="Inspect safe bindings only; no network or database writes.",
    )
    parser.add_argument(
        "--limits-file",
        type=Path,
        help="Existing budget/verified price receipt JSON; see EvaluationLimits.",
    )
    parser.add_argument(
        "--spend-cap-usd",
        type=Decimal,
        help="Finite positive cap no larger than receipt remaining_budget_usd.",
    )
    parser.add_argument(
        "--database-url-env",
        default="AGENT_EVAL_DATABASE_URL",
        help="Name of securely bound disposable PostgreSQL URL variable.",
    )
    parser.add_argument(
        "--cases", type=Path, default=Path(__file__).parent / "fixtures/agent_capture_quality.json"
    )
    parser.add_argument(
        "--run-id",
        type=UUID,
        help="New explicit UUID; same output/schema cannot automatically repeat.",
    )
    parser.add_argument(
        "--output", type=Path, help="New private JSON report path; parent directory must exist."
    )
    args = parser.parse_args()
    settings = Settings()
    if args.preflight:
        blocked = (
            not settings.openai_api_key.strip()
            or settings.provider_mode.strip().lower() == "mock"
            or not os.getenv(args.database_url_env)
            or args.limits_file is None
            or not args.limits_file.exists()
        )
        print(
            json.dumps(
                {
                    "credential_binding": "OPENAI_API_KEY",
                    "credential_bound": bool(settings.openai_api_key.strip()),
                    "configured_model": settings.agent_reasoning_model,
                    "provider_mode": settings.provider_mode,
                    "database_binding": args.database_url_env,
                    "database_bound": bool(os.getenv(args.database_url_env)),
                    "limits_bound": args.limits_file is not None and args.limits_file.exists(),
                    "measured_cases": 0,
                    "provider_calls": 0,
                    "status": "blocked" if blocked else "bindings_present_not_verified",
                    "quality_metrics": None,
                    "latency_ms": None,
                    "usage": None,
                }
            )
        )
        return 2 if blocked else 0
    if any(
        value is None for value in (args.limits_file, args.spend_cap_usd, args.run_id, args.output)
    ):
        parser.error("Measurement requires --limits-file, --spend-cap-usd, --run-id and --output.")
    try:
        limits = EvaluationLimits.model_validate_json(args.limits_file.read_text())
        cases = [EvaluationCase.model_validate(case) for case in json.loads(args.cases.read_text())]
        if not cases or len({case.id for case in cases}) != len(cases):
            raise ValueError("Invalid case identities")
        database_url = os.environ.get(args.database_url_env, "")
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], check=True, text=True, capture_output=True
        ).stdout.strip()
        report = evaluate(
            settings,
            database_url,
            cases,
            limits,
            args.spend_cap_usd,
            args.run_id,
            args.output,
            revision,
        )
    except Exception as exc:
        from app.agent_capture.provider_evaluation import EvaluationBlockedError

        reason = str(exc) if isinstance(exc, EvaluationBlockedError) else type(exc).__name__
        print(
            json.dumps(
                {
                    "status": "blocked_or_error",
                    "reason": reason,
                    "measurement_state": "review_private_receipt_if_created",
                    "quality_metrics": None,
                    "latency_ms": None,
                    "usage": None,
                }
            )
        )
        return 2
    print(
        json.dumps(
            {
                "status": "complete" if report["complete"] else "incomplete",
                "report": str(args.output),
                "measured": report["measured"],
                "quality_accepted": report["quality_accepted"],
                "metrics": report["metrics"],
            }
        )
    )
    return 0 if report["complete"] and report["measured"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
