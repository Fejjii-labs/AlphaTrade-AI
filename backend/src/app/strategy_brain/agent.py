"""Grounded read tool for the existing interactive Agent. No market acquisition."""

import json
import re
from itertools import zip_longest
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.errors import NotFoundError
from app.interactive_agent.contracts import ArtifactKind, ConnectionRef, ProvenanceSource
from app.interactive_agent.parsing import extract_symbol, extract_timeframe
from app.strategy_brain.service import details, overview


def read_brain(
    session: Session,
    *,
    organization_id: UUID,
    message: str,
    symbol: str | None = None,
    user_id: UUID | None = None,
) -> tuple[str, list[ConnectionRef], list[str]]:
    query_symbol = extract_symbol(message) or symbol
    timeframe = extract_timeframe(message)
    requested_families = [
        name
        for name, pattern in (("sfp", r"\bsfp\b|swing failure"), ("nested", r"\bnested\b"))
        if re.search(pattern, message, re.I)
    ]
    family = requested_families[0] if len(requested_families) == 1 else None
    data = overview(session, organization_id=organization_id, symbol=query_symbol, user_id=user_id)
    selected = re.search(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", message, re.I)
    if selected:
        try:
            data["setups"] = [
                details(session, organization_id=organization_id, setup_id=UUID(selected.group()))
            ]
        except NotFoundError:
            data["setups"] = []
    else:
        data["setups"] = [
            setup
            for setup in data["setups"]
            if (not timeframe or setup.get("timeframe") == timeframe)
            and (not family or (setup.get("family") == "sfp") == (family == "sfp"))
        ]
        if re.search(r"\b(?:current|latest|forming|state|status)\b", message, re.I):
            # Overview is newest first. Keep independent strategy/timeframe scopes;
            # older episodes cannot crowd the current facts out of the reply budget.
            seen = set()
            latest = []
            for setup in data["setups"]:
                key = (setup["strategy_version_id"], setup.get("timeframe"))
                if key not in seen:
                    latest.append(setup)
                    seen.add(key)
            data["setups"] = latest
    lines = [
        "Watcher configuration: "
        + (", ".join(data["watched_symbols"]) or "no enabled symbols")
        + f" (revision {data['watchlist_revision']})."
    ]
    refs = []
    definitions = []
    for strategy in data["strategies"]:
        spec = strategy["spec"]
        is_sfp = spec["kind"] == "swing_failure_pattern/v1"
        if (
            (family and is_sfp != (family == "sfp"))
            or (query_symbol and spec.get("symbol") != query_symbol.upper())
            or (timeframe and spec.get("trigger_timeframe") != timeframe)
        ):
            continue
        definitions.append(strategy)
    # Alternate families when both are requested, including when there are no setup rows.
    groups = [
        [
            item
            for item in definitions
            if (item["spec"]["kind"] == "swing_failure_pattern/v1") == sfp
        ]
        for sfp in (False, True)
    ]
    definitions = [item for pair in zip_longest(*groups) for item in pair if item]
    definition_lines = []
    for strategy in definitions[:8]:
        spec = strategy["spec"]
        definition_lines.append(
            f"Selected strategy {strategy['name']}: version {strategy['version']} "
            f"({strategy['version_id']}); {spec.get('symbol')} {spec.get('direction')} "
            f"{spec.get('trigger_timeframe')}; lifecycle={strategy['status']} "
            f"(canonical event {strategy['lifecycle_event_id'] or 'unavailable'}); "
            f"research_validation={strategy['research_validation']}; "
            f"enabled={strategy['enabled']}; "
            f"execution={strategy['execution_permission']}. "
            f"Stored rules: {json.dumps(strategy['rules'], separators=(',', ':'))}. "
            f"Stored parameters: {json.dumps(spec.get('parameters', {}), separators=(',', ':'))}. "
            f"Structured rules: {json.dumps(strategy['structured_rules'], separators=(',', ':'))}."
        )
        refs.append(
            ConnectionRef(
                artifact_kind=ArtifactKind.STRATEGY,
                record_id=strategy["strategy_id"],
                title=strategy["name"][:200],
                relation="selected version rules and canonical lifecycle",
                provenance=ProvenanceSource.USER_SUPPLIED,
            )
        )
    lines.extend(_bounded_lines(definition_lines, 4000))
    if user_id is not None and re.search(r"alert|notification|telegram|delivery", message, re.I):
        from app.strategy_brain.notification_diagnostics import read_notification_diagnostics

        diagnostics = read_notification_diagnostics(
            session, organization_id=organization_id, user_id=user_id, strategies=definitions
        )
        lines.append(
            f"Notification receipt window {diagnostics['window_start']} to "
            f"{diagnostics['window_end']}; at most {diagnostics['per_stage_row_limit']} "
            "rows per stage and version."
        )
        for scope in diagnostics["scopes"]:
            latest = scope["deliveries"][0] if scope["deliveries"] else None
            lines.append(
                f"Notification diagnostics {scope['family']} {scope['strategy_version_id']} "
                f"{scope['symbol']} {scope['timeframe']}: "
                f"scans={scope['scan_status_counts']}, reasons={scope['scan_reasons']}; "
                f"acquisition={scope['acquisition_status_counts']}, "
                f"reasons={scope['acquisition_reasons']}; "
                f"evaluation={scope['evaluation_status_counts']}, "
                f"reasons={scope['evaluation_reasons']}; "
                f"detections={scope['detection_states']}, reasons={scope['detection_reasons']}; "
                f"candidate_links={scope['candidate_links']}; "
                f"decision_reasons={scope['decision_reasons']}; "
                f"delivery={scope['delivery_states']}; latest receipt={latest}; "
                f"missing={scope['missing_evidence']}; truncated={scope['truncated']}."
            )
        data["limitations"].extend(diagnostics["limitations"])
        lines.extend(diagnostics["limitations"])
    if len(definitions) > 8:
        data["limitations"].append("Strategy context includes at most eight selected definitions.")
    if not definitions:
        lines.append("No selected strategy definition is available for this query.")
    if not data["setups"]:
        lines.append(
            (
                "No stored SFP setup exists for this query. "
                if family == "sfp"
                else "No stored Nested Continuation setup exists for this query. "
                if family == "nested"
                else "No stored Nested Continuation setup exists for this query. "
                "No stored SFP setup exists for this query. "
            )
            + "Current market conditions are unknown."
        )
    setup_lines = []
    availability = sorted({setup["freshness"] for setup in data["setups"]})
    missing = sorted(
        {
            f"{name}={state}"
            for setup in data["setups"]
            for name, state in setup.get("evidence", {}).items()
            if state != "AVAILABLE"
        }
    )
    if availability:
        lines.append(
            "Stored setup evidence: "
            + "; ".join([*(f"freshness={state}" for state in availability), *missing])
        )
    for setup in data["setups"][:8]:
        setup_lines.append(
            f"Setup {setup['setup_id']} on {setup.get('instrument')}: {setup['state']}, "
            f"{setup.get('condition', setup.get('stage'))}, {setup.get('direction')}; "
            f"strategy version {setup['strategy_version_id']}; "
            f"observed {setup['observed_at']}; expires {setup['expires_at']}; "
            f"freshness {setup['freshness']}. "
            f"Reasons: {', '.join(setup.get('reason_codes', []))}; "
            f"risk {setup.get('risk_state', 'not_evaluated')}. "
            f"Evidence {setup.get('evidence_reference')}; missing/unsupported: "
            + ", ".join(
                f"{name}={state}"
                for name, state in setup.get("evidence", {}).items()
                if state != "AVAILABLE"
            )
            + f". Candidate {setup['candidate_id'] or 'none'}; "
            + f"journal {setup['journal'] or 'none'}; decision {setup['decision_id'] or 'none'}. "
            "These are stored observations, not current market claims."
        )
        if setup.get("family") == "sfp":
            level = setup["sweep"]["reference_level"]
            setup_lines.append(
                f"Swept {level['kind']} {level['price']}; level {level['level_id']}; "
                f"evidence time {setup['evidence_at']}; "
                f"reclaim {setup.get('reclaim_observation_id')}; "
                "quality components "
                + ", ".join(
                    f"{name}={component['availability']}:{component['value']} {component['unit']}"
                    for name, component in setup["quality"].items()
                )
                + "; "
                "target space is advisory, not an executable target."
            )
        refs.append(
            ConnectionRef(
                artifact_kind=ArtifactKind.OBSERVATION,
                record_id=setup["setup_id"],
                title=(
                    f"{setup.get('family', 'Nested')} "
                    f"{setup.get('condition', setup.get('stage'))} {setup['state']}"
                ),
                relation="stored setup and history",
                provenance=ProvenanceSource.WATCHER_OBSERVED,
            )
        )
    lines.extend(_bounded_lines(setup_lines, 1500))
    lines.append(
        "Lifecycle approval is not research validation, setup confirmation "
        "or execution eligibility. "
        "A confirmed Candidate, fresh evidence, deterministic risk approval and an authorized "
        "plan are required. SFP execution remains restricted: no governed SFP plan path. "
        "Insufficient history for win rate or expectancy; no rule was changed."
    )
    return "\n".join(lines), refs, data["limitations"]


def _bounded_lines(lines: list[str], budget: int) -> list[str]:
    """Allocate equal space per record; never silently imply omitted rules are absent."""
    if not lines:
        return []
    per_record = budget // len(lines)
    suffix = " [Additional stored details omitted.]"
    return [
        line if len(line) <= per_record else line[: per_record - len(suffix)] + suffix
        for line in lines
    ]
