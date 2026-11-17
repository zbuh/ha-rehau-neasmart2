"""Fixtures."""

from __future__ import annotations

import socket

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load custom_components/ in every test."""
    yield


@pytest.fixture(autouse=True)
def _real_sockets(socket_enabled):
    """These tests use a real TCP server on 127.0.0.1 (the converter connects to it)."""
    yield


@pytest.fixture
def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
