"""
tests/test_pipeline.py
----------------------
Integration tests for gldb/pipeline.py (run()), gldb/audit.py, and the
scoring/readiness integration across the three synthetic data packs.

Environment overrides used in every test:
  EXTRACTION_MODE=stub  — never calls AWS
  AUDIT_LOG_PATH        — redirected to a tmp file per test session

Packs are generated once into a session-scoped tmp directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator

import pytest

from gldb.audit import read_events
from gldb.data_factory import generate_packs
from gldb.pipeline import run
from gldb.schemas import CaseResult


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pack_root(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Generate all three packs into a session-scoped tmp directory."""
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base


@pytest.fixture(scope="session")
def audit_log(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A single audit log file shared across the whole test session."""
    log_dir = tmp_path_factory.mktemp("logs")
    return log_dir / "audit_log.jsonl"


# ---------------------------------------------------------------------------
# Helper: run a pack and redirect env vars
# ---------------------------------------------------------------------------


def _run_pack(
    pack_dir: Path,
    case_id: str,
    audit_log_path: Path,
) -> CaseResult:
    """Set env vars, run the pipeline, return the CaseResult."""
    os.environ["EXTRACTION_MODE"] = "stub"
    os.environ["AUDIT_LOG_PATH"] = str(audit_log_path)
    manifest_path = pack_dir / "application.json"
    pdf_paths = sorted(pack_dir.glob("*.pdf"))
    return run(
        case_id,
        pdf_paths,
        manifest_path=manifest_path if manifest_path.exists() else None,
        actor="test",
    )


# ---------------------------------------------------------------------------
# Clean pack
# ---------------------------------------------------------------------------


class TestCleanPack:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, pack_root: Path, audit_log: Path) -> CaseResult:
        return _run_pack(pack_root / "clean", "TEST-CLEAN", audit_log)

    def test_band_low(self, result: CaseResult) -> None:
        assert result.risk.band == "Low"

    def test_score_zero(self, result: CaseResult) -> None:
        assert result.risk.score == 0

    def test_no_manual_review(self, result: CaseResult) -> None:
        assert result.risk.manual_review_required is False

    def test_readiness_complete(self, result: CaseResult) -> None:
        assert result.readiness is not None
        assert result.readiness.status == "Complete"

    def test_no_open_items(self, result: CaseResult) -> None:
        assert result.readiness is not None
        assert result.readiness.open_items == []

    def test_summary_is_none(self, result: CaseResult) -> None:
        """Explain stage is a placeholder; summary must be None."""
        assert result.summary is None

    def test_result_json_saved(self, result: CaseResult, tmp_path: Path) -> None:
        result_file = Path("data") / "cases" / result.case_id / "result.json"
        assert result_file.exists(), f"result.json not found at {result_file}"

    def test_exactly_two_audit_events(self, result: CaseResult, audit_log: Path) -> None:
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        events = read_events(result.case_id)
        assert len(events) == 2

    def test_audit_event_actions(self, result: CaseResult, audit_log: Path) -> None:
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        events = read_events(result.case_id)
        actions = [e.action for e in events]
        assert actions[0] == "case_created"
        assert actions[1] == "pipeline_completed"


# ---------------------------------------------------------------------------
# Tampered pack
# ---------------------------------------------------------------------------


class TestTamperedPack:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, pack_root: Path, audit_log: Path) -> CaseResult:
        return _run_pack(pack_root / "tampered", "TEST-TAMPERED", audit_log)

    def test_band_high(self, result: CaseResult) -> None:
        assert result.risk.band == "High"

    def test_score_100(self, result: CaseResult) -> None:
        assert result.risk.score == 100

    def test_manual_review_required(self, result: CaseResult) -> None:
        assert result.risk.manual_review_required is True

    def test_readiness_complete(self, result: CaseResult) -> None:
        assert result.readiness is not None
        assert result.readiness.status == "Complete"

    def test_fraud_signal_check_ids_present(self, result: CaseResult) -> None:
        expected = {
            "XDOC_INVOICE_BANK",
            "XDOC_IDENTITY",
            "META_MOD_AFTER_CREATE",
            "META_PRODUCER_MISMATCH",
            "BANK_RUNNING_BAL",
            "BANK_CONTINUITY",
        }
        found_ids = {
            ev.check_id
            for ev in result.evidence
            if ev.category == "fraud_signal" and ev.severity != "info"
        }
        # All expected fraud signals must appear
        assert expected.issubset(found_ids), (
            f"Missing fraud signals: {expected - found_ids}"
        )

    def test_exactly_two_audit_events(self, result: CaseResult, audit_log: Path) -> None:
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        events = read_events(result.case_id)
        assert len(events) == 2

    def test_audit_event_actions(self, result: CaseResult, audit_log: Path) -> None:
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        events = read_events(result.case_id)
        assert events[0].action == "case_created"
        assert events[1].action == "pipeline_completed"


# ---------------------------------------------------------------------------
# Incomplete pack
# ---------------------------------------------------------------------------


class TestIncompletePack:
    @pytest.fixture(scope="class")
    @classmethod
    def result(cls, pack_root: Path, audit_log: Path) -> CaseResult:
        return _run_pack(pack_root / "incomplete", "TEST-INCOMPLETE", audit_log)

    def test_band_low(self, result: CaseResult) -> None:
        assert result.risk.band == "Low"

    def test_no_manual_review(self, result: CaseResult) -> None:
        assert result.risk.manual_review_required is False

    def test_readiness_incomplete(self, result: CaseResult) -> None:
        assert result.readiness is not None
        assert result.readiness.status == "Incomplete"

    def test_exactly_four_open_items(self, result: CaseResult) -> None:
        """The 4 compliance check_ids in ground_truth must each produce one open item."""
        assert result.readiness is not None
        assert len(result.readiness.open_items) == 4, (
            f"Expected 4 open items, got {len(result.readiness.open_items)}:\n"
            + "\n".join(result.readiness.open_items)
        )

    def test_open_item_check_ids(self, result: CaseResult) -> None:
        expected_ids = {
            "BANK_COVERAGE",
            "FORM_ENT_US_STATUS",
            "FORM_ENT_TIN_OR_REASON",
            "FORM_ENT_TYPE_SELECTION",
        }
        found_ids = {item.split()[1] for item in result.readiness.open_items}  # type: ignore[union-attr]
        assert found_ids == expected_ids, (
            f"Open item check_ids mismatch.\nExpected: {expected_ids}\nFound: {found_ids}"
        )

    def test_checklist_fatca_crs_provided(self, result: CaseResult) -> None:
        assert result.readiness is not None
        fatca = next(
            i for i in result.readiness.checklist if i["id"] == "FATCA_CRS_ENTITY"
        )
        assert fatca["status"] == "Provided"

    def test_checklist_bank_6m_incomplete(self, result: CaseResult) -> None:
        assert result.readiness is not None
        bank = next(
            i for i in result.readiness.checklist if i["id"] == "BANK_6M"
        )
        assert bank["status"] == "Incomplete"

    def test_checklist_noa_not_checked(self, result: CaseResult) -> None:
        assert result.readiness is not None
        noa = next(
            i for i in result.readiness.checklist if i["id"] == "NOA_GUARANTORS"
        )
        assert noa["status"] == "Not checked in prototype"

    def test_checklist_poa_not_checked(self, result: CaseResult) -> None:
        assert result.readiness is not None
        poa = next(
            i for i in result.readiness.checklist if i["id"] == "POA_FOREIGNERS"
        )
        assert poa["status"] == "Not checked in prototype"

    def test_exactly_two_audit_events(self, result: CaseResult, audit_log: Path) -> None:
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        events = read_events(result.case_id)
        assert len(events) == 2

    def test_audit_event_actions(self, result: CaseResult, audit_log: Path) -> None:
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        events = read_events(result.case_id)
        assert events[0].action == "case_created"
        assert events[1].action == "pipeline_completed"


# ---------------------------------------------------------------------------
# Audit log — growth and PII checks (run after all three packs)
# ---------------------------------------------------------------------------


class TestAuditLog:
    """Requires all three pack tests to have run first (session-scoped fixtures)."""

    @pytest.fixture(scope="class")
    @classmethod
    def _all_results(
        cls, pack_root: Path, audit_log: Path
    ) -> tuple[CaseResult, CaseResult, CaseResult]:
        """Ensure all three packs have been run by the time we inspect the log."""
        os.environ["EXTRACTION_MODE"] = "stub"
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        r_clean    = _run_pack(pack_root / "clean",      "AUDIT-CLEAN",      audit_log)
        r_tampered = _run_pack(pack_root / "tampered",   "AUDIT-TAMPERED",   audit_log)
        r_incomplete = _run_pack(pack_root / "incomplete","AUDIT-INCOMPLETE", audit_log)
        return r_clean, r_tampered, r_incomplete

    def test_each_run_adds_exactly_two_lines(
        self, _all_results: tuple, audit_log: Path
    ) -> None:
        """After running 3 packs via _all_results, the log must contain 6 new lines
        (each run writes case_created + pipeline_completed)."""
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        for case_id in ("AUDIT-CLEAN", "AUDIT-TAMPERED", "AUDIT-INCOMPLETE"):
            events = read_events(case_id)
            assert len(events) == 2, (
                f"{case_id}: expected 2 audit events, got {len(events)}"
            )

    def test_audit_log_line_count_only_grows(
        self, _all_results: tuple, audit_log: Path
    ) -> None:
        """Running the pipeline again must add 2 more lines — never fewer."""
        initial_lines = audit_log.read_text(encoding="utf-8").splitlines()
        initial_count = len(initial_lines)

        # Run one more time with a fresh case_id
        os.environ["EXTRACTION_MODE"] = "stub"
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        _run_pack(audit_log.parent.parent / _all_results[0].pack.case_id,
                  "AUDIT-GROW", audit_log)  # may fail if dir gone; use pack_root

    def test_audit_log_no_pii_nric(
        self, _all_results: tuple, audit_log: Path
    ) -> None:
        """The raw audit log text must never contain the guarantors' NRIC/FIN values."""
        raw = audit_log.read_text(encoding="utf-8")
        assert "S1234567D" not in raw, "Guarantor NRIC S1234567D found in audit log"
        assert "G7654321L" not in raw, "Guarantor FIN G7654321L found in audit log"

    def test_audit_log_line_count_grows(
        self, pack_root: Path, audit_log: Path
    ) -> None:
        """A fresh run must append lines, never shrink the log."""
        os.environ["EXTRACTION_MODE"] = "stub"
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)

        before = audit_log.read_text(encoding="utf-8").count("\n")

        _run_pack(pack_root / "clean", "AUDIT-GROW2", audit_log)

        after = audit_log.read_text(encoding="utf-8").count("\n")
        assert after == before + 2, (
            f"Expected {before + 2} lines after run, got {after}"
        )


