"""
tests/test_official_records.py
------------------------------
The mock government records service (mock_gov) and the client the pipeline
fetches a case's official records with (gldb.official). The suite reaches the
service in-process (conftest), so no server needs to run.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient

from gldb import official
from gldb.schemas import Address, ApplicationManifest, Guarantor, TaxResidencyRow
from mock_gov.app import app
from tests.acma_records import cert, indiv, pack

ACMA = "196500233E"
CHAIRMAN = "S2015888E"


@pytest.fixture
def service() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# The mock service
# ---------------------------------------------------------------------------


def test_the_service_names_its_data_versions(service: TestClient) -> None:
    body = service.get("/").json()
    assert set(body) == {"acra", "iras"}
    assert body["acra"]["version"].startswith("ACRA-MOCK") and body["iras"]["version"].startswith("IRAS-MOCK")


def test_acra_answers_a_registered_uen_and_404s_an_unknown_one(service: TestClient) -> None:
    assert service.get(f"/acra/companies/{ACMA}").json()["company_name"] == "ACMA LTD."
    assert service.get("/acra/companies/199999999Z").status_code == 404


def test_acra_search_offers_companies_sharing_a_distinctive_word(service: TestClient) -> None:
    found = service.get("/acra/companies", params={"name": "Seabright Provisions"}).json()
    assert found == [{"uen": "202205551D", "company_name": "Seabright Provisions Pte. Ltd."}]
    assert service.get("/acra/companies", params={"name": "Pte Ltd"}).json() == []


def test_iras_answers_the_latest_notice_whatever_the_id_spacing(service: TestClient) -> None:
    body = service.get("/iras/notices/ s2015888e").json()
    assert (body["name"], body["year_of_assessment"]) == ("MR TAN BEE HUAT", 2025)
    assert service.get("/iras/notices/S0000000A").status_code == 404


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


def test_a_company_record_is_read_into_the_profile_schema() -> None:
    record = official.company(ACMA)
    assert record is not None and record.doc_id == official.ACRA_DOC
    assert record.financials is not None and record.financials.revenue == Decimal("7.935")
    assert [o.name for o in record.officers] == ["TAN BEE HUAT", "MATTHEW TAN"]  # the chairman and the CEO
    assert official.company("199999999Z") is None


def test_a_notice_is_read_into_the_notice_schema() -> None:
    notice = official.notice(CHAIRMAN)
    assert notice is not None and notice.doc_id == official.IRAS_DOC
    assert (notice.chargeable_income, notice.postal_code) == (Decimal("109322.00"), "478236")
    assert official.notice("S0000000A") is None


def test_search_returns_full_records_of_the_candidates() -> None:
    assert [c.uen for c in official.search("Seabright Provisions Pte Ltd")] == ["202205551D"]


def test_a_service_that_does_not_answer_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(official, "_client", httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x"))
    with pytest.raises(official.OfficialRecordsError, match="did not answer"):
        official.company(ACMA)


# ---------------------------------------------------------------------------
# What a case fetches
# ---------------------------------------------------------------------------


def _singapore_tin(tin: str) -> TaxResidencyRow:
    return TaxResidencyRow(row=1, country="SINGAPORE", tin=tin, reasons_ticked=[], explanation=None)



def test_the_applicant_and_its_people_come_from_the_forms_when_there_is_no_application() -> None:
    acma = pack(entity_self_certs=[cert()], individual_self_certs=[indiv()])
    records = official.fetch(acma)
    assert records.error is None and records.applicant_uen == ACMA
    assert records.applicant() is not None and records.applicant().company_name == "ACMA LTD."
    assert [n.name for n in records.notices] == ["MR TAN BEE HUAT"] and records.no_notice == []
    assert set(records.versions) == {"acra", "iras"}


def test_the_application_names_the_applicant_and_guarantors() -> None:
    guarantor = Guarantor(name="Someone", id_number="S0000000A", nationality="SG", residential_address=Address())
    manifest = ApplicationManifest(
        application_id="A1", application_date=date(2026, 10, 1), company_name="Lumen Harbour Trading Pte. Ltd.",
        uen="202301234K", country_of_incorporation="SINGAPORE", date_of_incorporation=date(2023, 1, 16),
        registered_address=Address(), authorised_signatories=[], guarantors=[guarantor],
    )
    records = official.fetch(pack(manifest=manifest))
    assert records.applicant_uen == "202301234K" and records.applicant() is not None
    assert records.notices == [] and records.no_notice == ["S0000000A"]


def test_an_unregistered_uen_is_recorded_as_not_found() -> None:
    records = official.fetch(pack(entity_self_certs=[cert(tax_rows=[_singapore_tin("199999999Z")])]))
    assert records.companies == {"199999999Z": None} and records.applicant() is None


def test_an_unreachable_service_is_recorded_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(official, "_client", httpx.Client(transport=httpx.MockTransport(refuse), base_url="http://x"))
    records = official.fetch(pack(entity_self_certs=[cert()]))
    assert records.error is not None and "did not answer" in records.error
    assert records.companies == {} and records.applicant_uen == ACMA
