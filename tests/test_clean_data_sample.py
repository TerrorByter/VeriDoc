"""
tests/test_clean_data_sample.py
-------------------------------
The team's clean data sample (kept in data/templates/sample/): one
case's documents in the format cases will arrive in. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gldb import forensics
from gldb.checks import (
    check_acra_entity,
    check_bank_coverage,
    check_bank_running_bal,
    check_fs_acra,
    check_xdoc_bank_cash,
    check_xdoc_bank_debt,
    check_xdoc_bank_revenue,
    check_xdoc_identity,
)
from gldb.data_factory import ENTITY_TEMPLATE, INDIVIDUAL_TEMPLATE
from gldb.extract_stub import parse_bank_statement_form, parse_entity_self_cert, parse_individual_self_cert, parse_pack
from gldb.intake import intake
from gldb.schemas import ExtractedPack

SAMPLE = Path(__file__).parent.parent / "data" / "templates" / "sample"
NOA = SAMPLE / "acma_NOA.pdf"
#: The CEO's Notice of Assessment and individual form (the form is flattened: its values are printed, not fields)
CEO_NOA, CEO_INDIV = SAMPLE / "acma_NOA (Matthew Tan).pdf", SAMPLE / "ACMA INDIV (Matthew Tan).pdf"
STATEMENT = SAMPLE / "ACMA BANK 1.pdf"
WORKBOOK = SAMPLE / "Submitted financials.xlsx"
#: One case: the sample's statement, forms, ACRA profile, workbook and Notice of Assessment
CASE = [STATEMENT, SAMPLE / "ACMA ENTITY.pdf", SAMPLE / "ACMA INDIV.pdf", SAMPLE / "ACMA ACRA.pdf", WORKBOOK, NOA]


def test_every_document_is_recognised() -> None:
    assert {d.filename: d.doc_type for d in intake("t", CASE)} == {
        "ACMA BANK 1.pdf": "bank_statement",
        "ACMA ENTITY.pdf": "fatca_crs_entity",
        "ACMA INDIV.pdf": "fatca_crs_individual",
        "ACMA ACRA.pdf": "acra_profile",
        "Submitted financials.xlsx": "financial_statements",
        "acma_NOA.pdf": "notice_of_assessment",
    }


def test_the_statement_form_reads_and_reconciles() -> None:
    bank = parse_bank_statement_form("ACMA BANK 1", STATEMENT)
    assert bank.account_holder == "ACMA Ltd."
    assert (bank.period_start, bank.period_end) == (date(2026, 1, 1), date(2026, 6, 30))
    assert (bank.opening_balance, bank.closing_balance) == (Decimal("1213365.00"), Decimal("862148.58"))
    assert len(bank.transactions) == 28
    pack = ExtractedPack(case_id="t", docs=[], invoices=[], bank_statements=[bank], financial_statements=[],
                         extraction_mode="stub")
    assert check_bank_running_bal(pack) == []
    assert check_bank_coverage(pack) == []


def test_the_notice_of_assessment_is_read() -> None:
    notice = parse_pack("t", intake("t", [NOA]), [NOA], None).notices_of_assessment[0]
    assert (notice.name, notice.id_number, notice.year_of_assessment) == ("MR TAN BEE HUAT", "S2015888E", 2025)
    assert (notice.income, notice.deductions, notice.chargeable_income, notice.tax_payable) == (
        Decimal("131862.00"), Decimal("22540.00"), Decimal("109322.00"), Decimal("6522.03"),
    )
    assert notice.income - notice.deductions == notice.chargeable_income


def test_the_ceos_notice_is_read_though_its_totals_share_a_line_with_the_signs() -> None:
    """Matthew Tan's notice prints "+ 119,177.95 - 16,298.86 = INCOME 102,879.09" on one line, where the
    chairman's prints the signs above the totals: both are how IRAS lays a notice out."""
    notice = parse_pack("t", intake("t", [CEO_NOA]), [CEO_NOA], None).notices_of_assessment[0]
    assert (notice.name, notice.id_number, notice.year_of_assessment, notice.postal_code) == (
        "MR MATTHEW TAN", "S8294716J", 2025, "650412")
    assert (notice.income, notice.deductions, notice.chargeable_income, notice.tax_payable) == (
        Decimal("119177.95"), Decimal("16298.86"), Decimal("102879.09"), Decimal("5781.10"),
    )
    assert notice.income - notice.deductions == notice.chargeable_income


@pytest.mark.skipif(not INDIVIDUAL_TEMPLATE.exists(), reason="GLDB AcroForm templates not present")
def test_the_individual_form_is_read() -> None:
    cert = parse_individual_self_cert("ACMA INDIV", SAMPLE / "ACMA INDIV.pdf")
    assert (cert.full_name, cert.date_of_birth, cert.declaration_id_number) == ("TAN BEE HUAT", date(1982, 5, 20), "S2015888E")
    assert cert.template_id == "gldb_fatca_crs_individual_2025-11"
    assert cert.residential_address.postal_code == "478236"


def test_the_acra_profile_is_read() -> None:
    path = SAMPLE / "ACMA ACRA.pdf"
    profile = parse_pack("t", intake("t", [path]), [path], None).acra_profiles[0]
    assert (profile.company_name, profile.uen, profile.incorporation_date) == ("ACMA LTD.", "196500233E", date(1965, 10, 9))
    # The CEO's position wraps over three lines beside the nationality, which wraps too
    assert [(o.name, o.id_number, o.position, o.postal_code) for o in profile.officers] == [
        ("TAN BEE HUAT", "S2015888E", "CHAIRMAN", "478236"),
        ("MATTHEW TAN", "S8294716J", "CHIEF EXECUTIVE OFFICER", "650412")]
    assert [(s.name, s.shares) for s in profile.shareholders] == [("BOBBY DARYL LEE", 25_000_000)]


def _case() -> ExtractedPack:
    return parse_pack("t", intake("t", CASE), CASE, None)


def test_the_whole_case_is_read_by_the_stub_extractor() -> None:
    pack = _case()
    assert [len(items) for items in (
        pack.bank_statements, pack.entity_self_certs, pack.individual_self_certs,
        pack.notices_of_assessment, pack.acra_profiles, pack.financial_statements,
    )] == [1] * 6


def test_the_workbook_agrees_with_acra_and_the_statement_with_the_workbook() -> None:
    """The cross-checks the team's pairs call for hold on the consistent parts of the sample."""
    pack = _case()
    assert pack.financial_statements[0].total_assets == Decimal("6749000.00")
    for check in (check_fs_acra, check_xdoc_bank_cash, check_xdoc_bank_debt, check_xdoc_bank_revenue,
                  check_acra_entity, check_xdoc_identity):
        assert check(pack) == [], check.__name__


def test_the_notice_of_assessment_title_is_not_hidden_text() -> None:
    assert forensics.hidden_text(NOA) == []


@pytest.mark.skipif(not ENTITY_TEMPLATE.exists(), reason="GLDB AcroForm templates not present")
def test_the_entity_form_is_the_current_template() -> None:
    cert = parse_entity_self_cert("ACMA ENTITY", SAMPLE / "ACMA ENTITY.pdf")
    assert cert.template_id == "gldb_fatca_crs_entity_2025-11"
