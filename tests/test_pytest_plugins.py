"""
tests/test_pytest_plugins.py

One plugin runs the async tests (CHY-212).

On 02.11.2026 every integration test failed with "Future attached to a different
loop" although nothing in the repository had changed. pytest-asyncio
(`asyncio_mode = "auto"`) and anyio's own pytest plugin both claimed the same
tests. Which one ran a test depended on the order pytest loaded them, and that
order is the order the file system lists site-packages -- on ext4 a hash of the
directory names. A new anyio release renamed its directory and flipped it: the
`pool` fixture was built on pytest-asyncio's loop, the test ran on anyio's.

`addopts` in pyproject.toml switches anyio's plugin off. This holds that line:
remove `-p no:anyio` and the second test below fails, whatever the order.
The third holds `asyncio_default_fixture_loop_scope = "function"`: set it to
`session` and the fixture's loop is no longer the test's.
"""
from __future__ import annotations

import asyncio

import pytest


def test_pytest_asyncio_runs_the_async_tests(pytestconfig):
    assert pytestconfig.pluginmanager.has_plugin("asyncio")
    assert pytestconfig.getini("asyncio_mode") == "auto"


def test_anyio_s_plugin_is_switched_off(pytestconfig):
    assert not pytestconfig.pluginmanager.has_plugin("anyio"), (
        "anyio's pytest plugin is active next to pytest-asyncio; which of the two "
        "runs a test then depends on the plugin load order (CHY-212)"
    )


@pytest.fixture
async def fixture_loop():
    yield asyncio.get_running_loop()


async def test_an_async_fixture_runs_on_the_test_s_loop(fixture_loop):
    """What the `pool` fixture needs, without a database: the test's own loop."""
    assert fixture_loop is asyncio.get_running_loop()
