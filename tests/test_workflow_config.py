"""
tests/test_workflow_config.py

Workflow configuration as data, and the record of what a run used (CHY-42).

The load-bearing test in this file is not one of the endpoint tests. It is
`test_the_snapshot_never_grows_past_what_takes_effect`.

`pilot_runs.effective_config` is meant to be the record of the configuration a
run actually used (EU AI Act Art. 12). A value recorded there that no node
reads makes the record **false** — and unnoticeably so, because it looks
complete. ADR-028 point 4 keeps Study and Banking out of scope for rebuilding,
so `params` is stored and served but takes no effect, and must therefore stay
out of the snapshot. Nothing about that is enforced by the type system; it is
enforced here.

The second one that matters is `test_every_tier_the_pilots_ask_for_is_seeded`.
`_model()` falls back to the pilot's module constant when the run carries no
map — needed for a CLI invocation, and a silent lie for a run whose
effective_config claims otherwise. That test is what keeps the fallback
unreachable in production.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from uuid import uuid4

import pytest

from contexts.pilots import workflow_config as cfg
from contexts.pilots.errors import ConfigSectionNotEffective, UnknownPilotConfig
from tests.conftest import actor_row, client_for, make_pool, make_redis, workflow_config_row

_ROOT = Path(__file__).resolve().parents[1]
_MIGRATION = (_ROOT / "migrations" / "017_workflow_config.sql").read_text(encoding="utf-8")


# ── What the snapshot may contain ─────────────────────────────────────────────

def test_the_snapshot_never_grows_past_what_takes_effect():
    """The record must not claim more than the run honoured.

    Given a stored config with both sections, the snapshot carries `model_map`
    and not `params` — because the pilots read the first and ignore the second.
    If `params` ever appears here, either it now takes effect (and
    EFFECTIVE_SECTIONS should say so) or the record has quietly become false.
    """
    snapshot = cfg.compose_effective(
        pilot_name="study",
        version=3,
        stored={"model_map": {"small": "phi3-mini"}, "params": {"chunk_size": 1200}},
        run_config={},
    )

    assert set(snapshot) == {"pilot_name", "config_version", *cfg.EFFECTIVE_SECTIONS}
    assert "params" not in snapshot


def test_the_snapshot_names_the_configuration_it_came_from():
    """Without the version, an audit can read "what" but not "against what".

    Telling "the configuration changed since" from "this run differed" is
    exactly the question a reader has six months later.
    """
    snapshot = cfg.compose_effective(
        pilot_name="banking", version=7, stored={"model_map": {}}, run_config={}
    )
    assert snapshot["pilot_name"] == "banking"
    assert snapshot["config_version"] == 7


def test_a_run_override_wins_over_the_stored_value():
    snapshot = cfg.compose_effective(
        pilot_name="study", version=1,
        stored={"model_map": {"small": "phi3-mini", "medium": "gemma4-e4b"}},
        run_config={"model_map": {"small": "gemma3-1b"}},
    )
    assert snapshot["model_map"] == {"small": "gemma3-1b", "medium": "gemma4-e4b"}


def test_an_override_of_one_tier_leaves_the_others():
    """The counter-probe to the test above: a replace rather than a merge would
    silently drop every tier the caller did not mention, and the run would use
    the fallback for them while the record said otherwise."""
    snapshot = cfg.compose_effective(
        pilot_name="study", version=1,
        stored={"model_map": {"small": "a", "medium": "b", "cloud": "c"}},
        run_config={"model_map": {"medium": "x"}},
    )
    assert snapshot["model_map"] == {"small": "a", "medium": "x", "cloud": "c"}


# ── Refused, not ignored ──────────────────────────────────────────────────────

class TestOverrideValidation:
    def test_a_section_that_takes_no_effect_is_refused(self):
        """`params` is stored and served, and changing it per run would do
        nothing. Accepting it would be an API that reports success without
        looking — the caller would believe their value is in force."""
        with pytest.raises(ConfigSectionNotEffective) as refused:
            cfg.check_override({"params": {"chunk_size": 5}})
        assert "CHY-82" in str(refused.value)

    def test_an_unknown_section_is_a_different_error(self):
        """A typo and a "not yet" need different answers, or somebody goes
        looking for a feature when they mistyped `model_maps`."""
        with pytest.raises(UnknownPilotConfig):
            cfg.check_override({"model_maps": {}})

    def test_an_effective_section_passes(self):
        cfg.check_override({"model_map": {"small": "x"}})

    def test_no_override_passes(self):
        cfg.check_override({})


# ── The fallback must be unreachable in production ────────────────────────────

def _tiers_the_nodes_ask_for(pilot: str) -> set[str]:
    """Every literal tier passed to `_model(state, ...)` in a pilot's nodes.

    Over the AST, not a grep: the same words appear in comments and docstrings
    of these files ("Uses MODEL_CLOUD for complex reasoning"), and counting
    prose as code is a mistake this project has made six times in one day.
    """
    src = (_ROOT / "pilots" / pilot / pilot / "nodes.py").read_text(encoding="utf-8")
    tiers = set()
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "_model" and len(node.args) == 2
                and isinstance(node.args[1], ast.Constant)):
            tiers.add(node.args[1].value)
    return tiers


def _seeded_model_maps() -> dict[str, dict]:
    """The model maps migration 017 inserts."""
    block = re.search(
        r"INSERT INTO workflow_configs.*?ON CONFLICT", _MIGRATION, re.S
    )
    assert block, "seed INSERT not found in migration 017"
    maps = {}
    for m in re.finditer(r"\('(\w+)',\s*\n\s*'(\{[^']*\})'::jsonb", block.group(0)):
        maps[m.group(1)] = json.loads(m.group(2))
    return maps


@pytest.mark.parametrize("pilot", ["study", "banking"])
def test_every_tier_the_pilots_ask_for_is_seeded(pilot: str):
    """The guard that keeps `_model()`'s fallback out of production.

    A tier missing from the seeded map would make the node silently read its
    module constant, while effective_config recorded the map as what was used.
    The record would be false and nothing would say so — which is why this is a
    test and not a comment next to the fallback.
    """
    asked = _tiers_the_nodes_ask_for(pilot)
    assert asked, f"no _model() calls found in {pilot}/nodes.py — the parser missed them"

    seeded = _seeded_model_maps()
    assert pilot in seeded, f"migration 017 seeds no config for {pilot}"

    missing = asked - set(seeded[pilot])
    assert not missing, (
        f"{pilot}/nodes.py asks for tiers the seeded model_map does not define: "
        f"{sorted(missing)}. Those runs would fall back to the module constant "
        f"while effective_config claimed the map."
    )


def test_the_seed_covers_every_pilot_that_can_be_started():
    """A known pilot with no seeded row reaches the "seed is missing" branch on
    every run — an error path nobody would notice until a run failed."""
    from contexts.pilots.run import KNOWN_PILOTS

    assert set(_seeded_model_maps()) >= KNOWN_PILOTS


# ── The record is written once ────────────────────────────────────────────────

def _sql_without_comments(sql: str) -> str:
    """The migration with `--` comment tails removed.

    The first version of the test below searched the raw file, and a probe that
    commented the CREATE TRIGGER out left it green: the words were all still
    there, in a comment. Prose counted as code — the same mistake as reading a
    Python docstring as a statement, one language over.

    Line-based and not a full SQL parser: `--` inside a string literal would
    fool it, and there is none in this migration. Written down because the
    limitation is real and the next migration might have one.
    """
    return "\n".join(line.split("--")[0] for line in sql.split("\n"))


def test_effective_config_is_protected_by_a_trigger():
    """A record that can be rewritten afterwards is not a record.

    Asserted against the migration rather than a live database for the same
    reason test_supersede_block does: a trigger that raises cannot be exercised
    through a mock, and what must not be quietly removed is the trigger itself.
    """
    sql = _sql_without_comments(_MIGRATION)

    # The statement, not the words: `CREATE TRIGGER x BEFORE UPDATE ON y
    # ... EXECUTE FUNCTION z` has to be one live statement, or the column is
    # unprotected while the file still reads as though it were not.
    trigger = re.search(
        r"CREATE TRIGGER\s+trg_effective_config_write_once\s+"
        r"BEFORE UPDATE ON pilot_runs\s+"
        r"FOR EACH ROW EXECUTE FUNCTION\s+cc_effective_config_write_once\(\)",
        sql, re.S,
    )
    assert trigger, "the write-once trigger is not created as a live statement"
    assert "OLD.effective_config IS NOT NULL" in sql


def test_the_upsert_keeps_a_section_the_caller_did_not_send():
    """`EXCLUDED` would have cleared it.

    EXCLUDED carries the value the INSERT would have written, and that has
    already been COALESCEd to '{}' — never NULL, so "leave this section alone"
    would have written an empty object instead. Caught while writing the query;
    asserted here because the next person to touch it will reach for EXCLUDED
    again, it being the idiomatic thing.
    """
    from contexts.pilots import store

    # Over the AST, and only the string constants: the comment above the query
    # explains *why* EXCLUDED is wrong and therefore contains the word. A text
    # search would have failed on the explanation — prose counted as code, the
    # mistake this project has now made seven times.
    src = ast.parse(Path(store.__file__).read_text(encoding="utf-8"))
    fn = next(
        n for n in ast.walk(src)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "upsert_workflow_config"
    )
    sql = "".join(
        n.value for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n is not fn.body[0].value          # the docstring, excluded by position
    )
    assert "COALESCE($2::text::jsonb, workflow_configs.model_map)" in sql
    assert "EXCLUDED" not in sql


# ── Through the endpoints ─────────────────────────────────────────────────────

def test_reading_a_config_falls_back_to_the_database_on_a_cache_miss():
    pool = make_pool(workflow_config=workflow_config_row())
    with client_for(pool, redis=make_redis()) as client:
        response = client.get("/v1/pilots/configs/study")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["model_map"] == {"small": "phi3-mini", "medium": "gemma4-e4b"}
    assert body["version"] == 1


def test_reading_a_config_prefers_the_cache():
    """The counter-probe: without it the test above would also pass against an
    implementation that never looks at Redis."""
    cached = workflow_config_row(version=9, model_map={"small": "cached-model"})
    cached["updated_at"] = cached["updated_at"].isoformat()
    redis = make_redis()
    redis.get.return_value = json.dumps(cached)

    pool = make_pool(workflow_config=workflow_config_row(version=1))
    with client_for(pool, redis=redis) as client:
        body = client.get("/v1/pilots/configs/study").json()

    assert body["version"] == 9
    assert body["model_map"] == {"small": "cached-model"}

    # Not `fetchrow.assert_not_called()`: the actor lookup goes through the
    # same method, so that assertion started failing the moment the read began
    # checking the role — and it would have been "fixed" by deleting it, which
    # is how a counter-probe quietly stops probing. The claim is narrower and
    # actually the one that matters: the *configuration* was not read.
    asked = [call.args[0] for call in pool.fetchrow.await_args_list]
    assert not any("FROM workflow_configs" in sql for sql in asked), (
        "the cache was ignored and the row read from the database anyway"
    )


@pytest.mark.parametrize(
    "broken",
    [{"get": ConnectionError("redis gone")}, {"value": "{not json"}],
    ids=["redis-silent", "entry-unreadable"],
)
def test_a_cache_that_fails_costs_a_read_and_leaves_a_line(broken, caplog):
    """Before CHY-97 both were passed over without a word: a Redis that had
    stopped answering showed only as everything getting slower."""
    import logging

    redis = make_redis()
    if "get" in broken:
        redis.get.side_effect = broken["get"]
    else:
        redis.get.return_value = broken["value"]
    pool = make_pool(workflow_config=workflow_config_row())
    with (
        client_for(pool, redis=redis) as client,
        caplog.at_level(logging.WARNING, logger="contexts.pilots.service"),
    ):
        response = client.get("/v1/pilots/configs/study")

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 1
    assert [r.message for r in caplog.records if r.name == "contexts.pilots.service"] == [
        "pilot config cache not read"
    ]


def test_an_unknown_pilot_is_refused_before_anything_is_read():
    """400, not 422 — `UnknownPilot` is what `check_pilot_known` raises, and
    this codebase maps it to 400. Asserted as it is rather than as it might
    tidily have been: the two unknown-value families answering differently
    (UnknownStatus is a 422) predates this ticket and is not its business."""
    pool = make_pool()
    with client_for(pool, redis=make_redis()) as client:
        assert client.get("/v1/pilots/configs/nope").status_code == 400


def test_writing_a_config_requires_architect():
    pool = make_pool(actor=actor_row(role="contributor"))
    with client_for(pool, redis=make_redis()) as client:
        response = client.put("/v1/pilots/configs/study",
                              json={"model_map": {"small": "x"}})
    assert response.status_code == 403, response.text


def test_writing_a_config_drops_the_cached_copy():
    """The invalidation, asserted as an effect and not as a line of code.

    A PUT that writes the row and leaves the cache is not a slow answer, it is
    a wrong one: every run afterwards composes from a value nobody can see.
    """
    redis = make_redis()
    pool = make_pool(
        actor=actor_row(role="architect"),
        conn_fetchrow=workflow_config_row(version=2, model_map={"small": "new"}),
    )
    with client_for(pool, redis=redis) as client:
        response = client.put("/v1/pilots/configs/study",
                              json={"model_map": {"small": "new"}})

    assert response.status_code == 200, response.text
    assert response.json()["version"] == 2
    redis.delete.assert_awaited_once_with("cc:workflow:study:config")


def test_starting_a_run_records_what_it_will_use():
    """The whole point of the ticket, seen from outside."""
    run_row = {
        "id": uuid4(), "thread_id": "study-abc", "pilot_name": "study",
        "input_file": "study-example.pdf", "status": "pending", "started_at": None,
        "completed_at": None, "result_json": None, "error_message": None,
        "created_at": workflow_config_row()["updated_at"],
        "run_config": {"model_map": {"small": "gemma3-1b"}},
        "effective_config": {
            "pilot_name": "study", "config_version": 1,
            "model_map": {"small": "gemma3-1b", "medium": "gemma4-e4b"},
        },
    }
    pool = make_pool(actor=actor_row(role="contributor"), conn_fetchrow=run_row)

    from contexts.pilots import service

    out = service._run_out(run_row)
    assert out.effective_config["model_map"]["small"] == "gemma3-1b"
    assert out.run_config == {"model_map": {"small": "gemma3-1b"}}
    assert pool is not None
