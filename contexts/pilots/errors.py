"""
contexts/pilots/errors.py

What can go wrong in the pilots context, expressed without HTTP (ADR-030, CHY-104).

Same reasoning as kernel/errors.py, one zone further out: api/v1/pilots.py
raised HTTPException twelve times, and every one of those raises put a status
code decision inside a rule. That made the rules unusable outside a request —
a cancellation could only be decided by an HTTP handler, and the oversight
decision the EU AI Act Art. 14 record depends on could not be triggered from
anywhere else at all.

The status codes are unchanged. Only the place where they are chosen has
moved: api/main.py, where HTTP exists.
"""
from __future__ import annotations

from uuid import UUID


class PilotError(Exception):
    """Base class, so the transport layer can catch the family in one handler."""


class RunNotFound(PilotError):
    """No run with this id.

    Also raised for an input file outside the input directory — deliberately.
    See run.contain_input_file: a caller probing for /etc/passwd must not learn
    from the response whether it exists.
    """

    def __init__(self, what: str = "Run not found") -> None:
        super().__init__(what)


class UnknownPilot(PilotError):
    """The requested pilot name is not one this deployment knows."""

    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Unknown pilot: {name}")


class RunNotOwned(PilotError):
    """The run belongs to a different actor.

    Runs created before CHY-88 carry no owner and are exempt — refusing them
    would make old runs undeletable by anybody.
    """

    def __init__(self) -> None:
        super().__init__("This run belongs to another actor.")


class RunNotCancellable(PilotError):
    """The run is in a state from which neither cancel nor delete makes sense."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Run is not cancellable (status: {status})")


class RunHasTransparencyRecords(PilotError):
    """Deleting the run would take EU AI Act Art. 50 evidence with it.

    Migration 015 turned that foreign key into RESTRICT, so the database
    already refuses. This exists to explain the refusal rather than let a
    constraint violation surface as a 500.
    """

    def __init__(self) -> None:
        super().__init__(
            "This run has transparency records attached (EU AI Act Art. 50) "
            "and cannot be deleted. They document a disclosure that was made "
            "and outlive the run itself."
        )


class NotAwaitingOversight(PilotError):
    """An oversight decision was submitted for a run that is not waiting for one."""

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"Run is not awaiting oversight (status: {status})")


class OversightGateGone(PilotError):
    """The run waits for a decision, but the in-process gate no longer exists.

    The gate lives in the API process. A restart loses it while the database
    row keeps saying "awaiting_oversight" — the run is then unreachable and
    saying so is more useful than a generic conflict.
    """

    def __init__(self) -> None:
        super().__init__("Oversight gate not found — server may have restarted")


class InvalidDecision(PilotError):
    """An oversight decision other than approved/rejected."""

    def __init__(self, decision: str) -> None:
        self.decision = decision
        super().__init__("decision must be 'approved' or 'rejected'")


class NoCoverageData(PilotError):
    """The run has no question_bank coverage — a banking run, or none ingested."""

    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__("No coverage data for this run")


class QuestionNotFound(PilotError):
    """No question with this id in the bank."""

    def __init__(self) -> None:
        super().__init__("Question not found")


class UnknownPilotConfig(PilotError):
    """A run override names a configuration section that does not exist.

    Almost always a typo — `model_maps` for `model_map`. Refused rather than
    ignored: an unknown key that is silently dropped leaves the caller
    believing their setting is in force, and the run then behaves like the
    default for a reason nothing records.
    """

    def __init__(self, section: str, known: tuple[str, ...]) -> None:
        self.section = section
        super().__init__(
            f"Unknown configuration section {section!r}. Expected one of: "
            f"{', '.join(known)}."
        )


class ConfigSectionNotEffective(PilotError):
    """A run override names a section that is stored but takes no effect yet.

    Distinct from UnknownPilotConfig on purpose: this one is a "not yet" with a
    ticket behind it, not a mistake. `params` lives in workflow_configs and is
    served by the API, but the pilots still read their own constants — ADR-028
    keeps them out of scope for rebuilding until the runner of CHY-82 replaces
    them.

    Accepting it would be the worse answer. The caller would see 202, the run
    would ignore the value, and pilot_runs.effective_config would not record it
    either, because a record of a value that did nothing is a false record.
    """

    def __init__(self, section: str) -> None:
        self.section = section
        super().__init__(
            f"Configuration section {section!r} can be stored and read, but does "
            f"not yet change what a run does — the pilots still use their own "
            f"constants (ADR-028). Overriding it per run would have no effect, "
            f"so it is refused rather than ignored. See CHY-82."
        )
