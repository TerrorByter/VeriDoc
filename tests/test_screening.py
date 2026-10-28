"""
tests/test_screening.py
-----------------------
Reference data (the synthetic sanctions list), deterministic name screening,
and the Registry & Screening rule group against ACRA's records (the in-process
mock service). No AWS calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gldb import reference, screening
from gldb.checks import (
    RULE_GROUPS,
    check_high_risk_countries,
    check_official_copies,
    check_official_records,
    check_registry_entities,
    check_registry_signatories,
    check_sanctions,
)
from gldb.config import SCREENING_MATCH_THRESHOLD
from gldb.data_factory import ENTITY_TEMPLATE, generate_packs
from gldb.pipeline import pack_from_folder
from gldb.schemas import ExtractedPack
from gldb.signal_library import BY_CHECK

PACKS = Path(__file__).parent / "fixtures" / "packs"


def test_reference_data_is_versioned_and_dated() -> None:
    watchlist = reference.watchlist()
    assert watchlist.version and watchlist.as_of and watchlist.entries
    assert reference.versions() == {
        "sanctions": watchlist.version, "high_risk_countries": reference.high_risk_countries().version}


@pytest.mark.parametrize(("a", "b", "same"), [
    ("Kestrel Marine Supplies Pte. Ltd.", "KESTREL MARINE SUPPLIES LIMITED", True),
    ("Tan Wei Ming", "TAN, Wei-Ming", True),
    ("Ming Tan Wei", "Tan Wei Ming", True),          # word order
    ("Bayview Marine Supplies Pte. Ltd.", "Kestrel Marine Supplies Limited", False),
    ("Orchid Lane Retail", "Orchid Lane Holdings", False),
])
def test_name_similarity_is_deterministic_and_suffix_blind(a: str, b: str, same: bool) -> None:
    assert (screening.similarity(a, b) >= SCREENING_MATCH_THRESHOLD) is same
    assert screening.similarity(a, b) == screening.similarity(b, a)


def test_clean_pack_passes_registry_and_screening() -> None:
    pack = pack_from_folder(PACKS / "clean")
    assert check_registry_entities(pack) == []
    assert check_registry_signatories(pack) == []
    assert check_sanctions(pack) == []


def test_unregistered_uen_is_a_registry_finding() -> None:
    pack = pack_from_folder(PACKS / "tampered")
    (ev,) = check_registry_entities(pack)
    assert (ev.check_id, ev.severity, ev.document) == (
        "REGISTRY_ENTITY_NOT_FOUND", "high", "invoice_INV-1002",
    )
    assert ev.observed is not None and "202301284K" in ev.observed
    assert pack.official is not None and pack.official.versions["acra"] in ev.explanation


def _clean_with(**manifest_changes: object) -> ExtractedPack:
    pack = pack_from_folder(PACKS / "clean")
    assert pack.manifest is not None
    return pack.model_copy(update={"manifest": pack.manifest.model_copy(update=manifest_changes)})


def test_name_mismatch_against_the_registry() -> None:
    found = check_registry_entities(_clean_with(company_name="Lumen Harbor Holdings Pte. Ltd."))
    assert [e.check_id for e in found] == ["REGISTRY_NAME_MISMATCH"]


def _with_acra(pack: ExtractedPack, uen: str, **record: object) -> ExtractedPack:
    """*pack* with ACRA's record of *uen* changed."""
    assert pack.official is not None
    changed = pack.official.companies[uen].model_copy(update=record)
    return pack.model_copy(update={"official": pack.official.model_copy(update={"companies": {**pack.official.companies, uen: changed}})})


def test_a_struck_off_applicant_is_one_finding_however_many_documents_name_it() -> None:
    """The clean pack names its applicant on the application, two invoices and two orders."""
    pack = pack_from_folder(PACKS / "clean")
    assert pack.official is not None and pack.official.applicant_uen
    (ev,) = check_registry_entities(_with_acra(pack, pack.official.applicant_uen, status="STRUCK OFF"))
    assert (ev.check_id, ev.severity, ev.document) == ("REGISTRY_ENTITY_INACTIVE", "high", "pack")
    assert "also named on invoice_INV-1001, invoice_INV-1002, purchase_order_PO-2001, purchase_order_PO-2002" in ev.explanation


def test_an_unregistered_uen_on_several_documents_is_one_finding_on_the_first() -> None:
    pack = pack_from_folder(PACKS / "clean")
    assert pack.official is not None
    unknown = pack.model_copy(update={"official": pack.official.model_copy(update={"companies": {}})})
    found = [e for e in check_registry_entities(unknown) if e.check_id == "REGISTRY_ENTITY_NOT_FOUND"]
    assert len(found) == len({e.observed for e in found}) == 1
    assert found[0].document == "pack" and "also named on invoice_INV-1001" in found[0].explanation


def test_each_wrong_name_for_a_company_is_a_finding_once() -> None:
    pack = pack_from_folder(PACKS / "clean")
    renamed = [i.model_copy(update={"seller_name": "Lumen Harbor Holdings Pte. Ltd."}) for i in pack.invoices]
    found = check_registry_entities(pack.model_copy(update={"invoices": renamed}))
    assert [(e.check_id, e.document) for e in found] == [("REGISTRY_NAME_MISMATCH", "invoice_INV-1001")]


