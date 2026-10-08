"""
gldb/audit.py
-------------
Append-only audit log for the gldb-prototype.

Rules:
- log_event() appends exactly one JSON line per call.  The log is NEVER
  edited or deleted by this module.
- read_events() replays the log filtered by case_id.
- AUDIT_LOG_PATH is read from the environment at call time so tests can
  redirect the log to a temporary file by setting AUDIT_LOG_PATH before
  calling these functions.
- Personal identifiers (NRIC/FIN, passport, TIN, SSN) must NOT appear in
  the details dict passed to log_event().  The caller is responsible.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from gldb.schemas import AuditEvent


def _log_path() -> Path:
    """Read AUDIT_LOG_PATH from the environment at call time.

    Defaults to the value in gldb.config if the env var is not set.
    Reading at call time (not import time) lets tests override the path
    by setting the environment variable before calling log_event().
    """
    raw = os.environ.get("AUDIT_LOG_PATH", "").strip()
    if not raw:
        # Import lazily to avoid circular-import risk and allow env override
        from gldb.config import AUDIT_LOG_PATH  # noqa: PLC0415
        return Path(AUDIT_LOG_PATH)
    return Path(raw)


def log_event(event: AuditEvent) -> None:
    """Append *event* as a single JSON line to the audit log.

    Creates the log file and any missing parent directories if needed.
    Opens the file in append mode only — never truncates or overwrites.

    Args:
        event: The AuditEvent to persist.
    """
    path = _log_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    line = event.model_dump_json() + "\n"

    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def read_all_events() -> list[AuditEvent]:
    """Return every AuditEvent in the log, in file order.

    Reads the file at the current AUDIT_LOG_PATH.  Lines that fail to parse
    are silently skipped so a corrupt line does not break the reader.
    """
    path = _log_path()
    if not path.exists():
        return []

    events: list[AuditEvent] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(AuditEvent.model_validate(json.loads(line)))
            except Exception:  # noqa: BLE001
                continue  # skip malformed lines without crashing

    return events


def read_events(case_id: str) -> list[AuditEvent]:
    """Return all AuditEvents from the log that match *case_id*, in file order."""
    return [ev for ev in read_all_events() if ev.case_id == case_id]
