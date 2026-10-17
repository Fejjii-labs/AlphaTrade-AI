"""Export/check the pure read contract without modifying shared HTTP artifacts."""

import argparse
import json
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field, TypeAdapter

from app.experiments.native_lineage import NativeEntryBinding, NativeExitLineage
from app.experiments.outcome_contract import ExperimentPerformance, NativeOutcome

DESTINATION = (
    Path(__file__).resolve().parents[2] / "docs/contracts/blofin_experiment_outcomes.v1.schema.json"
)


def schema_bytes() -> bytes:
    contract: TypeAdapter[Any] = TypeAdapter(
        Annotated[
            NativeEntryBinding | NativeExitLineage | NativeOutcome | ExperimentPerformance,
            Field(discriminator="contract_version"),
        ]
    )
    return (
        json.dumps(contract.json_schema(mode="serialization"), indent=2, sort_keys=True) + "\n"
    ).encode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = schema_bytes()
    if args.check:
        if not DESTINATION.exists() or DESTINATION.read_bytes() != expected:
            print("BloFin experiment outcome contract drift")
            return 1
        print("BloFin experiment outcome contract matches")
        return 0
    DESTINATION.write_bytes(expected)
    print("Exported " + DESTINATION.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
