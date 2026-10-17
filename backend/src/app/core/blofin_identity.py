"""Native UID parsing and opaque connection selectors; selectors are never UID proof."""

import hashlib
import json
from typing import TYPE_CHECKING, Any

from app.core.errors import ExchangeDemoInactiveError

if TYPE_CHECKING:
    from app.core.config import Settings


def native_uid(payload: Any) -> str | None:
    uid = payload.get("uid") if isinstance(payload, dict) else None
    if (
        not isinstance(uid, str)
        or not 0 < len(uid) <= 128
        or not uid.isascii()
        or any(not (c.isalnum() or c in "-_.") for c in uid)
    ):
        return None
    return uid


def connection_binding(settings: "Settings", *, readonly: bool) -> str:
    credentials = (
        (
            settings.blofin_readonly_api_key,
            settings.blofin_readonly_api_secret,
            settings.blofin_readonly_api_passphrase,
        )
        if readonly
        else (settings.blofin_api_key, settings.blofin_api_secret, settings.blofin_api_passphrase)
    )
    selected = tuple(value.strip() for value in credentials)
    if not all(selected):
        raise ExchangeDemoInactiveError("BloFin connection credentials missing.")
    selector = (
        "readonly" if readonly else "execution",
        settings.blofin_demo_rest_base_url.rstrip("/"),
        *selected,
    )
    return hashlib.sha256(json.dumps(selector).encode()).hexdigest()