def test_two_struck_off_companies_are_two_findings() -> None:
    pack = pack_from_folder(PACKS / "clean")
    assert pack.official is not None and pack.official.applicant_uen
    other = pack.official.companies[pack.official.applicant_uen].model_copy(update={"uen": "999999999Z", "status": "STRUCK OFF"})
    both = _with_acra(pack, pack.official.applicant_uen, status="STRUCK OFF")
    assert both.official is not None
    renumbered = [pack.invoices[0].model_copy(update={"seller_uen": other.uen, "seller_name": other.company_name}), *pack.invoices[1:]]
    with_other = both.model_copy(update={
        "invoices": renumbered,
        "official": both.official.model_copy(update={"companies": {**both.official.companies, other.uen: other}}),
    })
    assert sorted(e.observed or "" for e in check_registry_entities(with_other) if e.check_id == "REGISTRY_ENTITY_INACTIVE") == [
        "STRUCK OFF", "STRUCK OFF"]


def test_signatory_must_be_a_registered_officer() -> None:
    (ev,) = check_registry_signatories(_clean_with(authorised_signatories=["Tan Wei Ming", "Peter Lim"]))
    assert (ev.check_id, ev.severity) == ("REGISTRY_SIGNATORY_NOT_OFFICER", "medium")
    assert ev.observed == "Peter Lim"


def test_group_and_signals_are_registered() -> None:
    group = next(g for g in RULE_GROUPS if g.name == "Registry & Screening")
    assert group.checks == [check_official_records, check_official_copies, check_registry_entities,
                            check_registry_signatories, check_sanctions, check_high_risk_countries]
    for check_id, signal_id in [
        ("XDOC_OFFICIAL_RECORD", "REG-009"), ("OFFICIAL_RECORDS_UNAVAILABLE", "REG-010"),
        ("REGISTRY_ENTITY_NOT_FOUND", "REG-002"), ("REGISTRY_NAME_MISMATCH", "REG-003"),
        ("REGISTRY_ENTITY_INACTIVE", "REG-004"), ("REGISTRY_SIGNATORY_NOT_OFFICER", "REG-005"),
        ("SANCTIONS_MATCH", "COMP-030"), ("HIGH_RISK_COUNTRY", "COMP-031"),
    ]:
        assert BY_CHECK[check_id].signal_id == signal_id


@pytest.fixture(scope="module")
def packs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not ENTITY_TEMPLATE.exists():
        pytest.skip("AcroForm templates not present")
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base


def test_sanctioned_pack_buyer_is_screened(packs: Path) -> None:
    pack = pack_from_folder(packs / "sanctioned")
    hits = check_sanctions(pack)
    assert {e.check_id for e in hits} == {"SANCTIONS_MATCH"}
    assert all(e.severity == "high" for e in hits)
    assert any("Kestrel Marine Supplies" in (e.observed or "") for e in hits)
    truth = json.loads((packs / "sanctioned" / "ground_truth.json").read_text())
    assert (truth["expected_band"], truth["expected_path"]) == ("Medium", "enhanced_review")
    assert truth["expected_check_ids"] == ["SANCTIONS_MATCH"]


def test_the_people_acra_and_the_forms_name_are_screened() -> None:
    from datetime import datetime, timezone

    from gldb.schemas import AcraShareholder, OfficialRecords
    from tests.acma_records import acra, cert, indiv, pack

    record = acra(shareholders=[AcraShareholder(name="VIKTOR ALDANE", shares=1000000)])
    official = OfficialRecords(retrieved_at=datetime.now(timezone.utc), applicant_uen=record.uen,
                               companies={record.uen: record})
    (hit,) = check_sanctions(pack(official=official, entity_self_certs=[cert()], individual_self_certs=[indiv()]))
    assert (hit.check_id, hit.document, hit.quote) == ("SANCTIONS_MATCH", "pack", "VIKTOR ALDANE")
    assert "shareholder" in hit.explanation

    signed = cert(signatories=[cert().signatories[0].model_copy(update={"name": "Amira Solenko"})])
    (hit,) = check_sanctions(pack(entity_self_certs=[signed]))
    assert (hit.document, hit.quote) == ("ACMA ENTITY", "Amira Solenko") and "signatory" in hit.explanation
    (hit,) = check_sanctions(pack(individual_self_certs=[indiv(full_name="Amira Solenko")]))
    assert hit.document == "ACMA INDIV" and "declarant" in hit.explanation


# ---------------------------------------------------------------------------
# Everyone the case names is screened, and a date of birth decides a name match
# ---------------------------------------------------------------------------

def test_a_controlling_person_on_the_entity_form_is_screened() -> None:
    from gldb.schemas import ControllingPerson
    from tests.acma_records import cert, pack

    controller = ControllingPerson(slot=2, name="Viktor Aldane", types_ticked=["legal_ownership"], page=3)
    (hit,) = check_sanctions(pack(entity_self_certs=[cert(controlling_persons=[controller])]))
    assert (hit.document, hit.page, hit.quote, hit.severity) == ("ACMA ENTITY", 3, "Viktor Aldane", "high")
    assert "controlling person" in hit.explanation


