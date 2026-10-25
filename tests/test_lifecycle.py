"""
tests/test_lifecycle.py
-----------------------
Tests for gldb/lifecycle.py: case status derived by replaying audit events
(event sourcing), legal transitions, flag dispositions, and maker-checker.
"""

from __future__ import annotations

from typing import Literal

import pytest

from gldb import lifecycle
from gldb.schemas import AuditAction, AuditEvent, CaseStatus, Evidence


def _event(
    action: AuditAction, decision: str | None = None, actor: str = "ana",
    evidence_id: str | None = None,
) -> AuditEvent:
    return AuditEvent(case_id="c", actor=actor, action=action, decision=decision, evidence_id=evidence_id)


ASSESSED = [_event("case_created"), _event("pipeline_completed")]


def _ev(
    evidence_id: str,
    severity: Literal["info", "medium"] = "medium",
    category: Literal["fraud_signal", "compliance"] = "fraud_signal",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id, check_id="X", category=category, severity=severity,
        document="d", page=1, method="deterministic", confidence=1.0, explanation="x",
    )


def test_new_case_is_assessed_with_the_four_analyst_actions() -> None:
    assert lifecycle.status(ASSESSED) == "assessed"
    assert lifecycle.allowed_actions("assessed") == ["approve", "decline", "escalate", "request_info"]


@pytest.mark.parametrize(("decision", "status"), [
    ("approve", "approved"), ("decline", "declined"),
    ("request_info", "info_requested"), ("escalate", "escalated"),
])
def test_analyst_decisions_move_the_case(decision: str, status: str) -> None:
    assert lifecycle.status([*ASSESSED, _event("analyst_decision", decision)]) == status


def test_request_info_then_new_documents_reassess_the_case() -> None:
    events = [*ASSESSED, _event("analyst_decision", "request_info"), _event("documents_added")]
    assert lifecycle.status(events) == "info_requested"  # until the pipeline re-runs
    assert lifecycle.status([*events, _event("pipeline_completed")]) == "assessed"


def test_escalation_path_through_mlro_back_to_a_final_decision() -> None:
    events = [*ASSESSED, _event("analyst_decision", "escalate"), _event("mlro_decision", "no_str", actor="mo")]
    assert lifecycle.status(events) == "mlro_reviewed"
    assert lifecycle.allowed_actions("mlro_reviewed") == ["approve", "decline"]
    assert lifecycle.status([*events, _event("analyst_decision", "decline")]) == "declined"


def test_final_states_allow_nothing() -> None:
    assert lifecycle.allowed_actions("approved") == []
    assert lifecycle.allowed_actions("declined") == []


@pytest.mark.parametrize(("status", "action"), [
    ("declined", "approve"), ("assessed", "documents_added"),
    ("assessed", "mlro_decision"), ("escalated", "approve"),
])
def test_illegal_transitions_are_refused(status: CaseStatus, action: str) -> None:
    with pytest.raises(lifecycle.TransitionError, match=f"cannot {action} a case that is {status}"):
        lifecycle.require(status, action)


def test_enhanced_review_is_not_approved_before_the_mlro_has_reviewed_it() -> None:
    assert lifecycle.allowed_actions("assessed", edd_required=True) == ["decline", "escalate", "request_info"]
    assert lifecycle.allowed_actions("mlro_reviewed", edd_required=True) == ["approve", "decline"]
    assert lifecycle.allowed_actions("assessed", edd_required=False) == ["approve", "decline", "escalate", "request_info"]
    with pytest.raises(lifecycle.TransitionError, match="enhanced review.*MLRO"):
        lifecycle.require("assessed", "approve", edd_required=True)
    lifecycle.require("assessed", "decline", edd_required=True)  # the cautious way is never held back
    lifecycle.require("assessed", "escalate", edd_required=True)
    lifecycle.require("mlro_reviewed", "approve", edd_required=True)
    lifecycle.require("assessed", "approve")  # any other case: the analyst may approve


def test_legacy_final_decision_events_still_replay() -> None:
    assert lifecycle.status([*ASSESSED, _event("final_decision", "rejected")]) == "declined"


def test_open_flags_count_only_decisions_since_the_latest_assessment() -> None:
    evidence = [_ev("E01"), _ev("E02"), _ev("E03", severity="info"), _ev("E04", category="compliance")]
    events = [*ASSESSED, _event("flag_confirmed", evidence_id="E01")]
    assert lifecycle.open_flags(evidence, events) == ["E02"]
    rerun = [*events, _event("documents_added"), _event("pipeline_completed")]
    assert lifecycle.open_flags(evidence, rerun) == ["E01", "E02"]


def test_escalated_by_names_the_maker() -> None:
    events = [*ASSESSED, _event("analyst_decision", "escalate", actor="alice")]
    assert lifecycle.escalated_by(events) == "alice"
    assert lifecycle.escalated_by(ASSESSED) is None
