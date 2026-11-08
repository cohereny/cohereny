"""
contexts/pilots/registry.py

Process-local state of the runs currently in flight (ADR-030, CHY-104).

Six module-level dicts used to sit at the top of api/v1/pilots.py, keyed by
run_id and mutated from three directions: the request handlers, the SSE
generators, and the background thread through call_soon_threadsafe. Nothing
named the thing they collectively were, so nothing could be tested without
starting a run.

They are one object now. The database holds what a run *is*; this holds what
is happening to it right now in *this* process — which is why a restart loses
it, and why stream_events has a branch for exactly that.

Not part of the published interface: zone 1 reaches this through service.py.
Threading note, and the distinction matters: the background thread hands
*events* over with loop.call_soon_threadsafe, so appends and the reads in
stream_events are ordered against each other on one loop. Its own teardown
(release_thread) it calls directly, which is safe because dict.pop and
set.discard are single bytecode operations under the GIL and no reader depends
on their ordering.
"""
from __future__ import annotations

import asyncio
import threading
from uuid import UUID


class RunRegistry:
    """Everything about an in-flight run that does not survive a restart."""

    def __init__(self) -> None:
        # Set to stop the background thread at the next node boundary.
        self._cancel: dict[UUID, threading.Event] = {}

        # Oversight gate for banking runs waiting on a human decision.
        # The result holder is a one-element list so the API can write
        # "approved"/"rejected" — or "cancelled", when the run is cancelled
        # while it waits (CHY-153) — into it before setting the event; the
        # thread reads it immediately after waking.
        self._oversight_event: dict[UUID, threading.Event] = {}
        self._oversight_result: dict[UUID, list] = {}

        # Per-run event history plus a notification.
        # A list rather than a Queue: a page reload has to replay from the
        # beginning, and a Queue consumed once cannot do that.
        self._events: dict[UUID, list[dict]] = {}
        self._notify: dict[UUID, asyncio.Event] = {}
        self._done: set[UUID] = set()

    # ── registration ─────────────────────────────────────────────────────────

    def open(self, run_id: UUID) -> threading.Event:
        """Register a starting run; returns its cancel event."""
        self._notify[run_id] = asyncio.Event()
        cancel = threading.Event()
        self._cancel[run_id] = cancel
        return cancel

    def open_oversight_gate(self, run_id: UUID) -> tuple[threading.Event, list]:
        """Register the human-decision gate for a run that needs one."""
        event = threading.Event()
        result: list = [None]
        self._oversight_event[run_id] = event
        self._oversight_result[run_id] = result
        return event, result

    def forget(self, run_id: UUID) -> None:
        """Drop every trace of a run — after deletion, or after the stream ends."""
        self._events.pop(run_id, None)
        self._notify.pop(run_id, None)
        self._done.discard(run_id)
        self._oversight_event.pop(run_id, None)
        self._oversight_result.pop(run_id, None)

    def release_thread(self, run_id: UUID) -> None:
        """Drop what only the background thread needed, keeping the event log.

        Called from the thread's finally block. The events and the done marker
        stay so a client that connects after the run ends still gets the story.
        """
        self._cancel.pop(run_id, None)
        self._oversight_event.pop(run_id, None)
        self._oversight_result.pop(run_id, None)

    # ── events ───────────────────────────────────────────────────────────────

    def push(self, run_id: UUID, event: dict | None) -> None:
        """Append an event, or mark the run finished when event is None.

        Reached from the background thread via loop.call_soon_threadsafe, so it
        executes on the main loop like every other mutation here.
        """
        if event is None:
            self._done.add(run_id)
        else:
            self._events.setdefault(run_id, []).append(event)
        notify = self._notify.get(run_id)
        if notify:
            notify.set()

    def is_live(self, run_id: UUID) -> bool:
        """True if this process knows the run — running, or finished and not yet drained."""
        return run_id in self._notify or run_id in self._done

    def is_done(self, run_id: UUID) -> bool:
        return run_id in self._done

    def buffers(self, run_id: UUID) -> tuple[list[dict], asyncio.Event]:
        """The event list and notification for a stream, creating them if absent.

        Each SSE connection keeps its own cursor into the returned list, which
        is append-only until forget(); that is what makes several concurrent
        connections to one run safe.
        """
        return (
            self._events.setdefault(run_id, []),
            self._notify.setdefault(run_id, asyncio.Event()),
        )

    # ── cancellation and oversight ───────────────────────────────────────────

    def cancel_signal(self, run_id: UUID) -> threading.Event | None:
        return self._cancel.get(run_id)

    def in_flight(self, run_id: UUID) -> bool:
        """True if a background thread for this run is tracked in this process."""
        return run_id in self._cancel

    def has_oversight_gate(self, run_id: UUID) -> bool:
        """True if a thread is waiting for a decision on this run.

        The gate lives in this process. A restart loses it while the database
        row keeps saying "awaiting_oversight", and a decision recorded for a
        run nothing is waiting on would be a decision that never takes effect.
        """
        return run_id in self._oversight_event

    def decide_oversight(self, run_id: UUID, decision: str) -> bool:
        """Hand a decision to the waiting thread. False if the gate is gone.

        The result is written before the event is set, never after: the thread
        reads the holder the moment it wakes, and the reverse order is a race
        that would surface as an approved run behaving like a rejected one.
        """
        event = self._oversight_event.get(run_id)
        if event is None:
            return False
        self._oversight_result.setdefault(run_id, [None])[0] = decision
        event.set()
        return True


#: The registry of this process. Tests build their own instead of clearing this.
registry = RunRegistry()
