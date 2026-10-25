"""
tests/test_notice_of_assessment.py
----------------------------------
A guarantor's Notice of Assessment as IRAS prints it: the identity, the year
and the tax computation are read by code, not by an LLM. The labelled layout
of the synthetic packs is covered with the other extractors. No AWS calls.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from gldb.extract_stub import parse_pack
from gldb.intake import intake
from gldb.schemas import NoticeOfAssessment
from tests.text_pdfs import text_pdf

HEAD = [
    "Tax Reference Number : S2015888E",
    "Date : 30 Jun 2025",
    "Please quote the Tax Reference Number (e.g. NRIC, FIN etc.) in full when corresponding with us.",
    "MR TAN BEE HUAT",
    "HOUGANG AVENUE 3",
    "#15-243",
    "BLK 123",
    "SINGAPORE 478236",
    "1-1",
    "Income Tax – Notice of Assessment (Original)",
]
COMPUTATION = [
    "YEAR OF ASSESSMENT 2025",
    "INCOME^ ($) DEDUCTIONS ($) CHARGEABLE ($)",
    "+ - = INCOME",
    "131,862.00 22,540.00 109,322.00",
    "EMPLOYMENT 131,860.00 DONATIONS 490.00 TAX COMPUTATION",
]
PAYABLE = ["Tax Payable", "by 30 Jul 2025", "$6,522.03"]


def _parse(tmp_path: Path, *body: list[str]) -> NoticeOfAssessment:
    """The notice a first page of HEAD followed by *body* reads as."""
    path = text_pdf(tmp_path, "noa.pdf", [[*HEAD, *(line for part in body for line in part)]])
    docs = intake("t", [path])
    assert [d.doc_type for d in docs] == ["notice_of_assessment"]
    (notice,) = parse_pack("t", docs, [path], None).notices_of_assessment
    return notice


def test_identity_and_year_are_read(tmp_path: Path) -> None:
    notice = _parse(tmp_path, COMPUTATION, PAYABLE)
    assert (notice.name, notice.id_number, notice.year_of_assessment) == ("MR TAN BEE HUAT", "S2015888E", 2025)


def test_the_tax_computation_is_read(tmp_path: Path) -> None:
    notice = _parse(tmp_path, COMPUTATION, PAYABLE)
    assert (notice.income, notice.deductions, notice.chargeable_income, notice.tax_payable) == (
        Decimal("131862.00"), Decimal("22540.00"), Decimal("109322.00"), Decimal("6522.03"),
    )


def test_the_postal_code_of_the_address_is_read(tmp_path: Path) -> None:
    assert _parse(tmp_path, COMPUTATION, PAYABLE).postal_code == "478236"


def test_a_notice_with_no_postal_code_has_none(tmp_path: Path) -> None:
    path = text_pdf(tmp_path, "noa.pdf", [[*(line for line in HEAD if "478236" not in line), *COMPUTATION]])
    docs = intake("t", [path])
    (notice,) = parse_pack("t", docs, [path], None).notices_of_assessment
    assert notice.postal_code is None


def test_figures_the_notice_does_not_print_stay_empty(tmp_path: Path) -> None:
    """A notice with no tax payable (or none of the figures) is still a notice."""
    notice = _parse(tmp_path, ["YEAR OF ASSESSMENT 2025"])
    assert (notice.income, notice.deductions, notice.chargeable_income, notice.tax_payable) == (None,) * 4


def test_a_notice_without_a_year_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="noa.*cannot read the Notice of Assessment"):
        _parse(tmp_path, COMPUTATION[1:], PAYABLE)


def test_a_notice_without_a_name_is_refused(tmp_path: Path) -> None:
    path = text_pdf(tmp_path, "noa.pdf", [[HEAD[0], HEAD[1], HEAD[-1], *COMPUTATION]])
    docs = intake("t", [path])
    with pytest.raises(ValueError, match="noa.*cannot read the Notice of Assessment"):
        parse_pack("t", docs, [path], None)
