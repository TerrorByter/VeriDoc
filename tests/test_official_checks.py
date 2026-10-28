"""
tests/test_official_checks.py
-----------------------------
Checks against the official records the pipeline fetched (gldb.official):
the documents are held to ACRA's own record rather than an uploaded profile,
a guarantor's notice may come from IRAS, an uploaded copy of a record is
compared with the original, and a service that did not answer is an open
item. Records come from the in-process mock service (conftest).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest

from gldb import official
from gldb.checks import (
    acra_record,
    check_acra_persons,
    check_official_copies,
    check_official_records,
    check_registry_entities,
    guarantors_without_noa,
)
from gldb.schemas import Address, ApplicationManifest, ExtractedPack, Guarantor, TaxResidencyRow
from tests.acma_records import acra, acra_financials, cert, indiv, noa, pack


def _fetched(**fields) -> ExtractedPack:
    """An ACMA pack (entity and individual forms, the chairman at home in Hougang as ACRA and IRAS
    have him) with its official records fetched."""
    chairman = indiv(residential_address=Address(postal_code="478236"))
    acma = pack(**{"entity_self_certs": [cert()], "individual_self_certs": [chairman], **fields})
    return acma.model_copy(update={"official": official.fetch(acma)})


def test_the_fetched_record_is_preferred_to_an_uploaded_profile() -> None:
    uploaded = acra(company_name="SOMETHING ELSE LTD.")
    assert acra_record(pack(acra_profiles=[uploaded])) == uploaded
    record = acra_record(_fetched(acra_profiles=[uploaded]))
    assert record is not None and (record.doc_id, record.company_name) == ("ACRA", "ACMA LTD.")


def test_records_that_were_fetched_raise_nothing() -> None:
    assert check_official_records(_fetched()) == []


def test_records_switched_off_are_noted() -> None:
    (finding,) = check_official_records(pack())
    assert (finding.check_id, finding.category, finding.severity) == ("OFFICIAL_RECORDS_UNAVAILABLE", "completeness", "info")


def test_records_that_could_not_be_fetched_are_an_open_item(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(official, "_client", httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x"))
    (finding,) = check_official_records(_fetched())
    assert (finding.check_id, finding.category, finding.severity) == ("OFFICIAL_RECORDS_UNAVAILABLE", "completeness", "medium")
    assert "could not be fetched" in finding.explanation


def test_a_guarantor_is_covered_by_the_notice_iras_holds() -> None:
    chairman = Guarantor(name="TAN BEE HUAT", id_number="S2015888E", nationality="SINGAPORE", residential_address=Address())
    manifest = ApplicationManifest(
        application_id="A1", application_date=date(2026, 10, 1), company_name="ACMA LTD.", uen="196500233E",
        country_of_incorporation="SINGAPORE", date_of_incorporation=date(1965, 10, 9), registered_address=Address(),
        authorised_signatories=["TAN BEE HUAT"], guarantors=[chairman],
    )
    assert guarantors_without_noa(pack(manifest=manifest)) == [chairman]
    assert guarantors_without_noa(_fetched(manifest=manifest)) == []


def test_an_iras_notice_that_disagrees_with_acra_is_reported_against_the_application() -> None:
    acma = _fetched()
    assert acma.official is not None and check_acra_persons(acma) == []
    # ACRA moves the chairman to another address; IRAS still has the old one
    record = acma.official.applicant()
    assert record is not None
    moved = record.model_copy(update={"officers": [record.officers[0].model_copy(update={"postal_code": "987654"}),
                                                   *record.officers[1:]]})
    official = acma.official.model_copy(update={"companies": {record.uen: moved}})
    findings = check_acra_persons(acma.model_copy(update={"official": official, "individual_self_certs": []}))
    assert [(f.check_id, f.document, f.severity) for f in findings] == [("XDOC_ACRA_PERSON", "pack", "medium")]
    assert "IRAS" in findings[0].explanation


def test_an_applicant_uen_acra_does_not_know_is_reported_on_the_form_that_gives_it() -> None:
    assert check_registry_entities(_fetched()) == []
    row = TaxResidencyRow(row=1, country="SINGAPORE", tin="199999999Z", reasons_ticked=[], explanation=None)
    (finding,) = check_registry_entities(_fetched(entity_self_certs=[cert(tax_rows=[row])]))
    assert (finding.check_id, finding.document, finding.severity) == ("REGISTRY_ENTITY_NOT_FOUND", "ACMA ENTITY", "high")
    assert "199999999Z" in finding.explanation


# ---- an uploaded copy against the original ------------------------------------------------------

def _official_figures(**kw) -> dict:
    record = official.company("196500233E")
    assert record is not None and record.financials is not None
    return {**record.model_dump(exclude={"doc_id", "page", "source_quotes"}), "doc_id": "ACMA_ACRA", **kw}


def test_a_true_copy_of_the_acra_record_passes() -> None:
    copy = acra(**_official_figures())
    assert check_official_copies(_fetched(acra_profiles=[copy])) == []


def test_an_altered_acra_profile_is_caught_field_by_field() -> None:
    fields = _official_figures()
    financials = acra_financials(**{**fields["financials"], "revenue": Decimal("17.935")})
    copy = acra(**{**fields, "status": "LIVE COMPANY", "registered_address": "1 RAFFLES PLACE (048616)",
                   "financials": financials})
    findings = check_official_copies(_fetched(acra_profiles=[copy]))
    assert {(f.check_id, f.document, f.severity) for f in findings} == {("XDOC_OFFICIAL_RECORD", "ACMA_ACRA", "critical")}
    assert sorted(f.observed for f in findings) == ["1 RAFFLES PLACE (048616)", "17.935"]


def test_an_acra_profile_with_officers_acra_does_not_list_is_caught() -> None:
    fields = _official_figures()
    officers = [*fields["officers"], {**fields["officers"][0], "name": "MATTHEW TAN", "id_number": "S1111111A"}]
    (finding,) = check_official_copies(_fetched(acra_profiles=[acra(**{**fields, "officers": officers})]))
    assert "MATTHEW TAN" in finding.explanation and "S1111111A" not in finding.explanation
    assert finding.severity == "critical"


def test_a_true_copy_of_the_notice_passes() -> None:
    copy = noa(income=Decimal("131862.00"), deductions=Decimal("22540.00"), chargeable_income=Decimal("109322.00"),
               tax_payable=Decimal("6522.03"), postal_code="478236")
    assert check_official_copies(_fetched(notices_of_assessment=[copy])) == []


def test_a_notice_with_inflated_income_is_caught() -> None:
    copy = noa(income=Decimal("231862.00"), deductions=Decimal("22540.00"), chargeable_income=Decimal("209322.00"),
               tax_payable=Decimal("6522.03"), postal_code="478236")
    findings = check_official_copies(_fetched(notices_of_assessment=[copy]))
    assert sorted(f.observed for f in findings) == ["209322.00", "231862.00"]
    assert {(f.check_id, f.document, f.severity) for f in findings} == {("XDOC_OFFICIAL_RECORD", "acma_NOA", "critical")}


def test_a_notice_for_another_year_is_noted_not_compared() -> None:
    (finding,) = check_official_copies(_fetched(notices_of_assessment=[noa(year_of_assessment=2023, income=Decimal(1))]))
    assert (finding.check_id, finding.severity) == ("XDOC_OFFICIAL_RECORD", "info")


def test_nothing_is_compared_without_fetched_records() -> None:
    assert check_official_copies(pack(acra_profiles=[acra(company_name="X")], notices_of_assessment=[noa()])) == []
