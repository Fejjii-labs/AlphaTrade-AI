"""Applicable HTTP and model admission shared by both Agent capability engines."""

from fastapi import Depends

from app.security.rate_limit import tenant_rate_limit_dependency

TURN_DEPENDENCIES = [
    Depends(
        tenant_rate_limit_dependency(
            "agent:turn", limit=60, window_seconds=3600, ip_limit=120, user_limit=60
        )
    )
]
