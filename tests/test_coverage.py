"""
tests/test_coverage.py
----------------------
Evidence coverage: every risk finding must be traceable to a real document,
page and quote. Stub extraction, no AWS calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gldb.coverage import coverage, untraceable
from gldb.intake import intake, load_manifest
from gldb.pipeline import assess
from gldb.schemas import CaseResult, Evidence, ExtractedPack
from tests.statement_forms import END, ROWS, statement_form

PACKS = Path(__file__).parent / "fixtures" / "packs"


@pytest.fixture(scope="module")
def tampered() -> CaseResult:
    folder = PACKS / "tampered"
    return assess("tampered", sorted(folder.glob("*.pdf")), load_manifest(folder))


def _risk_finding(result: CaseResult, check_id: str) -> Evidence:
    return next(e for e in result.evidence if e.check_id == check_id and e.severity != "info")


def test_every_tampered_finding_is_traceable(tampered: CaseResult) -> None:
    assert untraceable(tampered.evidence, tampered.pack) == []
    assert coverage(tampered.evidence, tampered.pack) == 1.0


@pytest.mark.parametrize("change", [
    {"document": "invoice_INV-9999"},          # no such document
    {"page": 9},                               # no such page
    {"quote": "Total: SGD 99,999.00"},         # quote not on the page
    {"quote": None, "observed": None},         # nothing to check against
])
def test_unverifiable_findings_are_reported(tampered: CaseResult, change: dict) -> None:
    ev = _risk_finding(tampered, "XDOC_PO_INVOICE").model_copy(update={**change, "evidence_id": "E99"})
    assert untraceable([*tampered.evidence, ev], tampered.pack) == ["E99"]
    assert coverage([ev], tampered.pack) == 0.0


def test_findings_on_the_application_need_the_application(tampered: CaseResult) -> None:
    ev = Evidence(evidence_id="E98", check_id="REGISTRY_SIGNATORY_NOT_OFFICER", severity="medium",
                  document="pack", page=1, observed="Someone Else", method="deterministic",
                  explanation="x")
    assert untraceable([ev], tampered.pack) == []
    assert untraceable([ev], tampered.pack.model_copy(update={"manifest": None, "official": None})) == ["E98"]


def test_only_risk_findings_count(tampered: CaseResult) -> None:
    readiness_item = Evidence(evidence_id="E97", check_id="BANK_COVERAGE", category="completeness",
                              severity="medium", document="nowhere", page=1, method="deterministic",
                              explanation="x")
    assert untraceable([readiness_item], tampered.pack) == []
    assert coverage([], tampered.pack) == 1.0


def _form_finding(path: Path, quote: str) -> tuple[list[Evidence], ExtractedPack]:
    docs = intake("t", [path])
    pack = ExtractedPack(case_id="t", docs=docs, invoices=[], bank_statements=[], financial_statements=[],
                         extraction_mode="stub", paths={docs[0].doc_id: path})
    ev = Evidence(evidence_id="E01", check_id="BANK_RUNNING_BAL", severity="high", document=docs[0].doc_id,
                  page=1, quote=quote, method="deterministic", explanation="x")
    return [ev], pack


def test_a_quote_held_in_a_form_field_is_traceable(tmp_path: Path) -> None:
    """A fillable statement's figures are field values, not page text: the
    viewer boxes the field, so the analyst can be shown it."""
    path = statement_form(tmp_path, [*ROWS, END])
    assert untraceable(*_form_finding(path, "Sales Revenue")) == []
    assert untraceable(*_form_finding(path, "Dividend Income")) == ["E01"]


def test_findings_on_the_official_records_need_them(tampered: CaseResult) -> None:
    ev = Evidence(evidence_id="E96", check_id="SANCTIONS_MATCH", severity="high", document="pack", page=1,
                  quote="VIKTOR ALDANE", method="deterministic", explanation="x")
    no_application = tampered.pack.model_copy(update={"manifest": None})
    assert untraceable([ev], no_application) == []
    assert untraceable([ev], no_application.model_copy(update={"official": None})) == ["E96"]
