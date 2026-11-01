"""
tests/test_composable_block_types.py

Which block types may reach a model (CHY-87, ADR-037).

Until CHY-87 the question did not exist: every block type *was* prompt
material, and `compose` without a type filter taking everything active in the
scope was correct. `routing_rule` is the first entry that must never be
composed — it is configuration, and pasted into a system prompt it would tell
the model which models exist and how one of them is chosen.

The guard sits in the query, not in a review rule, because the failure it
prevents is silent: a longer prompt looks like a richer context, not like a
leak. Nothing would go red, no answer would look wrong, and the routing table
would be in every trace Langfuse holds.

The tests assert on the arguments the store hands to `pool.fetch`. That is
where the filter lives; asserting further up would test a mock.
"""
from __future__ import annotations

import typing
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from contexts.repository import store
from contexts.repository.schema import COMPOSABLE_BLOCK_TYPES, BlockType

pytestmark = pytest.mark.anyio

ALL_BLOCK_TYPES = frozenset(typing.get_args(BlockType))


def _pool():
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    return pool


async def _types_asked_for(block_types):
    """The type list the query actually filters on."""
    pool = _pool()
    await store.active_blocks_in_scopes(pool, [uuid4()], block_types)
    sql, *args = pool.fetch.await_args.args
    assert "block_type = ANY($2::text[])" in sql, (
        "the query stopped filtering by type — every active block in the scope "
        "would be composed, including the ones that are not prompt material"
    )
    return set(args[1])


class TestTheAllowList:
    def test_every_composable_type_is_a_real_block_type(self):
        """A typo here would exclude a type from composition and nothing else
        would notice — the block would simply stop appearing in prompts."""
        assert COMPOSABLE_BLOCK_TYPES <= ALL_BLOCK_TYPES

    def test_a_new_block_type_is_not_composable_by_default(self):
        """The allow-list is the point.

        Were this a deny-list naming `routing_rule`, a seventh type added
        tomorrow would be composed the moment it exists, and whoever added it
        would have had to know about a rule they never read.

        `calibration_anchor` joined `routing_rule` with CHY-67 (ADR-043): it
        carries the answer a benchmark measures, and it had been composable
        since the list existed.
        """
        assert ALL_BLOCK_TYPES - COMPOSABLE_BLOCK_TYPES == {"routing_rule", "calibration_anchor"}


class TestTheQuery:
    async def test_asking_for_nothing_asks_for_the_composable_types(self):
        """`block_types=None` used to mean 'everything active in the scope'."""
        assert await _types_asked_for(None) == set(COMPOSABLE_BLOCK_TYPES)

    async def test_a_routing_rule_is_never_composed_even_when_asked_for(self):
        """The core of ADR-037.

        An explicit request does not win. A caller that names a non-composable
        type is either mistaken or malicious, and neither is a reason to put
        the routing table in front of a model.
        """
        assert await _types_asked_for(["routing_rule"]) == set()
        mixed = await _types_asked_for(["system_prompt", "routing_rule"])
        assert mixed == {"system_prompt"}

    async def test_a_calibration_anchor_is_never_composed_even_when_asked_for(self):
        """ADR-043. An anchor in the prompt is the answer in the prompt — the
        model would be measured on a question whose solution it was handed."""
        assert await _types_asked_for(["calibration_anchor"]) == set()
        mixed = await _types_asked_for(["example_io", "calibration_anchor"])
        assert mixed == {"example_io"}

    async def test_the_filter_does_not_refuse_the_normal_case(self):
        """Counter-probe: a guard that lets nothing through passes every
        prohibition test and is useless."""
        assert await _types_asked_for(["system_prompt", "example_io"]) == {
            "system_prompt", "example_io",
        }

    async def test_no_query_ever_carries_a_non_composable_type(self):
        """Driven over every declared type rather than the one we know about,
        so a seventh type is covered the day it is added."""
        for typ in sorted(ALL_BLOCK_TYPES):
            asked_for = await _types_asked_for([typ])
            assert not (asked_for - COMPOSABLE_BLOCK_TYPES), (
                f"asking for {typ} put a non-composable type into the query"
            )
