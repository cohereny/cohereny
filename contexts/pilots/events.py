"""
contexts/pilots/events.py

What a pilot run reports while it happens (ADR-030, CHY-104).

These strings were literals scattered across the executor, the SSE generator
and the cancel endpoint, which is how `oversight_required` came to be rewritten
to `oversight_historical` in one place and nowhere else. One name, one spelling.

They are the wire vocabulary the agent UI switches on, so renaming one is a
breaking change to a consumer — that is why they are constants and not an enum
serialised by name.
"""
from __future__ import annotations

#: A workflow node finished; carries the node's output summary.
NODE_COMPLETE = "node_complete"

#: A long node reports partial progress.
NODE_PROGRESS = "node_progress"

#: The run finished successfully. Carries the Art. 50 model disclosure.
DONE = "done"

#: The run failed. Carries a truncated traceback.
ERROR = "error"

#: The run ended without approval (EU AI Act Art. 14).
REJECTED = "rejected"

#: A banking run reached the gate and needs a human decision now.
OVERSIGHT_REQUIRED = "oversight_required"

#: The same gate, replayed after the fact. A client must not offer buttons for
#: a decision that has already been made.
OVERSIGHT_HISTORICAL = "oversight_historical"

#: A decision was submitted; live clients hide the card.
OVERSIGHT_SUBMITTED = "oversight_submitted"

#: Status of a run this process cannot say anything more detailed about.
STATUS = "status"

#: No further events will follow on this stream.
EOF = "eof"
