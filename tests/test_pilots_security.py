"""The four defects of CHY-90, each shown to be caught (CHY-102).

Measured on cohereny_dev before the fix, on 19.08.2026:

  18 pilot runs, every one of them in a terminal status and therefore
  deletable · 9 transparency records hanging off them · 0 runs with an owner ·
  `actor` taken by the endpoint and never read.

So: any authenticated caller could delete any run, nothing was written to the
audit log, and ON DELETE CASCADE took the Article 50 records with it. One DELETE
against the live schema turned nine records into eight — that was the
demonstration, and this file is what keeps it demonstrated.
"""
from __future__ import annotations

import ast
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from uuid import UUID  # noqa: E402

from contexts.pilots.errors import RunNotFound  # noqa: E402
from contexts.pilots.run import contain_input_file  # noqa: E402
from tests.conftest import actor_row, client_for, make_pool  # noqa: E402


def _function(path: str, name: str) -> ast.AST:
    """The named function's syntax tree, from any module in the repo."""
    src = pathlib.Path(path).read_text(encoding="utf-8")
    tree = ast.parse(src)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == name
    )


def _literals(fn: ast.AST) -> list[str]:
    """String constants only — SQL and identifiers, never comments or docstrings.

    The docstring has to go too, and finding that out cost a red run: the
    docstring of clear_oversight_gate names oversight_decision in order to
    explain why it must not write it. Same trap as the comment before it, one
    level down — prose read as code, again in the check written to keep them
    apart.
    """
    doc = ast.get_docstring(fn, clean=False)
    return [n.value for n in ast.walk(fn)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and n.value != doc]


class TestPathContainment:
    """A pilot reads a document in order to send it to a language model.

    Reading an arbitrary file is therefore not only a read: it is a read with
    exfiltration attached. `Path(base) / user_input` provides two ways out and
    `.exists()` confirmed the hit.
    """

    @pytest.fixture
    def inputs(self, tmp_path, monkeypatch):
        from api import config
        base = tmp_path / "examples"
        base.mkdir()
        (base / "report.pdf").write_bytes(b"%PDF-1.4 ok")
        (tmp_path / "secret.txt").write_text("not for the model", encoding="utf-8")
        monkeypatch.setattr(config.settings, "pilot_input_dir", str(base))
        return base

    def test_a_normal_filename_resolves(self, inputs):
        assert contain_input_file(inputs, "report.pdf") == (inputs / "report.pdf").resolve()

    def test_climbing_out_with_dotdot_is_refused(self, inputs):
        with pytest.raises(RunNotFound):
            contain_input_file(inputs, "../secret.txt")

    def test_an_absolute_path_is_refused(self, inputs):
        """`Path("/data/examples") / "/etc/passwd"` is `/etc/passwd`.

        The join silently discards the base when the right-hand side is
        absolute. Nothing in the old code noticed.
        """
        with pytest.raises(RunNotFound):
            contain_input_file(inputs, "/etc/passwd")

    def test_a_deep_climb_is_refused(self, inputs):
        with pytest.raises(RunNotFound):
            contain_input_file(inputs, "../../../../etc/passwd")

    def test_a_symlink_pointing_outside_is_refused(self, inputs, tmp_path):
        """Why containment is checked after resolve(), not before.

        Rejecting ".." in the raw string would pass this case: the name is
        innocent and the link is not.
        """
        (inputs / "innocent.pdf").symlink_to(tmp_path / "secret.txt")
        with pytest.raises(RunNotFound):
            contain_input_file(inputs, "innocent.pdf")

    def test_a_directory_is_not_an_input_file(self, inputs):
        (inputs / "subdir").mkdir()
        with pytest.raises(RunNotFound):
            contain_input_file(inputs, "subdir")

    def test_the_refusal_is_indistinguishable_from_not_found(self, inputs):
        """A caller probing for /etc/passwd must not learn whether it exists.

        Since CHY-104 the rule raises a domain error and api/main.py chooses
        the code, so the claim is checked in two halves: the same error type
        here, and the same status code out of the real mapping table — not a
        404 written into the assertion.
        """
        from api.main import _PILOT_STATUS

        with pytest.raises(RunNotFound) as absent:
            contain_input_file(inputs, "nothing-here.pdf")
        with pytest.raises(RunNotFound) as blocked:
            contain_input_file(inputs, "../secret.txt")
        assert type(absent.value) is type(blocked.value)
        assert _PILOT_STATUS[type(absent.value)] == 404