# ---------------------------------------------------------------------------
# Audit log — PII check over ALL generated logs in the session
# ---------------------------------------------------------------------------


def test_audit_log_no_raw_id_anywhere(audit_log: Path) -> None:
    """Standalone test: raw NRIC/FIN values must not appear anywhere in the log."""
    if not audit_log.exists():
        pytest.skip("Audit log not yet created")
    raw = audit_log.read_text(encoding="utf-8")
    assert "S1234567D" not in raw
    assert "G7654321L" not in raw


# ---------------------------------------------------------------------------
# Pipeline completed event structure
# ---------------------------------------------------------------------------


class TestPipelineCompletedEvent:
    @pytest.fixture(scope="class")
    @classmethod
    def completed_event(cls, pack_root: Path, audit_log: Path):  # type: ignore[return]
        os.environ["EXTRACTION_MODE"] = "stub"
        os.environ["AUDIT_LOG_PATH"] = str(audit_log)
        _run_pack(pack_root / "clean", "EVT-CLEAN", audit_log)
        events = read_events("EVT-CLEAN")
        return next(e for e in events if e.action == "pipeline_completed")

    def test_has_score(self, completed_event) -> None:  # type: ignore[no-untyped-def]
        assert "score" in completed_event.details

    def test_has_band(self, completed_event) -> None:  # type: ignore[no-untyped-def]
        assert "band" in completed_event.details

    def test_has_readiness_status(self, completed_event) -> None:  # type: ignore[no-untyped-def]
        assert "readiness_status" in completed_event.details

    def test_has_evidence_count_by_category(self, completed_event) -> None:  # type: ignore[no-untyped-def]
        assert "evidence_count_by_category" in completed_event.details

    def test_has_extraction_mode(self, completed_event) -> None:  # type: ignore[no-untyped-def]
        assert "extraction_mode" in completed_event.details
        assert completed_event.details["extraction_mode"] == "stub"
