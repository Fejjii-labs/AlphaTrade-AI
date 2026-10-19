"""Stable refusal codes for the grade-based risk policy. Fail closed."""

from __future__ import annotations


class RiskPolicyRejected(ValueError):  # noqa: N818 — refusal, not a generic error
    """A profile, change, or trade was refused. ``code`` is the stable reason."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)
