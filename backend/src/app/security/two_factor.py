"""Two-factor primitives: RFC 6238 TOTP, recovery codes, secret encryption, QR.

TOTP uses the standard library (``hmac``/``hashlib``) only. The TOTP secret is
stored encrypted with AES-256-GCM (``cryptography``); the key comes from
``TWO_FACTOR_ENCRYPTION_KEY`` or, when that is unset, is derived from
``JWT_SECRET`` with HKDF under a two-factor-only label. Recovery codes are
stored as keyed HMAC-SHA256 digests, never in plain text.

Nothing in this module logs; callers must never log codes, secrets or digests.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import io
import secrets
import struct
import time
import uuid
from urllib.parse import quote, urlencode

import segno
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import Settings

TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
TOTP_ALLOWED_DRIFT_STEPS = 1
_SECRET_BYTES = 20  # 160-bit secret, the RFC 4226 recommendation.
_RECOVERY_CODE_GROUP = 5
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/i/l look-alikes
_CIPHERTEXT_VERSION = "v1"
_ENCRYPTION_INFO = b"alphatrade/two-factor/secret-encryption/v1"
_RECOVERY_INFO = b"alphatrade/two-factor/recovery-code-hmac/v1"


class TwoFactorKeyError(ValueError):
    """The configured two-factor key is malformed."""


class TwoFactorSecretError(ValueError):
    """A stored secret could not be decrypted (wrong key or tampered row)."""


# --------------------------------------------------------------------------- #
# TOTP (RFC 6238 over RFC 4226 HOTP, SHA-1, 6 digits, 30 s)
# --------------------------------------------------------------------------- #


def generate_totp_secret() -> str:
    """Return a new base32 secret (no padding), as authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def _secret_bytes(secret_b32: str) -> bytes:
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded, casefold=True)


def _hotp(key: bytes, counter: int) -> str:
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**TOTP_DIGITS).zfill(TOTP_DIGITS)


def current_time_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // TOTP_PERIOD_SECONDS)


def totp_code(secret_b32: str, *, step: int) -> str:
    """The code for one time step (used by tests and by verification)."""
    return _hotp(_secret_bytes(secret_b32), step)


def match_totp_step(secret_b32: str, code: str, *, now: float | None = None) -> int | None:
    """Return the matching time step within +-1 step, or None.

    Every candidate step is compared in constant time so the answer does not
    leak which step matched through timing.
    """
    normalized = code.strip().replace(" ", "")
    if len(normalized) != TOTP_DIGITS or not normalized.isdigit():
        return None
    key = _secret_bytes(secret_b32)
    center = current_time_step(now)
    matched: int | None = None
    for step in range(center - TOTP_ALLOWED_DRIFT_STEPS, center + TOTP_ALLOWED_DRIFT_STEPS + 1):
        if hmac.compare_digest(_hotp(key, step), normalized) and matched is None:
            matched = step
    return matched


def otpauth_uri(*, secret_b32: str, account_name: str, issuer: str) -> str:
    """Key URI for authenticator apps (Google Authenticator key-URI format)."""
    label = quote(f"{issuer}:{account_name}", safe="")
    query = urlencode(
        {
            "secret": secret_b32,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": TOTP_DIGITS,
            "period": TOTP_PERIOD_SECONDS,
        },
        quote_via=quote,
    )
    return f"otpauth://totp/{label}?{query}"


def qr_svg_data_uri(content: str) -> str:
    """Render ``content`` as an SVG QR code ``data:`` URI (no external service)."""
    buffer = io.BytesIO()
    segno.make(content, error="m").save(buffer, kind="svg", scale=5, border=2, xmldecl=False)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# --------------------------------------------------------------------------- #
# Keys
# --------------------------------------------------------------------------- #


def _master_key(settings: Settings) -> bytes:
    configured = settings.two_factor_encryption_key.strip()
    if configured:
        try:
            raw = base64.urlsafe_b64decode(configured + "=" * (-len(configured) % 4))
        except (binascii.Error, ValueError) as exc:
            raise TwoFactorKeyError("TWO_FACTOR_ENCRYPTION_KEY is not valid base64.") from exc
        if len(raw) != 32:
            raise TwoFactorKeyError("TWO_FACTOR_ENCRYPTION_KEY must decode to 32 bytes.")
        return raw
    return settings.jwt_secret.encode("utf-8")


def _derive(settings: Settings, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(
        _master_key(settings)
    )


def validate_two_factor_key(settings: Settings) -> None:
    """Raise ``TwoFactorKeyError`` when a configured key is malformed."""
    _master_key(settings)


# --------------------------------------------------------------------------- #
# Secret encryption at rest
# --------------------------------------------------------------------------- #


def _aad(user_id: uuid.UUID) -> bytes:
    # Binds the ciphertext to its user: a row copied onto another user fails to open.
    return b"alphatrade:2fa:" + user_id.bytes


def encrypt_totp_secret(secret_b32: str, *, user_id: uuid.UUID, settings: Settings) -> str:
    nonce = secrets.token_bytes(12)
    sealed = AESGCM(_derive(settings, _ENCRYPTION_INFO)).encrypt(
        nonce, secret_b32.encode("ascii"), _aad(user_id)
    )
    payload = base64.urlsafe_b64encode(nonce + sealed).decode("ascii")
    return f"{_CIPHERTEXT_VERSION}:{payload}"


def decrypt_totp_secret(ciphertext: str, *, user_id: uuid.UUID, settings: Settings) -> str:
    version, _, payload = ciphertext.partition(":")
    if version != _CIPHERTEXT_VERSION or not payload:
        raise TwoFactorSecretError("Unsupported two-factor secret format.")
    try:
        raw = base64.urlsafe_b64decode(payload)
        opened = AESGCM(_derive(settings, _ENCRYPTION_INFO)).decrypt(
            raw[:12], raw[12:], _aad(user_id)
        )
    except (InvalidTag, binascii.Error, ValueError) as exc:
        raise TwoFactorSecretError("Two-factor secret could not be decrypted.") from exc
    return opened.decode("ascii")


# --------------------------------------------------------------------------- #
# Recovery codes
# --------------------------------------------------------------------------- #


def generate_recovery_code() -> str:
    """``xxxxx-xxxxx`` from a 31-symbol alphabet (about 49.5 bits)."""
    chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(2 * _RECOVERY_CODE_GROUP))
    return f"{chars[:_RECOVERY_CODE_GROUP]}-{chars[_RECOVERY_CODE_GROUP:]}"


def normalize_recovery_code(code: str) -> str:
    return "".join(ch for ch in code.lower() if ch.isalnum())


def hash_recovery_code(code: str, *, user_id: uuid.UUID, settings: Settings) -> str:
    """Keyed digest; the user id is mixed in so equal codes differ per user."""
    message = user_id.bytes + normalize_recovery_code(code).encode("ascii", "ignore")
    return hmac.new(_derive(settings, _RECOVERY_INFO), message, hashlib.sha256).hexdigest()
