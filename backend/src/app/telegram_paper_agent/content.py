"""Deterministic Telegram outbox text. No profitability claims. No LLM setup truth."""

from __future__ import annotations

import re

from app.telegram_paper_agent.contracts import (
    MAX_TEXT_BYTES,
    JournalOutcomeView,
    LearningSummaryView,
    PaperNotificationKind,
    PaperTradeStatusView,
    WatcherScanNotice,
)

_PROFIT_CLAIMS = ("guaranteed", "will win", "autonomous profit")
_BULLET = "\u2022 "
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_HEX_HASH = re.compile(r"\b[0-9a-f]{32,}\b", re.I)
_BLOCKED_REASON_TEXT = {
    "stale_evidence": "market data is out of date",
    "provider_outage": "the market data provider is unavailable",
    "wrong_source": "market data came from an unexpected source",
    "candidate_creation_failed": "the setup could not be saved",
}
_WORDS = {"pnl": "P&L", "mfe": "MFE", "mae": "MAE", "true": "yes", "false": "no"}


def humanize(code: str) -> str:
    """``stale_evidence`` -> ``Stale evidence``. End-user text never shows raw codes."""
    words = [_WORDS.get(w.lower(), w) for w in re.split(r"[_\s]+", code.strip()) if w]
    text = " ".join(words)
    return text[:1].upper() + text[1:] if text else "n/a"


def _is_internal(value: str) -> bool:
    return bool(_UUID.search(value) or _HEX_HASH.search(value))


def format_watcher_blocked_text(notice: WatcherScanNotice) -> str:
    reason = _BLOCKED_REASON_TEXT.get(notice.reason_code) or humanize(notice.reason_code).lower()
    text = "\n".join(
        (
            f"{notice.symbol} scan paused",
            f"{_BULLET}Reason: {reason}",
            f"{_BULLET}No setup was created and no trade was placed.",
        )
    )
    return guard_paper_text(text)


def format_confirmed_setup_footer() -> str:
    return guard_paper_text("Reply to this message to ask about this setup.")


def format_journal_outcome_text(view: JournalOutcomeView) -> str:
    lines = [f"{view.symbol} paper trade {humanize(view.status).lower()}"]
    if view.result:
        lines.append(f"{_BULLET}Result: {humanize(view.result)}")
    if view.net_pnl is not None:
        lines.append(f"{_BULLET}Net P&L: {view.net_pnl}")
    lines.append(view.summary)
    return guard_paper_text("\n".join(lines))


def format_paper_status_text(view: PaperTradeStatusView) -> str:
    text = "\n".join(
        (
            "Paper trading status",
            f"{_BULLET}Open positions: {view.open_positions}",
            f"{_BULLET}Open orders: {view.open_orders}",
            f"{_BULLET}Live trading: {'on' if view.real_trading_enabled else 'off'}",
            view.summary,
        )
    )
    return guard_paper_text(text)


def _fact_line(line: str) -> str | None:
    sep = ":" if ":" in line else "="
    key, _, value = line.partition(sep)
    if key.endswith("_hash") or _is_internal(line):
        return None
    if key == "refinement":
        category = value.split(":", 1)[0]
        return f"{_BULLET}Refinement idea: {humanize(category)} (not active)"
    if not value:
        return f"{_BULLET}{humanize(key)}"
    return f"{_BULLET}{humanize(key)}: {humanize(value)}"


def format_learning_summary_text(view: LearningSummaryView) -> str:
    lines = ["Learning summary"]
    for label, value in (
        ("Setup quality", view.setup_quality),
        ("Execution quality", view.execution_quality),
        ("Trader behavior", view.trader_behavior),
    ):
        if value:
            lines.append(f"{_BULLET}{label}: {humanize(value)}")
    facts = [fact for fact in (_fact_line(line) for line in view.fact_lines) if fact]
    lines.extend(facts or ["No learning results yet."])
    # Banners read "CODE — sentence"; only the sentence is shown.
    banner = view.banner.split(" \u2014 ", 1)[-1].strip()
    lines.append(banner[:1].upper() + banner[1:] + ("" if banner.endswith(".") else "."))
    return guard_paper_text("\n".join(lines))


def notification_kind_label(kind: PaperNotificationKind) -> str:
    return kind.value


def guard_paper_text(text: str) -> str:
    lowered = text.lower()
    for claim in _PROFIT_CLAIMS:
        if claim in lowered:
            raise ValueError("Paper Telegram text must not claim guaranteed returns.")
    if len(text) > MAX_TEXT_BYTES:
        raise ValueError("Paper Telegram text exceeds outbox bounds.")
    return text