def test_the_people_acra_lists_for_a_counterparty_are_screened() -> None:
    from datetime import datetime, timezone

    from gldb.schemas import AcraShareholder, OfficialRecords
    from tests.acma_records import acra, pack

    applicant = acra()
    supplier = acra(uen="200709981R", company_name="STEELMEN PTE LTD.",
                    officers=[applicant.officers[0].model_copy(update={"name": "AMIRA SOLENKO"})],
                    shareholders=[AcraShareholder(name="VIKTOR ALDANE", shares=100)])
    official = OfficialRecords(retrieved_at=datetime.now(timezone.utc), applicant_uen=applicant.uen,
                               companies={applicant.uen: applicant, supplier.uen: supplier})
    hits = check_sanctions(pack(official=official))
    assert sorted(h.quote or "" for h in hits) == ["AMIRA SOLENKO", "VIKTOR ALDANE"]
    assert all("of STEELMEN PTE LTD. at ACRA" in h.explanation for h in hits)


def test_the_seller_on_a_purchase_order_is_screened() -> None:
    from datetime import date
    from decimal import Decimal

    from gldb.schemas import PurchaseOrder
    from tests.acma_records import pack

    order = PurchaseOrder(doc_id="purchase_order_PO-1", po_number="PO-1", po_date=date(2026, 1, 1), buyer_name="ACMA LTD.",
                          seller_name="Red Lantern Shipping Co", seller_uen="1", line_items=[], total=Decimal(1))
    (hit,) = check_sanctions(pack(purchase_orders=[order]))
    assert (hit.document, hit.quote) == ("purchase_order_PO-1", "Red Lantern Shipping Co") and "seller" in hit.explanation


@pytest.fixture
def listed_with_birth_dates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bank watchlist whose one person is listed with dates of birth: a day, a year and a range."""
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps({"source": "test", "version": "TEST-BORN", "as_of": "2026-10-01", "entries": [
        {"name": "Viktor Aldane", "kind": "individual", "list": "UN-SYN", "reference": "SYN-I-0311",
         "birth_dates": ["1971-03-02", "1968", "1980-1982"]}]}))
    monkeypatch.setattr(reference, "WATCHLIST_PATH", str(path))


@pytest.mark.parametrize(("born", "fits"), [
    ("1971-03-02", True), ("1968-11-30", True), ("1969-01-01", True), ("1981-06-15", True),
    ("1971-03-03", False), ("1990-05-05", False), ("1966-12-31", False),
])
def test_a_date_of_birth_fits_a_listed_day_a_year_give_or_take_one_or_a_range(born: str, fits: bool) -> None:
    from datetime import date

    from gldb.reference import WatchlistEntry

    entry = WatchlistEntry(name="X", kind="individual", list="L", reference="R",
                           birth_dates=["1971-03-02", "1968", "1980-1982"])
    assert screening.birth_date_fits(date.fromisoformat(born), entry) is fits


def test_an_unknown_date_of_birth_decides_nothing() -> None:
    from datetime import date

    from gldb.reference import WatchlistEntry

    entry = WatchlistEntry(name="X", kind="individual", list="L", reference="R")
    assert screening.birth_date_fits(None, entry) is None
    assert screening.birth_date_fits(date(1971, 3, 2), entry) is None


@pytest.mark.usefixtures("listed_with_birth_dates")
def test_a_name_match_whose_date_of_birth_differs_is_discounted_not_dropped() -> None:
    from datetime import date

    from gldb.routing import hard_flags
    from tests.acma_records import indiv, pack

    (hit,) = check_sanctions(pack(individual_self_certs=[indiv(full_name="Viktor Aldane", date_of_birth=date(1990, 5, 5))]))
    assert hit.severity == "info" and "Discounted" in hit.explanation and "05 May 1990" in hit.explanation
    assert hard_flags([hit]) == []


@pytest.mark.usefixtures("listed_with_birth_dates")
def test_a_name_match_whose_date_of_birth_fits_stands_and_says_so() -> None:
    from datetime import date

    from tests.acma_records import indiv, pack

    (hit,) = check_sanctions(pack(individual_self_certs=[indiv(full_name="Viktor Aldane", date_of_birth=date(1971, 3, 2))]))
    assert hit.severity == "high" and "fits" in hit.explanation


@pytest.mark.usefixtures("listed_with_birth_dates")
def test_the_declarants_date_of_birth_decides_for_the_same_person_named_elsewhere() -> None:
    from datetime import date

    from tests.acma_records import indiv, pack
    from tests.test_checks import _make_manifest

    case = pack(manifest=_make_manifest(authorised_signatories=[], guarantors=[
                    _make_manifest().guarantors[0].model_copy(update={"name": "VIKTOR ALDANE"})]),
                individual_self_certs=[indiv(full_name="Viktor Aldane", date_of_birth=date(1990, 5, 5))])
    (hit,) = check_sanctions(case)
    assert "guarantor" in hit.explanation and hit.severity == "info"
