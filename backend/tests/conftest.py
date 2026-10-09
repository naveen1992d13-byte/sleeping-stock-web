"""Test-harness compatibility for Python 3.12.

Several older async tests call asyncio.get_event_loop().run_until_complete().
Python 3.12 no longer creates a current loop implicitly. This fixture only
installs a loop when none exists; it does not rewrite those tests.
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def _compat_event_loop():
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    yield
