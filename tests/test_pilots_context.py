"""The pilots context after CHY-104 — rules without a database.

The claim of ADR-030 is not that the code is in more files. It is that a rule
can be checked without standing up the stack. Every test below is the evidence
for one such rule, and none of them opens a connection, builds a client or
speaks HTTP.

The measure that made this worth doing: api/v1/pilots.py was 846 lines at 54 %
coverage, and it was the hotspot of six findings in the 14.08. audit at once.
Not a coincidence — where the most happens and the least is tested, everything
collects.
"""
from __future__ import annotations

import ast
import asyncio
import pathlib
import sys
from uuid import uuid4

import pytest

from tests.conftest import make_pool, stub_user

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from api.main import _PILOT_STATUS  # noqa: E402
from contexts.pilots import run as rules  # noqa: E402
from contexts.pilots import service  # noqa: E402
from contexts.pilots.errors import (  # noqa: E402
    InvalidDecision,
    NotAwaitingOversight,
    PilotError,
    RunNotCancellable,
    RunNotFound,
    RunNotOwned,
    UnknownPilot,
)
from contexts.pilots.events import (  # noqa: E402
    DONE,
    EOF,
    ERROR,
    OVERSIGHT_HISTORICAL,
    OVERSIGHT_REQUIRED,
    STATUS,
)
from contexts.pilots.registry import RunRegistry  # noqa: E402
from contexts.pilots.run import Disposition  # noqa: E402


class TestDisposition:
    """What cancelling means depends on the state, and it used to be five
    branches interleaved with SQL inside one 150-line endpoint. None of the
    five were covered."""

    @pytest.mark.parametrize("status", ["failed", "complete", "rejected"])
    def test_a_terminal_run_is_deleted(self, status):
        assert rules.plan_disposition(status=status, in_flight=False) is Disposition.DELETE

    def test_a_terminal_run_is_deleted_even_if_a_thread_is_still_tracked(self):
        """Terminal wins over in-flight, and the order is deliberate.

        A thread whose run is already terminal has nothing left to cancel; the
        registry entry is a leftover. Checking in_flight first would turn a
        delete into a cancel that never completes.
        """
        assert rules.plan_disposition(status="complete", in_flight=True) is Disposition.DELETE

    def test_a_gated_run_releases_the_gate(self):
        assert (rules.plan_disposition(status="awaiting_oversight", in_flight=True)
                is Disposition.RELEASE_GATE)

    def test_a_tracked_run_is_signalled(self):
        assert (rules.plan_disposition(status="running", in_flight=True)
                is Disposition.CANCEL_IN_FLIGHT)

    def test_an_untracked_running_run_is_marked_failed(self):
        """The database says running, this process never heard of it.

        That is a restart. Nothing will ever finish the run, so leaving it
        "running" would keep a ghost in the list forever.
        """
        assert (rules.plan_disposition(status="running", in_flight=False)
                is Disposition.MARK_FAILED)

    def test_pending_without_a_thread_is_refused(self):
        with pytest.raises(RunNotCancellable) as e:
            rules.plan_disposition(status="pending", in_flight=False)
        assert e.value.status == "pending"


class TestOwnership:
    def test_the_owner_passes(self):
        actor = uuid4()
        rules.check_ownership(owner_id=actor, actor_id=actor)

    def test_a_stranger_is_refused(self):
        with pytest.raises(RunNotOwned):
            rules.check_ownership(owner_id=uuid4(), actor_id=uuid4())

    def test_a_run_without_an_owner_is_allowed(self):
        """Runs created before CHY-88 carry no owner.

        Refusing them would make every old run undeletable by anybody, while
        every run created from now on is owned and protected.
        """
        rules.check_ownership(owner_id=None, actor_id=uuid4())


class TestTheSmallGuards:
    def test_an_unknown_pilot_is_named_in_the_error(self):
        with pytest.raises(UnknownPilot) as e:
            rules.check_pilot_known("astrology")
        assert e.value.name == "astrology"

    @pytest.mark.parametrize("name", ["study", "banking"])
    def test_the_installed_pilots_pass(self, name):
        rules.check_pilot_known(name)

    @pytest.mark.parametrize("decision", ["approved", "rejected"])
    def test_the_two_decisions_pass(self, decision):
        rules.check_decision(decision)

    @pytest.mark.parametrize("decision", ["maybe", "", "APPROVED", "yes"])
    def test_anything_else_is_refused(self, decision):
        """Case included: "APPROVED" is not the value migration 014 stores."""
        with pytest.raises(InvalidDecision):
            rules.check_decision(decision)

    def test_the_gates_third_value_is_not_a_decision_anyone_can_submit(self):
        """"cancelled" releases the gate when a run is cancelled (CHY-153). Sent
        to the oversight endpoint it would be stored as a decision nobody made."""
        with pytest.raises(InvalidDecision):
            rules.check_decision("cancelled")

    def test_the_gate_is_only_open_while_awaiting(self):
        rules.check_awaiting_oversight("awaiting_oversight")
        with pytest.raises(NotAwaitingOversight) as e:
            rules.check_awaiting_oversight("running")
        assert e.value.status == "running"


