"""Shared pytest fixtures."""

from __future__ import annotations

import io

import pytest


@pytest.fixture
def small_body() -> bytes:
    """Tiny payload for round-trip tests."""
    return b"hello-audittrace"


@pytest.fixture
def small_stream(small_body: bytes) -> io.BytesIO:
    return io.BytesIO(small_body)
