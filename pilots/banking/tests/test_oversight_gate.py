"""Unit tests for the human oversight gate (EU AI Act Art. 14, ADR-004).

The gate had two fallbacks to "approved" (CHY-83): a run without a registered
gate approved itself, and a two-hour timeout counted as consent. Both are
tested here as regressions — the point of every test in this file is that
nothing except an explicit human "approved" ever produces an approval.

All heavy dependencies are mocked in conftest.py; no LLM stack required.
"""
import threading

import pytest

from banking.nodes import (
    CANCELLED_AT_GATE,
    OVERSIGHT_TIMEOUT_SECONDS,
    OversightGateError,
    WorkflowCancelledError,
    node_human_oversight,
    set_emit,
    set_oversight_gate,
    set_oversight_prompt,
    wait_for_oversight_decision,
)


@pytest.fixture(autouse=True)
def _clean_gate():
    """Every test starts and ends without a gate on this thread."""
    set_oversight_gate(None, None)
    set_oversight_prompt(None)
    set_emit(None)
    yield
    set_oversight_gate(None, None)
    set_oversight_prompt(None)
    set_emit(None)


def _decided_gate(decision):
    """An event-based gate that has already been answered."""
    event = threading.Event()
    holder = [decision]
    event.set()
    return event, holder


# ── No gate registered ────────────────────────────────────────────────────────

class TestMissingGate:
    def test_no_gate_raises_instead_of_approving(self):
        """CHY-83 (1): a missing gate used to return "approved"."""
        with pytest.raises(OversightGateError) as exc:
            wait_for_oversight_decision()
        assert "no automatic fallback" in str(exc.value).lower()

    def test_no_gate_never_returns_a_decision(self):
        """Whatever happens, it must not be a value the node reads as consent."""
        try:
            result = wait_for_oversight_decision()
        except OversightGateError:
            return
        pytest.fail(f"expected OversightGateError, got {result!r}")

    def test_node_propagates_missing_gate(self):
        with pytest.raises(OversightGateError):
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})


# ── Timeout ───────────────────────────────────────────────────────────────────

class TestTimeout:
    def test_timeout_reports_timeout_not_approval(self, monkeypatch):
        """CHY-83 (2): an expired wait used to return "approved"."""
        monkeypatch.setattr("banking.nodes.OVERSIGHT_TIMEOUT_SECONDS", 0.01)
        set_oversight_gate(threading.Event(), [None])  # never set

        assert wait_for_oversight_decision() == "timeout"

    def test_timeout_with_empty_holder_is_not_approval(self, monkeypatch):
        """The old code did `holder[0] or "approved"` — None must not mean yes."""
        monkeypatch.setattr("banking.nodes.OVERSIGHT_TIMEOUT_SECONDS", 0.01)
        set_oversight_gate(threading.Event(), [None])

        assert wait_for_oversight_decision() != "approved"

    def test_node_cancels_run_on_timeout(self, monkeypatch):
        monkeypatch.setattr("banking.nodes.OVERSIGHT_TIMEOUT_SECONDS", 0.01)
        set_oversight_gate(threading.Event(), [None])

        with pytest.raises(WorkflowCancelledError) as exc:
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})

        # api/v1/pilots.py keys the "rejected" branch on this attribute, and the
        # legacy string match must keep working for callers that predate it.
        assert exc.value.oversight_outcome == "timeout"
        assert "rejected by human oversight" in str(exc.value).lower()

    def test_timeout_is_distinguishable_from_a_human_rejection(self, monkeypatch):
        """Both end the run, but the audit trail must tell them apart."""
        monkeypatch.setattr("banking.nodes.OVERSIGHT_TIMEOUT_SECONDS", 0.01)
        set_oversight_gate(threading.Event(), [None])
        with pytest.raises(WorkflowCancelledError) as timed_out:
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})

        set_oversight_gate(*_decided_gate("rejected"))
        with pytest.raises(WorkflowCancelledError) as refused:
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})

        assert timed_out.value.oversight_outcome == "timeout"
        assert refused.value.oversight_outcome == "rejected"

    def test_timeout_emits_an_event(self, monkeypatch):
        monkeypatch.setattr("banking.nodes.OVERSIGHT_TIMEOUT_SECONDS", 0.01)
        set_oversight_gate(threading.Event(), [None])
        events = []
        set_emit(events.append)

        with pytest.raises(WorkflowCancelledError):
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})

        assert any(e.get("type") == "oversight_timeout" for e in events)

    def test_default_timeout_is_two_hours(self):
        """A shortened timeout in tests must not hide a changed default."""
        assert OVERSIGHT_TIMEOUT_SECONDS == 7200


