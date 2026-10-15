"""The backend suite refuses outbound sockets on every test, not via a CLI flag.

The full gate clears pytest addopts, so the block is installed from
``pytest_runtest_setup``. Loopback and Unix sockets stay available for Postgres.
"""

from __future__ import annotations

import socket

import pytest
from pytest_socket import SocketConnectBlockedError

_DOCUMENTATION_ADDRESS = ("203.0.113.1", 9)


def test_outbound_connect_fails_loudly() -> None:
    """A non-loopback connect raises, including the refused address in the error."""
    outbound = socket.socket()
    try:
        with pytest.raises(SocketConnectBlockedError, match=r"203\.0\.113\.1"):
            outbound.connect(_DOCUMENTATION_ADDRESS)
    finally:
        outbound.close()


def test_block_is_still_installed_on_the_next_test() -> None:
    """pytest-socket clears its patch at teardown; the suite hook puts it back."""
    outbound = socket.socket()
    try:
        with pytest.raises(SocketConnectBlockedError, match=r"203\.0\.113\.1"):
            outbound.connect(_DOCUMENTATION_ADDRESS)
    finally:
        outbound.close()


def test_loopback_connect_stays_open_for_postgres() -> None:
    """127.0.0.1 connects are the Postgres path used by the CI service."""
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        port = server.getsockname()[1]
        client = socket.create_connection(("127.0.0.1", port), timeout=2)
        client.close()
    finally:
        server.close()
