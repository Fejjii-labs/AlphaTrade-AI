"""Explicit confirmation policy for chat-initiated state mutations (Slice 40C)."""

from __future__ import annotations

import re


def is_question_message(message: str) -> bool:
    """True when the user is asking rather than commanding."""
    lowered = message.strip().lower()
    if lowered.endswith("?"):
        return True
    question_starts = (
        "should i ",
        "can i ",
        "could i ",
        "would you ",
        "do you think ",
        "what if ",
        "is it ok ",
        "is it okay ",
        "am i ",
    )
    return any(lowered.startswith(prefix) for prefix in question_starts)


def has_explicit_confirmation(message: str, *, confirm_arg: bool | None = None) -> bool:
    """True when the user supplied an explicit confirmation token."""
    if confirm_arg is True:
        return True
    lowered = message.lower()
    return (
        "confirm=true" in lowered
        or re.search(r"\b(i confirm|confirm action|yes,?\s*confirm)\b", lowered) is not None
    )


def contains_quoted_or_retrieved_instruction(message: str) -> bool:
    """True when confirmation could be coming from a quote or retrieved block."""

    stripped = message.strip()
    if not stripped:
        return False
    if "```" in stripped:
        return True
    for line in stripped.splitlines():
        candidate = line.strip()
        lowered = candidate.lower()
        if candidate.startswith(">") or lowered.startswith("system:"):
            return True
        if lowered.startswith("retrieved:") or lowered.startswith("[retrieved"):
            return True
    return False


def confirmation_authorizes_mutation(message: str) -> bool:
    """Strategy confirmation requires an unquoted confirmation-only message."""

    if is_question_message(message):
        return False
    if contains_quoted_or_retrieved_instruction(message):
        return False
    return is_confirmation_only_message(message) or confirmed_proposal_id(message) is not None


def rejection_authorizes_mutation(message: str) -> bool:
    """Strategy rejection requires an unquoted rejection-only message."""

    if is_question_message(message):
        return False
    if contains_quoted_or_retrieved_instruction(message):
        return False
    return is_rejection_only_message(message) or rejected_proposal_id(message) is not None


_LEADING_CONFIRMATION = re.compile(
    r"^(?:i confirm|yes,?\s*(?:i\s+)?confirm|confirm action|confirm=true)\b",
    re.IGNORECASE,
)
_TRAILING_CONFIRMATION = re.compile(
    r"(?:^|[.!;\n])\s*(?:i confirm|confirm action|confirm=true)[.!]?\s*$",
    re.IGNORECASE,
)


def has_standalone_confirmation(message: str) -> bool:
    """True when the confirmation opens the message or is its own closing sentence.

    A phrase buried mid-text (e.g. pasted prose that happens to contain
    "i confirm") does not count.
    """
    stripped = message.strip()
    return (
        _LEADING_CONFIRMATION.match(stripped) is not None
        or _TRAILING_CONFIRMATION.search(stripped) is not None
    )


def mutation_allowed(message: str, *, confirm_arg: bool | None = None) -> bool:
    """State-changing chat actions require an unquoted, standalone explicit confirmation.

    Questions, quoted/retrieved blocks and buried "i confirm" substrings never
    authorize a mutation (legacy chat path, audit M18).
    """
    if is_question_message(message):
        return False
    if contains_quoted_or_retrieved_instruction(message):
        return False
    if confirm_arg is True:
        return True
    return has_standalone_confirmation(message)


_CONFIRMATION_ONLY = re.compile(
    r"^(i confirm|yes,?\s*confirm|confirm action)(\s+(this |the )?(proposal|draft))?\.?$",
    re.IGNORECASE,
)

_CONFIRM_PROPOSAL_ID = re.compile(
    r"^(i confirm|yes,?\s*confirm|confirm action)\s+(this |the )?proposal\s+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.?$",
    re.IGNORECASE,
)

_REJECTION_ONLY = re.compile(
    r"^(i reject|reject proposal|reject this draft)(\s+(this |the )?(proposal|draft))?\.?$",
    re.IGNORECASE,
)

_REJECT_PROPOSAL_ID = re.compile(
    r"^(i reject|reject proposal|reject this draft)\s+(this |the )?proposal\s+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.?$",
    re.IGNORECASE,
)


def is_confirmation_only_message(message: str) -> bool:
    """True when the entire message is an explicit confirmation, not a buried injection."""
    return _CONFIRMATION_ONLY.match(message.strip()) is not None


def confirmed_proposal_id(message: str) -> str | None:
    """Return a proposal UUID only when the message is an explicit confirm of that id."""
    match = _CONFIRM_PROPOSAL_ID.match(message.strip())
    if match is None:
        return None
    return match.group(3)


def is_rejection_only_message(message: str) -> bool:
    """True when the entire message is an explicit rejection, not a buried injection."""
    return _REJECTION_ONLY.match(message.strip()) is not None


def rejected_proposal_id(message: str) -> str | None:
    """Return a proposal UUID only when the message is an explicit reject of that id."""
    match = _REJECT_PROPOSAL_ID.match(message.strip())
    if match is None:
        return None
    return match.group(3)


def rejection_allowed(message: str, *, confirm_arg: bool | None = None) -> bool:
    """Rejecting a draft requires an explicit reject/confirm token and is never a question."""
    if is_question_message(message):
        return False
    if confirm_arg is True:
        return True
    lowered = message.lower()
    return has_explicit_confirmation(message, confirm_arg=confirm_arg) or bool(
        re.search(r"\b(i reject|reject proposal|reject this draft)\b", lowered)
    )
