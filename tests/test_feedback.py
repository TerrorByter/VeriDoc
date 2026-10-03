"""
tests/test_feedback.py
----------------------
Tests for gldb/feedback.py: override statistics projected from the audit log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gldb import feedback
from gldb.audit import log_event
from gldb.schemas import AuditAction, AuditEvent, ReasonCode


def _flag(action: AuditAction, check_id: str, code: ReasonCode | None = None, case: str = "c1") -> AuditEvent:
    return AuditEvent(
        case_id=case, actor="ana", role="analyst", action=action, evidence_id="E01",
        reason_code=code, details={"check_id": check_id},
    )


def test_override_rates_per_check_most_overridden_first() -> None:
    events = [
        _flag("flag_dismissed", "FORENSIC_OCR_MISMATCH", "ocr_misread"),
        _flag("flag_dismissed", "FORENSIC_OCR_MISMATCH", "ocr_misread", case="c2"),
        _flag("flag_confirmed", "FORENSIC_OCR_MISMATCH", case="c3"),
        _flag("flag_confirmed", "XDOC_IDENTITY"),
        AuditEvent(case_id="c1", actor="system", action="pipeline_completed"),
    ]
    rows = feedback.report(events)
    assert [(r.check_id, r.confirmed, r.dismissed) for r in rows] == [
        ("FORENSIC_OCR_MISMATCH", 1, 2), ("XDOC_IDENTITY", 1, 0),
    ]
    assert rows[0].dismiss_rate == pytest.approx(2 / 3)
    assert rows[0].reasons == {"ocr_misread": 2}


def test_cli_reads_the_whole_audit_log(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    log_event(_flag("flag_dismissed", "META_PRODUCER_MISMATCH", "legitimate_explanation"))
    log_event(_flag("flag_dismissed", "META_PRODUCER_MISMATCH", "legitimate_explanation", case="c9"))
    feedback.main()
    out = capsys.readouterr().out
    assert "META_PRODUCER_MISMATCH" in out and "100%" in out and "legitimate_explanation: 2" in out
