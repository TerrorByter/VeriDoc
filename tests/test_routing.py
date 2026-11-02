"""
tests/test_routing.py
---------------------
Tests for gldb/routing.py: the decision layer that turns a scored case into a
review path. Pure functions, no AWS calls.
"""

from __future__ import annotations

from typing import Literal

import pytest

from gldb import routing
from gldb.config import POLICY_VERSION, QA_SAMPLE_PERCENT
from gldb.schemas import DocMeta, DocType, Evidence, ExtractedPack, ReadinessResult, RiskResult

Severity = Literal["info", "low", "medium", "high", "critical"]
Band = Literal["Low", "Medium", "High"]


def _ev(check_id: str, severity: Severity = "medium", evidence_id: str = "E01") -> Evidence:
    return Evidence(
        evidence_id=evidence_id, check_id=check_id, category="fraud_signal",
        severity=severity, document="doc", page=1, method="deterministic",
        explanation="x",
    )


def _risk(band: Band, score: int = 0) -> RiskResult:
    return RiskResult(score=score, band=band, manual_review_required=False, reasons=[])


def _ready(status: Literal["Complete", "Incomplete"] = "Complete") -> ReadinessResult:
    return ReadinessResult(status=status, open_items=[], checklist=[])


def _pack(
    doc_types: tuple[DocType, ...] = ("invoice",),
    ocr_confidence: dict[str, list[float]] | None = None,
) -> ExtractedPack:
    docs = [
        DocMeta(doc_id=f"d{i}", filename=f"d{i}.pdf", sha256="0" * 64, doc_type=t,
                page_count=1, creation_date=None, mod_date=None, producer=None)
        for i, t in enumerate(doc_types)
    ]
    return ExtractedPack(case_id="c", docs=docs, invoices=[], bank_statements=[],
                         financial_statements=[], extraction_mode="stub",
                         ocr_confidence=ocr_confidence or {})


def _not_sampled() -> str:
    return next(f"case-{i}" for i in range(1000) if not routing.qa_sampled(f"case-{i}"))


@pytest.mark.parametrize(("band", "path"), [
    ("Low", "fast_track"), ("Medium", "standard_review"), ("High", "enhanced_review"),
])
def test_path_follows_the_risk_band(band: Band, path: str) -> None:
    r = routing.route(_not_sampled(), [], _risk(band), _ready(), _pack())
    assert r.path == path
    assert r.edd_required is (path == "enhanced_review")
    assert r.analyst_review is (path != "fast_track")
    assert r.policy_version == POLICY_VERSION


def test_hard_flag_forces_enhanced_review_whatever_the_score() -> None:
    r = routing.route(_not_sampled(), [_ev("FORENSIC_HIDDEN_TEXT", evidence_id="E07")],
                      _risk("Low", 15), _ready(), _pack())
    assert (r.path, r.edd_required, r.analyst_review) == ("enhanced_review", True, True)
    assert r.hard_flags == ["E07 FORENSIC_HIDDEN_TEXT"]


def test_info_findings_are_never_hard_flags() -> None:
    r = routing.route(_not_sampled(), [_ev("FORENSIC_HIDDEN_TEXT", severity="info")],
                      _risk("Low"), _ready(), _pack())
    assert r.path == "fast_track" and r.hard_flags == []


@pytest.mark.parametrize(("pack", "evidence", "expected"), [
    (_pack(("unknown",)), [], "d0: document type not recognised"),
    (_pack(), [_ev("SYSTEM_CHECK_ERROR", evidence_id="E03")], "E03: a check failed to run"),
    (_pack(ocr_confidence={"d0": [99.0, 61.5]}), [], "d0 page 2: OCR confidence 62% is below 80%"),
])
def test_low_confidence_goes_to_an_analyst_whatever_the_score(
    pack: ExtractedPack, evidence: list[Evidence], expected: str
) -> None:
    r = routing.route(_not_sampled(), evidence, _risk("Low"), _ready(), pack)
    assert r.path == "standard_review" and r.analyst_review
    assert expected in r.low_confidence


def test_incomplete_application_goes_to_an_analyst() -> None:
    r = routing.route(_not_sampled(), [], _risk("Low"), _ready("Incomplete"), _pack())
    assert r.path == "standard_review"
    assert any("incomplete" in reason for reason in r.reasons)


def test_low_confidence_never_downgrades_enhanced_review() -> None:
    r = routing.route(_not_sampled(), [], _risk("High"), _ready("Incomplete"), _pack(("unknown",)))
    assert r.path == "enhanced_review"


def test_qa_sample_is_reproducible_and_about_five_percent() -> None:
    ids = [f"CASE-{i}" for i in range(4000)]
    picks = [routing.qa_sampled(i) for i in ids]
    assert picks == [routing.qa_sampled(i) for i in ids]
    assert abs(sum(picks) / len(ids) * 100 - QA_SAMPLE_PERCENT) < 1.5


def test_sampled_fast_track_case_gets_an_analyst() -> None:
    sampled = next(f"case-{i}" for i in range(1000) if routing.qa_sampled(f"case-{i}"))
    r = routing.route(sampled, [], _risk("Low"), _ready(), _pack())
    assert (r.path, r.qa_sampled, r.analyst_review) == ("fast_track", True, True)


def test_sampling_applies_only_to_fast_track() -> None:
    sampled = next(f"case-{i}" for i in range(1000) if routing.qa_sampled(f"case-{i}"))
    assert routing.route(sampled, [], _risk("High"), _ready(), _pack()).qa_sampled is False
