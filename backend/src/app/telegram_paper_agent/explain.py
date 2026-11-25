"""Read-only plain-language explanations of canonical records. LLM is not used.

Replies name the coin, direction, timeframe and status. Internal ids, hashes and
enum names stay in the stored records and are never rendered.
"""

from __future__ import annotations

from datetime import datetime

from app.candidate_alerts.content import ALERT_TIMEZONE, format_candidate_alert_text
from app.candidate_alerts.contracts import CandidateAlertContent
from app.signal_fusion.assessment import SetupAssessment
from app.signal_fusion.candidate import Candidate
from app.signal_fusion.evidence_window import CanonicalEvidenceWindowV1
from app.telegram_paper_agent.content import guard_paper_text, humanize
from app.telegram_paper_agent.contracts import StrategyDraftView

_BULLET = "• "


def _symbol(instrument_id: str) -> str:
    """``binance:usdm_futures:perpetual:BTCUSDT`` -> ``BTCUSDT``."""
    return instrument_id.rsplit(":", 1)[-1]


def _time(at: datetime) -> str:
    return at.astimezone(ALERT_TIMEZONE).strftime("%H:%M %Z")


def _title(candidate: Candidate) -> str:
    return (
        f"{_symbol(candidate.evidence_instrument)} {candidate.direction.value} setup "
        f"({candidate.timeframe.value})"
    )


def _candidate_text(candidate: Candidate, closing: str) -> str:
    return guard_paper_text(
        "\n".join(
            (
                _title(candidate),
                f"{_BULLET}Status: {humanize(candidate.state.value)}",
                f"{_BULLET}Valid until: {_time(candidate.valid_until)}",
                closing,
            )
        )
    )


def explain_persisted_candidate(*, candidate: Candidate) -> str:
    """Explain a stored Candidate when the assessment body is not on this path."""
    return _candidate_text(candidate, "The full setup check isn't available here.")


def explain_candidate(
    *,
    candidate: Candidate,
    content: CandidateAlertContent | None,
) -> str:
    if content is not None:
        return format_candidate_alert_text(content)
    return _candidate_text(
        candidate, "Ask me about the evidence, the risk or the strategy behind it."
    )


def explain_evidence(*, window: CanonicalEvidenceWindowV1, assessment: SetupAssessment) -> str:
    passed = sum(1 for rule in assessment.rule_results if rule.passed)
    failed = [humanize(rule.rule_id) for rule in assessment.rule_results if not rule.passed]
    lines = [
        f"Evidence for {_symbol(window.evidence_instrument)} ({window.timeframe.value})",
        f"{_BULLET}Setup check: {humanize(assessment.state.value)}",
        f"{_BULLET}Checks passed: {passed} of {len(assessment.rule_results)}",
    ]
    if failed:
        lines.append(f"{_BULLET}Not met: {', '.join(failed)}")
    lines.append("Missing or out-of-date data never counts as confirmation.")
    return guard_paper_text("\n".join(lines))


def explain_risk(*, candidate: Candidate, assessment: SetupAssessment) -> str:
    reasons = ", ".join(humanize(code.value) for code in assessment.reason_codes) or "none"
    return guard_paper_text(
        "\n".join(
            (
                f"Risk for the {_title(candidate)}",
                f"{_BULLET}Setup check: {humanize(assessment.state.value)}",
                f"{_BULLET}Reasons: {reasons}",
                "Risk limits and the kill switch always apply. Telegram can't override them.",
            )
        )
    )


def explain_strategy(*, candidate: Candidate, draft: StrategyDraftView | None) -> str:
    lines = [f"Strategy behind the {_title(candidate)}"]
    if draft is not None:
        lines.append(draft.summary)
    lines.append("Telegram can't approve or change a strategy.")
    return guard_paper_text("\n".join(lines))


def explain_market_context(*, window: CanonicalEvidenceWindowV1) -> str:
    return guard_paper_text(
        "\n".join(
            (
                f"Market context for {_symbol(window.evidence_instrument)} "
                f"({window.timeframe.value})",
                f"{_BULLET}Data window: {_time(window.interval.start)} to "
                f"{_time(window.interval.end)}",
                f"{_BULLET}Source: {humanize(window.evidence_venue.value)} "
                f"{humanize(window.evidence_market.value).lower()}",
                "This is the data the setup check used, not a live price.",
            )
        )
    )
