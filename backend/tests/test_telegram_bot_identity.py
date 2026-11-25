"""Bot identity and persisted-candidate discussion. No network and no trading."""

from __future__ import annotations

from app.telegram_activation.identity import bot_identity_mismatch, token_bot_user_id
from app.telegram_paper_agent.contracts import DiscussionIntent
from app.telegram_security.contracts import TelegramInboundUpdate
from tests.support.telegram_paper_agent import assert_no_internal_ids, enabled_paper_agent_world
from tests.support.telegram_security import message_identity


def test_numeric_bot_id_must_match_token_prefix() -> None:
    token = "123456789:AAHtestTokenValue"
    assert token_bot_user_id(token) == "123456789"
    assert bot_identity_mismatch(bot_id="123456789", token=token) is False
    assert bot_identity_mismatch(bot_id="999", token=token) is True
    assert bot_identity_mismatch(bot_id="bot-100", token=token) is False
    assert token_bot_user_id("not-a-token") is None


def test_persisted_candidate_discussion_does_not_invent_assessment() -> None:
    world = enabled_paper_agent_world()
    result = world.agent.handle_inbound_message(
        identity=message_identity(update_id=91, message_id="persisted"),
        inbound=TelegramInboundUpdate(update_type="message", body_size=32),
        text="Explain the candidate evidence and risk",
        candidate=world.candidate,
    )
    assert result.intent is DiscussionIntent.EXPLAIN_EVIDENCE
    assert result.reply_outbox is not None
    text = result.reply_outbox.text
    assert_no_internal_ids(text)
    assert text.startswith("BTCUSDT short setup (15m)\n")
    # The assessment is not on this path, so no setup-check result is invented.
    assert "Setup check" not in text
    assert "The full setup check isn't available here." in text
    assert result.executed is False
    assert result.candidate_minted is False
