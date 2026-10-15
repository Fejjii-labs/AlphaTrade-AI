"""Fail-closed guard against real outbound network access from tests.

``block_outbound_network`` patches DNS resolution and socket connects for the
duration of the context. Loopback and Unix-domain targets stay allowed (local
PostgreSQL/Redis). Any other target is recorded and refused, so a test that
silently swallows the resulting connection error can still be failed by
asserting the recorded attempts are empty.
"""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterator
from contextlib import contextmanager

_LOCAL_NAMES = frozenset({"localhost", "localhost.localdomain", ""})


class OutboundNetworkBlockedError(OSError):
    """Raised when a test tries to reach a non-loopback host."""


def _is_local(host: object) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "ignore")
    text = str(host).strip().lower()
    if text in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(text.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


@contextmanager
def block_outbound_network() -> Iterator[list[str]]:
    """Refuse and record non-loopback DNS lookups and socket connects."""
    attempts: list[str] = []
    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guarded_getaddrinfo(host, *args, **kwargs):
        if not _is_local(host):
            attempts.append(f"getaddrinfo:{host}")
            raise OutboundNetworkBlockedError(f"test attempted DNS lookup of {host!r}")
        return real_getaddrinfo(host, *args, **kwargs)

    def _check(sock: socket.socket, address: object) -> None:
        if sock.family not in (socket.AF_INET, socket.AF_INET6):
            return
        host = address[0] if isinstance(address, tuple) else address
        if not _is_local(host):
            attempts.append(f"connect:{host}")
            raise OutboundNetworkBlockedError(f"test attempted to connect to {address!r}")

    def _guarded_connect(self, address):
        _check(self, address)
        return real_connect(self, address)

    def _guarded_connect_ex(self, address):
        _check(self, address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = _guarded_getaddrinfo
    socket.socket.connect = _guarded_connect
    socket.socket.connect_ex = _guarded_connect_ex
    try:
        yield attempts
    finally:
        socket.getaddrinfo = real_getaddrinfo
        socket.socket.connect = real_connect
        socket.socket.connect_ex = real_connect_ex
