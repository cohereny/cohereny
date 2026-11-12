"""
core/app_log.py

The application log: one JSON object per line on stderr (CHY-97).

Until CHY-97 the service logged next to nothing. A request that ended in a 500
left the status code and no reason, and a Redis that had stopped answering
showed only as everything getting slower.

What a line carries:

  ts, level, logger, message   always
  request_id, actor_id         when the line was written inside a call; added
                               by the transport's filter, which knows the call
  any `extra=` field           as a field of its own, under its own name
  exception                    the traceback, when one was logged

What it never carries is content: no prompt, no answer, no document text, no
value from a run. A line names things by id and counts them. That is a rule for
whoever writes a log call (CONVENTIONS.md), not something this formatter could
enforce, because it cannot tell a name from a value.

stderr, not stdout, because that is where the server writes its own lines, and
one stream keeps their order. Docker collects both.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

#: How install() recognises its own handler, so a second call does not write
#: every line twice.
HANDLER_NAME = "cohereny-app-log"

#: Attributes every LogRecord has. Whatever else a record carries came in
#: through `extra=` and is written as a field of its own. Read from a real
#: record, so a Python release that adds one (3.12 added taskName) does not
#: turn it into a field.
_STANDARD = frozenset(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {
    "message", "asctime",
    # uvicorn's second copy of its message, with terminal colour codes in it.
    # Seen in the first run against a real server, 09.11.2026.
    "color_message",
}


class JsonFormatter(logging.Formatter):
    """One record, one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        entry.update(
            (key, value) for key, value in vars(record).items()
            if key not in _STANDARD and not key.startswith("_")
        )
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        # default=str: a UUID or a datetime in `extra` is written, not refused.
        # A log call that raises would hide the very failure it reports.
        return json.dumps(entry, default=str, ensure_ascii=False)


def install(level: str, *filters: logging.Filter) -> None:
    """Send every record at `level` and above to stderr as JSON.

    On the root logger, so a library's records are written the same way as
    ours. Safe to call twice: the level is set again, the handler added once.
    Handlers it did not add stay where they are; a test's log capture is one.
    """
    root = logging.getLogger()
    root.setLevel(level)
    if any(handler.get_name() == HANDLER_NAME for handler in root.handlers):
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.set_name(HANDLER_NAME)
    handler.setFormatter(JsonFormatter())
    for log_filter in filters:
        handler.addFilter(log_filter)
    root.addHandler(handler)