class TestCoverage:
    def test_the_ratio_and_the_samples(self):
        rows = [{"covered": True, "question": f"q{i}"} for i in range(7)]
        rows += [{"covered": False, "question": f"u{i}"} for i in range(3)]
        out = rules.coverage_from_rows(uuid4(), rows)
        assert (out.total_questions, out.covered_questions) == (10, 7)
        assert out.coverage_ratio == 0.7
        assert out.uncovered_samples == ["u0", "u1", "u2"]

    def test_at_most_five_samples(self):
        rows = [{"covered": False, "question": f"u{i}"} for i in range(40)]
        assert len(rules.coverage_from_rows(uuid4(), rows).uncovered_samples) == 5

    def test_nothing_covered_is_zero_not_a_crash(self):
        rows = [{"covered": False, "question": "u"}]
        assert rules.coverage_from_rows(uuid4(), rows).coverage_ratio == 0.0


class TestRegistry:
    """Six module-level dicts became one object; these are the transitions
    that were previously only observable by starting a run."""

    def test_a_run_is_unknown_before_it_opens(self):
        r, run_id = RunRegistry(), uuid4()
        assert not r.is_live(run_id)
        assert not r.in_flight(run_id)

    def test_events_arrive_in_order_and_the_sentinel_ends_it(self):
        r, run_id = RunRegistry(), uuid4()
        r.open(run_id)
        r.push(run_id, {"type": "a"})
        r.push(run_id, {"type": "b"})
        assert not r.is_done(run_id)
        r.push(run_id, None)
        assert r.is_done(run_id)
        buffer, _ = r.buffers(run_id)
        assert [e["type"] for e in buffer] == ["a", "b"]

    def test_pushing_wakes_a_waiting_reader(self):
        r, run_id = RunRegistry(), uuid4()
        r.open(run_id)
        _, notify = r.buffers(run_id)
        assert not notify.is_set()
        r.push(run_id, {"type": "a"})
        assert notify.is_set()

    def test_forget_removes_everything(self):
        r, run_id = RunRegistry(), uuid4()
        r.open(run_id)
        r.open_oversight_gate(run_id)
        r.push(run_id, {"type": "a"})
        r.push(run_id, None)
        r.forget(run_id)
        assert not r.is_live(run_id)
        assert not r.is_done(run_id)
        assert not r.has_oversight_gate(run_id)

    def test_release_thread_keeps_the_story(self):
        """The thread's teardown must not take the event log with it.

        A client that connects after the run ended still has to see what
        happened — that is the reconnect case, and it is the common one.
        """
        r, run_id = RunRegistry(), uuid4()
        r.open(run_id)
        r.open_oversight_gate(run_id)
        r.push(run_id, {"type": "a"})
        r.push(run_id, None)
        r.release_thread(run_id)
        assert not r.in_flight(run_id)
        assert not r.has_oversight_gate(run_id)
        assert r.is_live(run_id)
        assert r.buffers(run_id)[0] == [{"type": "a"}]

    def test_a_decision_reaches_the_waiting_thread(self):
        r, run_id = RunRegistry(), uuid4()
        event, holder = r.open_oversight_gate(run_id)
        assert not event.is_set()
        assert r.decide_oversight(run_id, "approved") is True
        assert holder[0] == "approved"
        assert event.is_set()

    def test_the_result_is_written_before_the_event_is_set(self):
        """The thread reads the holder the moment it wakes.

        Setting the event first is a race that surfaces as an approved run
        behaving like a rejected one — the holder would still read None. The
        check watches the ordering rather than the outcome: a listener fires on
        set() and records what the holder said at that instant.
        """
        r, run_id = RunRegistry(), uuid4()
        event, holder = r.open_oversight_gate(run_id)
        seen = []

        class Watched:
            def set(self):
                seen.append(holder[0])
                event.set()

        r._oversight_event[run_id] = Watched()
        r.decide_oversight(run_id, "rejected")
        assert seen == ["rejected"], "the event was set before the decision was written"

    def test_deciding_without_a_gate_reports_failure(self):
        r = RunRegistry()
        assert r.decide_oversight(uuid4(), "approved") is False


