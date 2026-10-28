"""
tests/test_scoring.py
---------------------
Unit tests for gldb/scoring.py.

These tests are fully deterministic and never call AWS.
All Evidence objects are constructed inline — no PDF parsing required.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from decimal import Decimal
from datetime import date

from gldb.schemas import (
    Address,
    ApplicationManifest,
    BankStatement,
    CaseResult,
    DocMeta,
    Evidence,
    ExtractedPack,
    FinancialStatementSummary,
    Guarantor,
    Invoice,
    NoticeOfAssessment,
    RiskResult,
    ReadinessResult,
)
from gldb.scoring import readiness, score


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ev(
    check_id: str,
    category: str,
    severity: str,
    *,
    evidence_id: str = "E01",
    explanation: str = "test finding",
    document: str = "test_doc",
) -> Evidence:
    """Minimal Evidence factory for tests."""
    return Evidence(
        evidence_id=evidence_id,
        check_id=check_id,
        category=category,          # type: ignore[arg-type]
        severity=severity,           # type: ignore[arg-type]
        document=document,
        page=1,
        method="deterministic",
        confidence=1.0,
        explanation=explanation,
    )


def _stub_bank_statement() -> BankStatement:
    """Minimal valid BankStatement for tests that only need a non-empty list."""
    return BankStatement(
        doc_id="stub_bs",
        bank_name="Test Bank",
        account_holder="Test Co",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        opening_balance=Decimal("0.00"),
        closing_balance=Decimal("0.00"),
        transactions=[],
    )


def _empty_pack(
    *,
    case_id: str = "test",
    entity_self_certs: list = [],
    has_bank_statements: bool = False,
    docs: list = [],
) -> ExtractedPack:
    """Minimal ExtractedPack factory for tests.

    Use ``has_bank_statements=True`` to signal that statements are present
    (scoring only checks truthiness, not the actual statement content).
    """
    bank_statements = [_stub_bank_statement()] if has_bank_statements else []
    return ExtractedPack(
        case_id=case_id,
        docs=docs,
        invoices=[],
        bank_statements=bank_statements,
        financial_statements=[],
        entity_self_certs=entity_self_certs,
        manifest=None,
        extraction_mode="stub",
    )


# ---------------------------------------------------------------------------
# score() — basic cases
# ---------------------------------------------------------------------------


class TestScoreEmpty:
    """Empty evidence list → Low band, score 0."""

    def test_score_zero(self) -> None:
        result = score([])
        assert result.score == 0

    def test_band_low(self) -> None:
        result = score([])
        assert result.band == "Low"

    def test_no_manual_review(self) -> None:
        result = score([])
        assert result.manual_review_required is False

    def test_no_reasons(self) -> None:
        result = score([])
        assert result.reasons == []


# ---------------------------------------------------------------------------
# score() — compliance-only evidence must not affect score
# ---------------------------------------------------------------------------


class TestScoreComplianceOnly:
    """Compliance and completeness evidence must not contribute to score."""

    def test_score_still_zero(self) -> None:
        evs = [
            _ev("BANK_COVERAGE",     "compliance",   "medium", evidence_id="E01"),
            _ev("MISSING_DOC",       "completeness", "high",   evidence_id="E02"),
            _ev("FORM_ENT_MANDATORY","compliance",   "critical",evidence_id="E03"),
        ]
        result = score(evs)
        assert result.score == 0

    def test_band_low(self) -> None:
        evs = [
            _ev("BANK_COVERAGE", "compliance", "high", evidence_id="E01"),
        ]
        result = score(evs)
        assert result.band == "Low"

    def test_no_manual_review(self) -> None:
        evs = [
            _ev("BANK_COVERAGE", "compliance", "critical", evidence_id="E01"),
        ]
        result = score(evs)
        assert result.manual_review_required is False

    def test_no_reasons(self) -> None:
        evs = [
            _ev("BANK_COVERAGE", "compliance", "medium", evidence_id="E01"),
        ]
        result = score(evs)
        assert result.reasons == []


# ---------------------------------------------------------------------------
# score() — weight accumulation
# ---------------------------------------------------------------------------


class TestScoreWeights:
    """Verify SEVERITY_WEIGHTS mapping: info=0, low=5, medium=15, high=30, critical=50."""

    @pytest.mark.parametrize("severity,expected_score", [
        ("info",     0),
        ("low",      5),
        ("medium",  15),
        ("high",    30),
        ("critical",50),
    ])
    def test_single_finding(self, severity: str, expected_score: int) -> None:
        ev = _ev("INV_ARITH", "fraud_signal", severity)
        result = score([ev])
        assert result.score == expected_score

    def test_accumulation(self) -> None:
        evs = [
            _ev("INV_ARITH",   "fraud_signal", "low",    evidence_id="E01"),   # 5
            _ev("BANK_BAL",    "fraud_signal", "medium", evidence_id="E02"),   # 15
            _ev("XDOC_MATCH",  "fraud_signal", "high",   evidence_id="E03"),   # 30
        ]
        result = score(evs)
        assert result.score == 50   # 5 + 15 + 30

    def test_one_check_scores_once_per_document(self) -> None:
        """Repeats of one check on one document are one problem: one score line,
        with how many findings it covers. Another document counts again."""
        evs = [
            _ev("BANK_RUNNING_BAL", "fraud_signal", "high", evidence_id="E01", document="stmt_a"),
            _ev("BANK_RUNNING_BAL", "fraud_signal", "high", evidence_id="E02", document="stmt_a"),
            _ev("BANK_RUNNING_BAL", "fraud_signal", "high", evidence_id="E03", document="stmt_a"),
            _ev("BANK_RUNNING_BAL", "fraud_signal", "high", evidence_id="E04", document="stmt_b"),
        ]
        result = score(evs)
        assert result.score == 60
        assert [(c.evidence_id, c.hits, c.points) for c in result.contributions] == [("E01", 3, 30), ("E04", 1, 30)]

    def test_a_repeat_at_higher_severity_sets_the_line(self) -> None:
        evs = [
            _ev("BANK_RUNNING_BAL", "fraud_signal", "medium", evidence_id="E01"),
            _ev("BANK_RUNNING_BAL", "fraud_signal", "critical", evidence_id="E02"),
        ]
        result = score(evs)
        assert [(c.evidence_id, c.hits, c.points) for c in result.contributions] == [("E02", 2, 50)]

    def test_capped_at_100(self) -> None:
        evs = [
            _ev("A", "fraud_signal", "critical", evidence_id="E01"),  # 50
            _ev("B", "fraud_signal", "critical", evidence_id="E02"),  # 50
            _ev("C", "fraud_signal", "critical", evidence_id="E03"),  # 50 (total 150 → capped)
        ]
        result = score(evs)
        assert result.score == 100


# ---------------------------------------------------------------------------
# score() — band thresholds
# ---------------------------------------------------------------------------


class TestScoreBands:
    """BAND_LOW_MAX=19, BAND_MEDIUM_MAX=49."""

    def test_score_0_is_low(self) -> None:
        assert score([]).band == "Low"

    def test_score_19_is_low(self) -> None:
        # Three "low" findings = 15; add a "low" to get 20? No — we need exactly 19.
        # Build with a custom combination: low(5) + low(5) + low(5) = 15 → Low
        # We can't hit 19 exactly with the given weights; 15 is Low, 20 is Medium.
        evs = [_ev(f"A{i}", "fraud_signal", "low", evidence_id=f"E{i:02d}") for i in range(1, 4)]
        result = score(evs)
        assert result.score == 15
        assert result.band == "Low"

    def test_score_20_is_medium(self) -> None:
        # medium(15) + low(5) = 20 → Medium
        evs = [
            _ev("A", "fraud_signal", "medium", evidence_id="E01"),
            _ev("B", "fraud_signal", "low",    evidence_id="E02"),
        ]
        result = score(evs)
        assert result.score == 20
        assert result.band == "Medium"

    def test_score_49_is_medium(self) -> None:
        # 3×medium(15) + low(4×... wait: 3×15=45, +low(5)=50 → High)
        # Use critical(50) → 50 High; medium×3=45 Medium
        evs = [_ev(f"A{i}", "fraud_signal", "medium", evidence_id=f"E{i:02d}") for i in range(1, 4)]
        result = score(evs)
        assert result.score == 45
        assert result.band == "Medium"

    def test_score_50_is_high(self) -> None:
        evs = [_ev("A", "fraud_signal", "critical", evidence_id="E01")]
        result = score(evs)
        assert result.score == 50
        assert result.band == "High"


# ---------------------------------------------------------------------------
# score() — hard rules
# ---------------------------------------------------------------------------


class TestScoreHardRules:
    """XDOC_IDENTITY at high/critical must force band High. Metadata findings
    corroborate; on their own they never force it."""

    def test_metadata_never_forces_high(self) -> None:
        """A re-saved PDF is weak evidence: its band follows its points alone."""
        for check in ("META_MOD_AFTER_CREATE", "META_PRODUCER_MISMATCH", "META_CUSTOM_FUTURE"):
            result = score([_ev(check, "fraud_signal", "high")])
            assert (result.score, result.band) == (30, "Medium")
            assert not any("Hard rule" in r for r in result.reasons)

    def test_meta_mod_after_create_low_does_not_force_high(self) -> None:
        """META_MOD_AFTER_CREATE at low severity — hard rule does NOT fire."""
        ev = _ev("META_MOD_AFTER_CREATE", "fraud_signal", "low")
        result = score([ev])
        # score=5, below BAND_LOW_MAX=19
        assert result.band == "Low"
        # But manual_review is still False (no high/critical)
        assert result.manual_review_required is False

    def test_xdoc_identity_high_forces_high(self) -> None:
        ev = _ev("XDOC_IDENTITY", "fraud_signal", "high")
        result = score([ev])
        assert result.band == "High"

    def test_xdoc_identity_medium_does_not_force_high(self) -> None:
        """XDOC_IDENTITY at medium — hard rule does NOT fire; band follows score."""
        ev = _ev("XDOC_IDENTITY", "fraud_signal", "medium")
        result = score([ev])
        assert result.score == 15
        assert result.band == "Low"    # 15 <= BAND_LOW_MAX(19)

    def test_hard_rule_reason_appended(self) -> None:
        """Hard rule fire should produce an extra reason line."""
        ev = _ev("XDOC_IDENTITY", "fraud_signal", "high")
        result = score([ev])
        # At least 2 lines: one for the finding, one for the hard rule
        assert len(result.reasons) >= 2
        hard_lines = [r for r in result.reasons if "Hard rule" in r]
        assert len(hard_lines) == 1


# ---------------------------------------------------------------------------
# score() — manual_review_required
# ---------------------------------------------------------------------------


class TestManualReview:
    def test_low_band_no_serious_no_review(self) -> None:
        ev = _ev("INV_ARITH", "fraud_signal", "low")
        result = score([ev])
        assert result.band == "Low"
        assert result.manual_review_required is False

    def test_medium_band_no_serious_no_review(self) -> None:
        evs = [
            _ev("A", "fraud_signal", "medium", evidence_id="E01"),
            _ev("B", "fraud_signal", "low",    evidence_id="E02"),
        ]
        result = score(evs)
        assert result.band == "Medium"
        assert result.manual_review_required is False

    def test_high_band_requires_review(self) -> None:
        evs = [_ev("A", "fraud_signal", "critical", evidence_id="E01")]
        result = score(evs)
        assert result.manual_review_required is True

    def test_high_severity_requires_review_even_if_low_band(self) -> None:
        """A single high-severity fraud_signal forces manual_review even if score is low.

        This can only happen when a hard-rule fires, because high=30pts > BAND_LOW_MAX=19.
        Instead test that at least one high-sev ev → manual_review=True.
        """
        ev = _ev("INV_ARITH", "fraud_signal", "high")
        result = score([ev])
        assert result.manual_review_required is True   # score=30 → High band

    def test_critical_fraud_signal_requires_review(self) -> None:
        ev = _ev("XDOC_INVOICE_BANK", "fraud_signal", "critical")
        result = score([ev])
        assert result.manual_review_required is True


# ---------------------------------------------------------------------------
# score() — reasons format
# ---------------------------------------------------------------------------


class TestScoreReasons:
    def test_info_findings_excluded_from_reasons(self) -> None:
        evs = [
            _ev("INV_ARITH", "fraud_signal", "info",   evidence_id="E01"),
            _ev("BANK_BAL",  "fraud_signal", "medium", evidence_id="E02"),
        ]
        result = score(evs)
        assert len(result.reasons) == 1
        assert "E02" in result.reasons[0]

    def test_reason_format(self) -> None:
        ev = _ev(
            "INV_ARITH", "fraud_signal", "medium",
            evidence_id="E03",
            explanation="Line total mismatch",
        )
        result = score([ev])
        assert result.reasons == ["E03 INV_ARITH (medium): Line total mismatch"]

    def test_multiple_findings_all_in_reasons(self) -> None:
        evs = [
            _ev("A", "fraud_signal", "low",  evidence_id="E01"),
            _ev("B", "fraud_signal", "high", evidence_id="E02"),
        ]
        result = score(evs)
        # Both non-info → both appear
        ids_in_reasons = [r.split()[0] for r in result.reasons if not r.startswith("Hard")]
        assert "E01" in ids_in_reasons
        assert "E02" in ids_in_reasons


# ---------------------------------------------------------------------------
# readiness() — status and open items
# ---------------------------------------------------------------------------


class TestReadinessStatus:
    def test_complete_when_no_open_items(self) -> None:
        pack = _empty_pack(entity_self_certs=[], has_bank_statements=False)
        # Only info-severity compliance findings → no open items → "Complete"
        evs = [_ev("BANK_COVERAGE", "compliance", "info", evidence_id="E01")]
        result = readiness(evs, pack)
        assert result.status == "Complete"
        assert result.open_items == []

    def test_incomplete_when_open_item_exists(self) -> None:
        pack = _empty_pack()
        evs = [_ev("BANK_COVERAGE", "compliance", "medium", evidence_id="E01")]
        result = readiness(evs, pack)
        assert result.status == "Incomplete"
        assert len(result.open_items) == 1

    def test_fraud_signal_does_not_count_as_open_item(self) -> None:
        pack = _empty_pack()
        evs = [
            _ev("META_MOD", "fraud_signal", "high",   evidence_id="E01"),
            _ev("BANK_COV", "compliance",   "medium", evidence_id="E02"),
        ]
        result = readiness(evs, pack)
        # Only the compliance finding is an open item
        assert len(result.open_items) == 1
        assert "E02" in result.open_items[0]

    def test_open_item_format(self) -> None:
        pack = _empty_pack()
        ev = _ev(
            "BANK_COVERAGE", "completeness", "high",
            evidence_id="E05",
            explanation="Missing statements",
        )
        result = readiness([ev], pack)
        assert result.open_items == ["E05 BANK_COVERAGE (high): Missing statements"]


# ---------------------------------------------------------------------------
# readiness() — checklist
# ---------------------------------------------------------------------------


class TestReadinessChecklist:
    def test_checklist_has_four_items(self) -> None:
        pack = _empty_pack()
        result = readiness([], pack)
        assert len(result.checklist) == 4

    def test_checklist_ids(self) -> None:
        pack = _empty_pack()
        result = readiness([], pack)
        ids = [item["id"] for item in result.checklist]
        assert "FATCA_CRS_ENTITY" in ids
        assert "BANK_6M" in ids
        assert "NOA_GUARANTORS" in ids
        assert "POA_FOREIGNERS" in ids

    def test_not_implemented_items_are_not_checked(self) -> None:
        pack = _empty_pack()
        result = readiness([], pack)
        poa = next(i for i in result.checklist if i["id"] == "POA_FOREIGNERS")
        assert poa["status"] == "Not checked in prototype"

    @pytest.mark.parametrize(("ids", "status"), [
        ([], "Missing"), (["S1234567D"], "Incomplete"), (["S1234567D", "G7654321L"], "Provided")])
    def test_noa_status_follows_the_guarantors_covered(self, ids: list[str], status: str) -> None:
        address = Address(country="Singapore")
        guarantors = [Guarantor(name=n, id_number=i, nationality="Singapore", residential_address=address)
                      for n, i in (("Tan Wei Ming", "S1234567D"), ("Daniel Brooks", "G7654321L"))]
        manifest = ApplicationManifest(
            application_id="A", application_date=date(2026, 8, 10), company_name="C", uen="202301234K",
            country_of_incorporation="Singapore", date_of_incorporation=date(2023, 1, 16),
            registered_address=address, authorised_signatories=[], guarantors=guarantors)
        notices = [NoticeOfAssessment(doc_id=f"noa_{i}", name="x", id_number=i, year_of_assessment=2026)
                   for i in ids]
        pack = _empty_pack().model_copy(update={"manifest": manifest, "notices_of_assessment": notices})
        noa = next(i for i in readiness([], pack).checklist if i["id"] == "NOA_GUARANTORS")
        assert noa["status"] == status

    def test_noa_not_checked_without_application_data(self) -> None:
        noa = next(i for i in readiness([], _empty_pack()).checklist if i["id"] == "NOA_GUARANTORS")
        assert noa["status"] == "No guarantors in the application data"

    def test_fatca_crs_missing_when_no_cert(self) -> None:
        pack = _empty_pack(entity_self_certs=[], docs=[])
        result = readiness([], pack)
        fatca = next(i for i in result.checklist if i["id"] == "FATCA_CRS_ENTITY")
        assert fatca["status"] == "Missing"

    def test_bank_6m_missing_when_no_statements(self) -> None:
        pack = _empty_pack(has_bank_statements=False)
        result = readiness([], pack)
        bank = next(i for i in result.checklist if i["id"] == "BANK_6M")
        assert bank["status"] == "Missing"

    def test_bank_6m_incomplete_when_coverage_fired(self) -> None:
        pack = _empty_pack(has_bank_statements=True)
        evs = [_ev("BANK_COVERAGE", "compliance", "medium", evidence_id="E01")]
        result = readiness(evs, pack)
        bank = next(i for i in result.checklist if i["id"] == "BANK_6M")
        assert bank["status"] == "Incomplete"

    def test_bank_6m_provided_when_statements_and_no_coverage(self) -> None:
        pack = _empty_pack(has_bank_statements=True)
        result = readiness([], pack)
        bank = next(i for i in result.checklist if i["id"] == "BANK_6M")
        assert bank["status"] == "Provided"
