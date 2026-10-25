"""
tests/test_acra_checks.py
-------------------------
Checks that hold the applicant's documents to the ACRA profile it supplied:
the submitted financial statements, and the entity self-certification's
identity and signatories. Packs are built directly. No AWS calls.
"""

from __future__ import annotations

from datetime import date

import pytest

from gldb import checks
from gldb.checks import check_acra_entity, check_acra_persons, check_acra_signatories, check_fs_acra, check_xdoc_identity
from gldb.schemas import Address, BankStatement, Signatory, TaxResidencyRow
from tests.acma_records import D, FIGURES
from tests.acma_records import acra as _acra
from tests.acma_records import acra_financials as _acra_financials
from tests.acma_records import cert as _cert
from tests.acma_records import chairman as _chairman
from tests.acma_records import fs as _fs
from tests.acma_records import indiv as _indiv
from tests.acma_records import noa as _noa
from tests.acma_records import pack as _pack

# ---- submitted financials vs the ACRA profile ---------------------------------------------------

def test_financials_that_match_the_profile_pass() -> None:
    assert check_fs_acra(_pack(financial_statements=[_fs()], acra_profiles=[_acra(financials=_acra_financials())])) == []


def test_a_figure_that_differs_is_cited_at_its_row() -> None:
    pack = _pack(financial_statements=[_fs(revenue=D("9935000"))], acra_profiles=[_acra(financials=_acra_financials())])
    (finding,) = check_fs_acra(pack)
    assert (finding.check_id, finding.category, finding.severity, finding.document) == (
        "FS_ACRA_MISMATCH", "fraud_signal", "high", "Submitted financials",
    )
    assert (finding.page, finding.quote) == (1, "Total Revenue 7.935")
    assert finding.expected == "SGD 7,935,000.00" and finding.observed == "SGD 9,935,000.00"


def test_every_figure_that_differs_is_reported() -> None:
    pack = _pack(financial_statements=[_fs(revenue=D("9935000"), total_assets=D("1000"))],
                 acra_profiles=[_acra(financials=_acra_financials())])
    assert len(check_fs_acra(pack)) == 2


@pytest.mark.parametrize(("revenue", "findings"), [("7935400", 0), ("7934600", 0), ("7936000", 1), ("7934000", 1)])
def test_acra_rounds_to_what_it_prints(revenue: str, findings: int) -> None:
    """ACRA prints 7.935 (millions): any figure that rounds to it agrees."""
    pack = _pack(financial_statements=[_fs(revenue=D(revenue))], acra_profiles=[_acra(financials=_acra_financials())])
    assert len(check_fs_acra(pack)) == findings