class TestTheEndpointReadsItsActor:
    """The defect underneath the other three: authenticated, then anonymous.

    The logic moved to contexts/pilots/service.py with CHY-104, so the checks
    moved with it. The endpoint is still checked — for passing the actor on
    rather than dropping it, which is the CHY-90 defect in its new shape.
    """

    def test_the_endpoint_hands_its_actor_to_the_service(self):
        fn = _function("api/v1/pilots.py", "cancel_or_delete_run")
        call = next(
            n for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "cancel_or_delete"
        )
        passed = {a.id for a in call.args if isinstance(a, ast.Name)}
        assert "actor" in passed, (
            "the endpoint takes `actor` — it has to pass it on. Taking it and "
            "dropping it is what CHY-90 was."
        )

    def test_the_service_resolves_the_caller(self):
        fn = _function("contexts/pilots/service.py", "cancel_or_delete")
        called = {getattr(n.func, "id", "") for n in ast.walk(fn) if isinstance(n, ast.Call)}
        assert "resolve_actor" in called, (
            "an actor that is never resolved has no identity to audit against"
        )

    def test_every_exit_writes_an_audit_entry(self):
        """Delete, cancel and the oversight release all record who did it.

        Counted rather than asserted per branch: the point is that no path out
        of this function is silent.
        """
        fn = _function("contexts/pilots/service.py", "cancel_or_delete")
        returns = sum(
            1 for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None
        )
        audits = sum(
            1 for n in ast.walk(fn)
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "write_audit"
        )
        assert audits >= returns - 1, (
            f"{returns} ways out, {audits} audit entries — a state change that "
            f"leaves no record is the defect, not the omission"
        )

    def test_the_oversight_release_is_not_recorded_as_a_decision(self):
        """Cancelling a run waiting on Art. 14 is not an oversight decision.

        The gate only speaks "approved"/"rejected", so releasing it sends
        "rejected". Writing that into oversight_decision would manufacture
        evidence of a human decision nobody made — the exact opposite of what
        CHY-88 built.

        Since CHY-104 the SQL lives in store.py, which lets both halves be
        stated: the release path must not touch the column, and the decision
        path must. Checking only the first would stay green if the second
        stopped writing it at all.

        The check walks the syntax tree rather than the source text. The first
        version read `ast.get_source_segment` — which keeps comments — and
        failed on the comment explaining the rule. Prose counted as code, in
        the test written to enforce that distinction.
        """
        release = _literals(_function("contexts/pilots/store.py", "clear_oversight_gate"))
        assert not any("oversight_decision" in s for s in release), (
            "cancelling must not write an Art. 14 decision"
        )
        record = _literals(_function("contexts/pilots/store.py", "record_oversight"))
        assert any("oversight_decision" in s for s in record), (
            "the decision path is the one that must write it"
        )

    def test_the_cancel_path_never_reaches_for_the_decision_action(self):
        fn = _function("contexts/pilots/service.py", "cancel_or_delete")
        names = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name)}
        assert "ACTION_PILOT_RUN_CANCELLED" in names
        assert "ACTION_PILOT_OVERSIGHT_DECIDED" not in names


class TestOwnership:
    """Only the actor who started a run may cancel or delete it.

    Written after removing the ownership check and watching all ten tests stay
    green. The other three defects had behavioural or static cover; this one had
    none, and the suite would have reported CHY-90 as fixed with a quarter of it
    undone. Third time this week that deleting a guard proved the tests were
    describing the code rather than checking it.
    """

    _RUN = UUID("11111111-2222-3333-4444-555555555555")
    _MINE = UUID("aaaaaaaa-0000-0000-0000-000000000001")
    _YOURS = UUID("bbbbbbbb-0000-0000-0000-000000000002")

    def _pool(self, owner, status="complete", evidence=0):
        return make_pool(
            actor=actor_row(self._MINE, role="consumer"),
            fetchrow={"status": status, "actor_id": owner},
            fetchval=evidence,
        )

    def test_the_owner_may_delete(self):
        pool = self._pool(self._MINE)
        with client_for(pool) as client:
            resp = client.request("DELETE", f"/v1/pilots/runs/{self._RUN}")
            # 202: the route declares status_code=202 — the delete is done,
            # the background bookkeeping is not.
            assert resp.status_code == 202, resp.text
            assert resp.json()["status"] == "deleted"

    def test_a_stranger_may_not(self):
        pool = self._pool(self._YOURS)
        with client_for(pool) as client:
            resp = client.request("DELETE", f"/v1/pilots/runs/{self._RUN}")
            assert resp.status_code == 403, resp.text

    def test_a_run_without_an_owner_stays_deletable(self):
        """Runs from before CHY-88 have actor_id NULL.

        Refusing them would make every old run undeletable by anybody. Allowing
        them keeps the endpoint usable while everything created from now on is
        owned.
        """
        pool = self._pool(None)
        with client_for(pool) as client:
            assert client.request(
                "DELETE", f"/v1/pilots/runs/{self._RUN}"
            ).status_code == 202

    def test_transparency_records_block_the_delete(self):
        """Migration 015 refuses this at the database; the endpoint explains it.

        Note what is *not* mocked here: no "does it have records" query. The
        endpoint attempts the delete and lets the foreign key speak. Asking
        first would be a second round trip and a check-then-act that a
        concurrent insert can slip through — the database already knows.
        """
        import asyncpg
        pool = self._pool(self._MINE, evidence=3)
        pool.conn.execute.side_effect = asyncpg.ForeignKeyViolationError(
            "violates foreign key constraint"
        )
        with client_for(pool) as client:
            resp = client.request("DELETE", f"/v1/pilots/runs/{self._RUN}")
            assert resp.status_code == 409
            assert "Art. 50" in resp.json()["detail"]
            assert "transparency records" in resp.json()["detail"]

    def test_a_clean_delete_asks_no_extra_question(self):
        """One round trip on the happy path, not two."""
        pool = self._pool(self._MINE)
        with client_for(pool) as client:
            assert client.request(
                "DELETE", f"/v1/pilots/runs/{self._RUN}"
            ).status_code == 202
        # One statement inside the transaction: the audit entry and the delete.
        # No "does it have records" question before either of them.
        assert pool.fetchval.await_count == 0
