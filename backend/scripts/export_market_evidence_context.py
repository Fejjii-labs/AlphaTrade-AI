"""Publish/check the serialization schema for the stable optional context contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.market_contracts.context import MarketEvidenceContext


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    target = (
        Path(__file__).resolve().parents[2]
        / "docs/contracts/market_evidence_context.v1.schema.json"
    )
    schema = MarketEvidenceContext.model_json_schema(mode="serialization")
    schema["$id"] = "urn:alphatrade:public-market-context:v1"
    content = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not target.exists() or target.read_text() != content:
            print("MarketEvidenceContext v1 schema drift; rerun this script without --check.")
            return 1
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