# ── Valid decisions ───────────────────────────────────────────────────────────

class TestDecisions:
    def test_approved_passes_through(self):
        set_oversight_gate(*_decided_gate("approved"))
        assert wait_for_oversight_decision() == "approved"

    def test_rejected_passes_through(self):
        set_oversight_gate(*_decided_gate("rejected"))
        assert wait_for_oversight_decision() == "rejected"

    def test_node_continues_after_approval(self):
        set_oversight_gate(*_decided_gate("approved"))
        result = node_human_oversight({"risk_level": "GRUEN", "ratios": {}, "anomalies": []})
        assert result["oversight_required"] is True
        assert "decision=approved" in result["audit_log"][-1]

    def test_node_raises_on_rejection(self):
        set_oversight_gate(*_decided_gate("rejected"))
        with pytest.raises(WorkflowCancelledError) as exc:
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})
        assert exc.value.oversight_outcome == "rejected"

    def test_cancelled_passes_through(self):
        set_oversight_gate(*_decided_gate("cancelled"))
        assert wait_for_oversight_decision() == "cancelled"

    def test_a_run_cancelled_at_the_gate_ends_without_an_oversight_outcome(self):
        """CHY-153: the cancel path released the gate with "rejected", and the
        run was booked as an Art. 14 rejection. Without an outcome the executor
        ends it the way it ends every cancelled run — and its message must not
        contain the phrase the executor still reads as a rejection."""
        set_oversight_gate(*_decided_gate("cancelled"))
        with pytest.raises(WorkflowCancelledError) as exc:
            node_human_oversight({"risk_level": "ROT", "ratios": {}, "anomalies": []})
        assert exc.value.oversight_outcome is None
        assert str(exc.value) == CANCELLED_AT_GATE
        assert "rejected by human oversight" not in str(exc.value).lower()

    @pytest.mark.parametrize("garbage", [None, "", "yes", "APPROVED", "maybe", 1, True])
    def test_unusable_value_raises_instead_of_approving(self, garbage):
        """A gate that answers with nonsense is a fault, not a yes."""
        set_oversight_gate(*_decided_gate(garbage))
        with pytest.raises(OversightGateError):
            wait_for_oversight_decision()


# ── Interactive prompt (CLI path) ─────────────────────────────────────────────

class TestPrompt:
    def test_prompt_supplies_the_decision(self):
        set_oversight_prompt(lambda: "approved")
        assert wait_for_oversight_decision() == "approved"

    def test_prompt_rejection_is_honoured(self):
        set_oversight_prompt(lambda: "rejected")
        assert wait_for_oversight_decision() == "rejected"

    def test_prompt_takes_precedence_over_event_gate(self):
        """A CLI run must not be silently answered by a stale event holder."""
        set_oversight_gate(*_decided_gate("approved"))
        set_oversight_prompt(lambda: "rejected")
        assert wait_for_oversight_decision() == "rejected"

    def test_prompt_returning_garbage_raises(self):
        set_oversight_prompt(lambda: "sure, why not")
        with pytest.raises(OversightGateError):
            wait_for_oversight_decision()

    def test_prompt_exception_propagates(self):
        """No terminal means no decision — and no decision must not mean yes."""
        def refuse():
            raise OversightGateError("stdin is not a terminal")

        set_oversight_prompt(refuse)
        with pytest.raises(OversightGateError):
            wait_for_oversight_decision()