def test_the_unit_acra_prints_in_is_a_stated_assumption(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(checks, "ACRA_FIGURES_UNIT", D("1000"))
    pack = _pack(financial_statements=[_fs()], acra_profiles=[_acra(financials=_acra_financials())])
    assert len(check_fs_acra(pack)) == len(FIGURES)


def test_statements_for_another_year_are_noted_not_compared() -> None:
    pack = _pack(financial_statements=[_fs(financial_year_end=date(2024, 12, 31), revenue=D(1))],
                 acra_profiles=[_acra(financials=_acra_financials())])
    (finding,) = check_fs_acra(pack)
    assert (finding.check_id, finding.severity) == ("FS_ACRA_MISMATCH", "info")
    assert "2024-12-31" in finding.explanation and "2025-12-31" in finding.explanation


def test_a_figure_either_document_lacks_is_not_compared() -> None:
    pack = _pack(financial_statements=[_fs(ebit=None, revenue=D("9935000"))],
                 acra_profiles=[_acra(financials=_acra_financials(revenue=None, profit_after_tax=None))])
    assert check_fs_acra(pack) == []


def test_without_a_profile_or_its_financial_information_nothing_is_compared() -> None:
    assert check_fs_acra(_pack(financial_statements=[_fs(revenue=D(1))])) == []
    assert check_fs_acra(_pack(financial_statements=[_fs(revenue=D(1))], acra_profiles=[_acra()])) == []


# ---- entity form vs the ACRA profile ------------------------------------------------------------

def test_an_entity_form_that_matches_the_profile_passes() -> None:
    assert check_acra_entity(_pack(entity_self_certs=[_cert()], acra_profiles=[_acra()])) == []


def test_a_different_legal_name_is_a_serious_mismatch() -> None:
    (finding,) = check_acra_entity(_pack(entity_self_certs=[_cert(legal_name="ACME TRADING PTE LTD")], acra_profiles=[_acra()]))
    assert (finding.check_id, finding.severity, finding.document) == ("XDOC_ACRA_ENTITY", "high", "ACMA ENTITY")
    assert finding.expected == "ACMA LTD." and finding.observed == "ACME TRADING PTE LTD"


def test_case_spacing_and_punctuation_do_not_make_a_different_name() -> None:
    assert check_acra_entity(_pack(entity_self_certs=[_cert(legal_name="Acma  Ltd")], acra_profiles=[_acra()])) == []


def test_a_singapore_tax_number_that_is_not_the_uen_is_a_serious_mismatch() -> None:
    row = TaxResidencyRow(row=1, country="SINGAPORE", tin="196500999X", reasons_ticked=[], explanation=None)
    (finding,) = check_acra_entity(_pack(entity_self_certs=[_cert(tax_rows=[row])], acra_profiles=[_acra()]))
    assert (finding.severity, finding.expected, finding.observed) == ("high", "196500233E", "196500999X")


def test_tax_numbers_of_other_countries_are_not_the_uen() -> None:
    row = TaxResidencyRow(row=1, country="MALAYSIA", tin="12345", reasons_ticked=[], explanation=None)
    assert check_acra_entity(_pack(entity_self_certs=[_cert(tax_rows=[row])], acra_profiles=[_acra()])) == []


def test_a_different_postal_code_is_a_mismatch() -> None:
    cert = _cert(registered_address=Address(street="1 OTHER ROAD", postal_code="123456"))
    (finding,) = check_acra_entity(_pack(entity_self_certs=[cert], acra_profiles=[_acra()]))
    assert (finding.severity, finding.expected, finding.observed) == ("medium", "619093", "123456")


def test_a_profile_address_without_a_postal_code_is_not_compared() -> None:
    pack = _pack(entity_self_certs=[_cert(registered_address=Address(postal_code="123456"))],
                 acra_profiles=[_acra(registered_address="19 JURONG PORT ROAD")])
    assert check_acra_entity(pack) == []


def test_the_entity_check_needs_a_profile() -> None:
    assert check_acra_entity(_pack(entity_self_certs=[_cert(legal_name="X")])) == []


# ---- signatories vs the profile's officers ------------------------------------------------------

def _signatory(name: str | None, slot: int = 1) -> Signatory:
    return Signatory(slot=slot, name=name, designation=None, date_raw=None, date=None, signature_field_signed=False)


def test_signatories_who_are_officers_pass() -> None:
    assert check_acra_signatories(_pack(entity_self_certs=[_cert()], acra_profiles=[_acra()])) == []


def test_a_signatory_who_is_not_an_officer_is_reported() -> None:
    cert = _cert(signatories=[_signatory("MATTHEW TAN"), _signatory("TAN BEE HUAT", 2)])
    (finding,) = check_acra_signatories(_pack(entity_self_certs=[cert], acra_profiles=[_acra()]))
    assert (finding.check_id, finding.category, finding.severity, finding.document) == (
        "XDOC_ACRA_SIGNATORY", "fraud_signal", "medium", "ACMA ENTITY",
    )
    assert finding.observed == "MATTHEW TAN" and finding.expected == "TAN BEE HUAT"
    assert finding.quote == "MATTHEW TAN"


def test_an_officer_is_recognised_whatever_the_case_and_order() -> None:
    cert = _cert(signatories=[_signatory("Tan  Bee Huat")])
    assert check_acra_signatories(_pack(entity_self_certs=[cert], acra_profiles=[_acra()])) == []


def test_an_empty_signatory_slot_is_ignored() -> None:
    assert check_acra_signatories(_pack(entity_self_certs=[_cert(signatories=[_signatory(None)])], acra_profiles=[_acra()])) == []


def test_a_profile_with_no_officers_flags_every_signatory() -> None:
    (finding,) = check_acra_signatories(_pack(entity_self_certs=[_cert()], acra_profiles=[_acra(officers=[])]))
    assert finding.expected == "no officers listed"


# ---- identity is held to the profile ------------------------------------------------------------

def _statement(holder: str) -> BankStatement:
    return BankStatement(doc_id="bank", account_holder=holder, period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
                         opening_balance=D(0), closing_balance=D(0), transactions=[])


def test_the_bank_account_holder_is_held_to_the_profiles_name() -> None:
    assert check_xdoc_identity(_pack(bank_statements=[_statement("ACMA Ltd.")], acra_profiles=[_acra()])) == []
    (finding,) = check_xdoc_identity(_pack(bank_statements=[_statement("Someone Else Pte Ltd")], acra_profiles=[_acra()]))
    assert finding.check_id == "XDOC_IDENTITY" and "ACRA profile company name 'ACMA LTD.'" in finding.explanation


def test_the_profile_is_the_reference_before_the_financial_statements() -> None:
    fs = _fs(company_name="OTHER LTD", uen="200000000A")
    pack = _pack(financial_statements=[fs], bank_statements=[_statement("ACMA Ltd.")], acra_profiles=[_acra()])
    assert check_xdoc_identity(pack) == []


def test_statements_that_state_no_identity_are_no_reference() -> None:
    assert check_xdoc_identity(_pack(financial_statements=[_fs()], bank_statements=[_statement("Anyone")])) == []


# ---- the people on the forms and the Notice of Assessment vs the profile's officers -------------

def test_a_person_who_matches_the_officer_with_their_id_passes() -> None:
    pack = _pack(notices_of_assessment=[_noa()], individual_self_certs=[_indiv()], acra_profiles=[_chairman()])
    assert check_acra_persons(pack) == []


def test_the_same_id_under_another_name_is_a_serious_mismatch() -> None:
    (finding,) = check_acra_persons(_pack(notices_of_assessment=[_noa(name="MS LIM MEI LING")], acra_profiles=[_chairman()]))
    assert (finding.check_id, finding.category, finding.severity, finding.document) == (
        "XDOC_ACRA_PERSON", "fraud_signal", "high", "acma_NOA",
    )
    assert (finding.expected, finding.observed, finding.quote) == ("TAN BEE HUAT", "MS LIM MEI LING", "MS LIM MEI LING")
    assert "S****888E" in finding.explanation and "S2015888E" not in finding.explanation


def test_a_title_case_and_spacing_are_not_a_different_name() -> None:
    pack = _pack(notices_of_assessment=[_noa(name="Mr  Tan Bee Huat")], acra_profiles=[_chairman()])
    assert check_acra_persons(pack) == []


def test_a_different_address_for_the_same_person_is_reported() -> None:
    pack = _pack(notices_of_assessment=[_noa(postal_code="478236")], individual_self_certs=[_indiv(residential_address=Address(postal_code="478236"))],
                 acra_profiles=[_chairman()])
    findings = check_acra_persons(pack)
    assert [(f.severity, f.document, f.expected, f.observed) for f in findings] == [
        ("medium", "acma_NOA", "987654", "478236"), ("medium", "ACMA INDIV", "987654", "478236"),
    ]


def test_an_address_either_document_lacks_is_not_compared() -> None:
    pack = _pack(notices_of_assessment=[_noa(postal_code=None)], individual_self_certs=[_indiv(residential_address=Address())],
                 acra_profiles=[_chairman(postal_code=None)])
    assert check_acra_persons(pack) == []
    assert check_acra_persons(_pack(notices_of_assessment=[_noa(postal_code="1")], acra_profiles=[_chairman(postal_code=None)])) == []


def test_a_person_who_is_not_an_officer_is_not_compared() -> None:
    pack = _pack(notices_of_assessment=[_noa(id_number="S7654321B", name="SOMEONE ELSE")], acra_profiles=[_chairman()])
    assert check_acra_persons(pack) == []


def test_the_person_check_needs_a_profile() -> None:
    assert check_acra_persons(_pack(notices_of_assessment=[_noa(name="X")])) == []
