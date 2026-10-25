"""
gldb/lifecycle.py
-----------------
Case lifecycle as an event-sourced state machine.

There is no stored status. A case's status is derived by replaying its audit
events (gldb.audit), which are append-only, so the status cannot drift from
the record or be edited without leaving a trace.

    assessed --approve--> approved                (final)
    assessed --decline--> declined                (final)
    assessed --request_info--> info_requested --documents re-run--> assessed
    assessed --escalate--> escalated --MLRO decides--> mlro_reviewed
    mlro_reviewed --approve | decline--> final

Rules:
- Pure functions over AuditEvents; no I/O.
- require() refuses any action not allowed from the current status.
- A case on the enhanced review path (enhanced due diligence) cannot be
  approved from "assessed": the MLRO must review it first, which is the
  senior sign-off. Declining, escalating and asking for information stay open.
- A case cannot be approved while a fraud flag from the latest assessment
  is undecided (open_flags); declining is always allowed.
- The MLRO must be a different person from the analyst who escalated
  (escalated_by): segregation of duties.
"""

from __future__ import annotations

from gldb.schemas import AuditEvent, CaseStatus, Evidence

#: Actions allowed from each status
_ALLOWED: dict[CaseStatus, frozenset[str]] = {
    "assessed": frozenset({"approve", "decline", "request_info", "escalate"}),
    "info_requested": frozenset({"documents_added"}),
    "escalated": frozenset({"mlro_decision"}),
    "mlro_reviewed": frozenset({"approve", "decline"}),
    "approved": frozenset(),
    "declined": frozenset(),
}

#: Status reached by each analyst decision
_DECISION_STATUS: dict[str, CaseStatus] = {
    "approve": "approved",
    "decline": "declined",
    "request_info": "info_requested",
    "escalate": "escalated",
}

#: Legacy final_decision values (before the lifecycle existed)
_LEGACY_STATUS: dict[str, CaseStatus] = {
    "approved": "approved",
    "rejected": "declined",
    "more_info_requested": "info_requested",
}


class TransitionError(Exception):
    """An action that the case's current status does not allow."""


def status(events: list[AuditEvent]) -> CaseStatus:
    """Replay *events* (one case, file order) into its current status."""
    current: CaseStatus = "assessed"
    for event in events:
        if event.action == "pipeline_completed":
            current = "assessed"
        elif event.action == "analyst_decision" and event.decision in _DECISION_STATUS:
            current = _DECISION_STATUS[event.decision]
        elif event.action == "mlro_decision":
            current = "mlro_reviewed"
        elif event.action == "final_decision" and event.decision in _LEGACY_STATUS:
            current = _LEGACY_STATUS[event.decision]
    return current


def _awaits_mlro(current: CaseStatus, action: str, edd_required: bool) -> bool:
    """Whether *action* is approval of an enhanced review case the MLRO has not reviewed."""
    return edd_required and current == "assessed" and action == "approve"


def allowed_actions(current: CaseStatus, edd_required: bool = False) -> list[str]:
    return sorted(a for a in _ALLOWED[current] if not _awaits_mlro(current, a, edd_required))


def require(current: CaseStatus, action: str, edd_required: bool = False) -> None:
    """Raise TransitionError unless *action* is allowed from *current*, for a
    case that is on the enhanced review path if *edd_required*."""
    if action not in _ALLOWED[current]:
        raise TransitionError(f"cannot {action} a case that is {current}")
    if _awaits_mlro(current, action, edd_required):
        raise TransitionError("enhanced review: the MLRO must review the case before it is approved")


def open_flags(evidence: list[Evidence], events: list[AuditEvent]) -> list[str]:
    """Fraud flags from the latest assessment that nobody has confirmed or
    dismissed yet. Decisions made before the latest pipeline run refer to an
    earlier evidence set (IDs are reassigned on re-run) and do not count."""
    last_run = max(
        (i for i, e in enumerate(events) if e.action == "pipeline_completed"), default=-1
    )
    decided = {
        e.evidence_id
        for e in events[last_run + 1:]
        if e.action in ("flag_confirmed", "flag_dismissed")
    }
    return [
        ev.evidence_id
        for ev in evidence
        if ev.category == "fraud_signal" and ev.severity != "info"
        and ev.evidence_id not in decided
    ]


def escalated_by(events: list[AuditEvent]) -> str | None:
    """Actor of the most recent escalation, if any."""
    escalations = [
        e.actor for e in events if e.action == "analyst_decision" and e.decision == "escalate"
    ]
    return escalations[-1] if escalations else None