class TestReplay:
    """A run this process no longer holds is reconstructed from its row."""

    def test_a_persisted_log_is_replayed_as_is(self):
        row = {"status": "complete", "events_json": [{"type": "node_complete"}, {"type": DONE}],
               "result_json": None}
        assert [e["type"] for e in service._replay(row)] == ["node_complete", DONE]

    def test_a_decided_gate_comes_back_as_historical(self):
        """A client must not be offered buttons for a decision already made."""
        row = {"status": "complete", "events_json": [{"type": OVERSIGHT_REQUIRED, "x": 1}],
               "result_json": None}
        replayed = service._replay(row)
        assert replayed[0]["type"] == OVERSIGHT_HISTORICAL
        assert replayed[0]["x"] == 1, "the rewrite must keep the payload"

    def test_a_still_open_gate_stays_actionable(self):
        row = {"status": "awaiting_oversight", "events_json": [{"type": OVERSIGHT_REQUIRED}],
               "result_json": None}
        assert service._replay(row)[0]["type"] == OVERSIGHT_REQUIRED

    def test_a_run_older_than_migration_004_is_summarised(self):
        row = {"status": "complete", "events_json": None,
               "result_json": {"lernziel_erreicht": True, "iteration_count": 2,
                               "fakten": ["a", "b", "c"]}}
        [event] = service._replay(row)
        assert event["type"] == DONE
        assert event["facts_count"] == 3

    def test_a_failed_run_without_a_log_still_says_so(self):
        assert service._replay(
            {"status": "failed", "events_json": None, "result_json": None}
        )[0]["type"] == ERROR

    def test_anything_else_reports_the_bare_status(self):
        [event] = service._replay(
            {"status": "pending", "events_json": None, "result_json": None}
        )
        assert (event["type"], event["status"]) == (STATUS, "pending")

    def test_jsonb_arriving_as_text_is_parsed(self):
        """asyncpg hands jsonb back as str on some paths, as a list on others."""
        row = {"status": "complete", "events_json": '[{"type": "done"}]', "result_json": None}
        assert service._replay(row)[0]["type"] == DONE


class TestStreaming:
    """The heartbeat is a None tick, and that is the whole reason the split
    reaches into SSE at all: keeping a socket open is an HTTP concern, noticing
    that a run is quiet is not."""

    def test_a_quiet_run_yields_none_not_an_event(self):
        r, run_id = RunRegistry(), uuid4()
        r.open(run_id)

        pool = make_pool(fetchrow={"status": "running", "events_json": None, "result_json": None})

        async def drive():
            stream = service.stream_events(pool, stub_user(), run_id, registry=r,
                                           idle_timeout=0.01)
            first = await anext(stream)
            assert first is None, "an idle stream must tick, not invent an event"
            r.push(run_id, {"type": "node_complete"})
            assert (await anext(stream))["type"] == "node_complete"
            r.push(run_id, None)
            assert (await anext(stream))["type"] == EOF

        asyncio.run(drive())

    def test_a_missing_run_raises_before_anything_is_yielded(self):
        """Otherwise the 404 would be framed into a 200 response body.

        The transport primes the generator for exactly this reason; if the
        raise moved to a later step the endpoint would answer HTTP 200 with a
        body explaining that the run does not exist.
        """
        pool = make_pool(fetchrow=None)

        async def drive():
            with pytest.raises(RunNotFound):
                await anext(service.stream_events(pool, stub_user(), uuid4(),
                                                  registry=RunRegistry()))

        asyncio.run(drive())


class TestTheCodesAreUnchanged:
    """The numbers from before the move, written down."""

    @pytest.mark.parametrize("exc,expected", [
        (RunNotFound(), 404),
        (UnknownPilot("x"), 400),
        (InvalidDecision("maybe"), 400),
        (RunNotOwned(), 403),
        (RunNotCancellable("pending"), 409),
        (NotAwaitingOversight("running"), 409),
    ])
    def test_mapping(self, exc, expected):
        assert _PILOT_STATUS[type(exc)] == expected

    def test_every_context_error_is_mapped(self):
        """An unregistered error type would silently become 500.

        The handler does that on purpose — the pessimistic default is right.
        But it must not happen unnoticed, so this counts the family.
        """
        everything = set(PilotError.__subclasses__())
        missing = everything - set(_PILOT_STATUS)
        assert not missing, f"no status code for: {sorted(c.__name__ for c in missing)}"


class TestTheContextKnowsNoHTTP:
    """The actual gain, and the reason the Art. 14 decision is now reachable
    without speaking HTTP to it."""

    @pytest.mark.parametrize("module", [
        "run", "service", "store", "registry", "errors", "events",
        "schema", "execution",
    ])
    def test_no_import_of_fastapi_or_api(self, module):
        """Against the AST, not a grep.

        The docstrings tell the story of the move and therefore mention api/
        and HTTPException by name. Repeatedly this month a grep counted prose
        as code; this reads import statements only.
        """
        tree = ast.parse(
            pathlib.Path(f"contexts/pilots/{module}.py").read_text(encoding="utf-8")
        )
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
        offending = [m for m in imported
                     if m.split(".")[0] in {"fastapi", "api", "starlette"}]
        assert not offending, f"contexts/pilots/{module}.py imports {offending}"

    def test_the_oversight_decision_is_callable_without_a_request(self):
        """The one-sentence summary of what CHY-104 bought.

        Before the move, approving a banking run required an HTTP client. The
        signature below is the evidence that it no longer does: no Request, no
        Depends, no CurrentUser — a pool, a caller, an id and a decision.
        """
        import inspect
        params = list(inspect.signature(service.submit_oversight).parameters)
        assert params[:4] == ["pool", "caller", "run_id", "decision"]
