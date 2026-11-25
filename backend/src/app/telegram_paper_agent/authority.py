"""Refusals for authorities Telegram must never hold."""

from __future__ import annotations

from app.telegram_paper_agent.contracts import DiscussionIntent

_REFUSALS: dict[DiscussionIntent, str] = {
    DiscussionIntent.REQUEST_EXECUTE: "Telegram cannot place an order.",
    DiscussionIntent.REQUEST_APPROVE_STRATEGY: (
        "Telegram cannot approve or activate a strategy. That is a separate, explicit step."
    ),
    DiscussionIntent.REQUEST_OVERRIDE_ASSESSMENT: (
        "Telegram cannot change whether a setup is confirmed. The setup check decides that."
    ),
    DiscussionIntent.REQUEST_OVERRIDE_RISK: (
        "Telegram cannot override risk limits. A risk block is final."
    ),
    DiscussionIntent.REQUEST_MINT_CANDIDATE: (
        "Telegram cannot create a setup. Setups come only from confirmed scans."
    ),
    DiscussionIntent.REQUEST_ENABLE_LIVE: (
        "Telegram cannot enable live trading. Live trading stays off."
    ),
}


def refusal_for(intent: DiscussionIntent) -> str | None:
    return _REFUSALS.get(intent)
