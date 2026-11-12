"""
tests/test_compose_by_tags.py

A prompt per step: composition by tag, and a prompt from several inputs
(CHY-178).

Two additions to the engine, both small and both needed for the credit
workflow, each tested here where it lives:

- **Tags.** A `context_compose` node may name tags; only blocks that carry every
  one are composed. Before, a node chose by type and scope only, so every model
  step of a workflow received the same system prompts. What only PostgreSQL
  shows — that the filter applies to the version that would be composed, not to
  an older one — is in tests/integration/test_compose_by_tags_db.py
- **A prompt from several inputs.** A dotted target in `input_mapping` builds an
  object, and an object as the prompt is set out in sections: a heading per key,
  text as it came, everything else as JSON
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from api.v1.workflows import _as_prompt
from contexts.orchestration.dataflow import apply_mapping
from contexts.orchestration.schema import ContextRequirements
from contexts.repository import compose, store
from contexts.repository.schema import ComposeRequest


# ── tags ──────────────────────────────────────────────────────────────────────

class TestTheRequirement:
    def test_a_node_names_its_tags(self):
        req = ContextRequirements(block_types=["system_prompt"], tags=["banking:risiko"])
        assert req.tags == ["banking:risiko"]

    def test_without_tags_nothing_changes(self):
        assert ContextRequirements(block_types=["system_prompt"]).tags is None

    @pytest.mark.parametrize("tags", [[], [""]])
    def test_an_empty_list_or_an_empty_tag_is_refused(self, tags):
        """`[]` would read as "no filter" — the opposite of what it says."""
        with pytest.raises(ValidationError):
            ContextRequirements(block_types=["system_prompt"], tags=tags)

    @pytest.mark.parametrize("tags", [[], [""]])
    def test_compose_refuses_them_too(self, tags):
        """The same request on /v1/compose: the store reads `[]` as no filter."""
        with pytest.raises(ValidationError):
            ComposeRequest(scope_id=uuid4(), tags=tags)


async def _query(tags):
    pool = MagicMock()
    pool.fetch = AsyncMock(return_value=[])
    await store.active_blocks_in_scopes(pool, [uuid4()], ["system_prompt"], tags=tags)
    return pool.fetch.await_args.args


class TestTheQuery:
    async def test_the_tags_reach_the_query(self):
        sql, *args = await _query(["banking:risiko"])
        assert "newest.tags @> $3::text[]" in sql
        assert args[2] == ["banking:risiko"]

    async def test_without_tags_the_filter_is_off(self):
        _sql, *args = await _query(None)
        assert args[2] is None

    async def test_the_filter_comes_after_the_version_is_chosen(self):
        """Filtered inside the DISTINCT ON, a block whose newest version dropped
        the tag would fall back to an older version that carries it."""
        sql, *_ = await _query(["banking:risiko"])
        assert sql.index("DISTINCT ON") < sql.index(") AS newest") < sql.index("@> $3")


class TestTheCache:
    def test_two_steps_asking_for_different_tags_get_different_entries(self):
        base = {"scope_id": uuid4(), "workflow_id": uuid4(), "node_ref": "prompt",
                "block_types": ["system_prompt"]}
        a = compose.compiled_key(ComposeRequest(**base, tags=["banking:risiko"]), {})
        b = compose.compiled_key(ComposeRequest(**base, tags=["banking:votum"]), {})
        assert a != b
        assert compose.compiled_key(ComposeRequest(**base), {}) not in (a, b)


# ── a prompt from several inputs ──────────────────────────────────────────────

class TestDottedTargets:
    def test_a_dotted_target_builds_an_object(self):
        available = {"kennzahlen": {"data": {"ek_quote": 0.42}},
                     "auffaelligkeiten": {"data": {"anomalies": []}}}
        out = apply_mapping({"prompt.kennzahlen": "kennzahlen.data",
                             "prompt.auffaelligkeiten": "auffaelligkeiten.data.anomalies"},
                            available)
        assert out == {"prompt": {"kennzahlen": {"ek_quote": 0.42}, "auffaelligkeiten": []}}

    def test_a_path_that_leads_nowhere_is_left_out_as_before(self):
        out = apply_mapping({"prompt.bewertung": "bewertung.content"}, {})
        assert out == {}

    def test_a_plain_target_is_unchanged(self):
        assert apply_mapping({"prompt": "doc.text"}, {"doc": {"text": "x"}}) == {"prompt": "x"}


class TestThePromptAsTheModelReadsIt:
    def test_text_stays_text(self):
        assert _as_prompt("Bitte prüfen.") == "Bitte prüfen."

    def test_an_object_is_set_out_in_sections_in_its_order(self):
        prompt = _as_prompt({"risiko": {"risikoklasse": "GELB"},
                             "kontoauszug": "<<<DOC\nGutschrift 1.200,00\nDOC>>>"})
        assert prompt.index("## risiko") < prompt.index("## kontoauszug")
        assert '"risikoklasse": "GELB"' in prompt

    def test_a_document_keeps_its_fence_and_its_line_breaks(self):
        """Inside a JSON string the markers would be escaped and the text one
        line — the model would read a different document."""
        fenced = "<<<DOC\nZeile eins\nZeile zwei\nDOC>>>"
        assert fenced in _as_prompt({"kontoauszug": fenced})

    def test_non_ascii_stays_readable(self):
        assert "GRÜN" in _as_prompt({"risiko": {"risikoklasse": "GRÜN"}})
