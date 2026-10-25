"""
tests/test_screening.py
-----------------------
Reference data (the synthetic sanctions list), deterministic name screening,
and the Registry & Screening agent against ACRA's records (the in-process
mock service). No AWS calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gldb import reference, screening
from gldb.checks import (
    AGENTS,
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
    assert reference.versions() == {"sanctions": watchlist.version}


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


def test_signatory_must_be_a_registered_officer() -> None:
    (ev,) = check_registry_signatories(_clean_with(authorised_signatories=["Tan Wei Ming", "Peter Lim"]))
    assert (ev.check_id, ev.severity) == ("REGISTRY_SIGNATORY_NOT_OFFICER", "medium")
    assert ev.observed == "Peter Lim"


def test_agent_and_signals_are_registered() -> None:
    agent = next(a for a in AGENTS if a.name == "Registry & Screening")
    assert agent.checks == [check_official_records, check_official_copies, check_registry_entities,
                            check_registry_signatories, check_sanctions]
    for check_id, signal_id in [
        ("XDOC_OFFICIAL_RECORD", "REG-009"), ("OFFICIAL_RECORDS_UNAVAILABLE", "REG-010"),
        ("REGISTRY_ENTITY_NOT_FOUND", "REG-002"), ("REGISTRY_NAME_MISMATCH", "REG-003"),
        ("REGISTRY_ENTITY_INACTIVE", "REG-004"), ("REGISTRY_SIGNATORY_NOT_OFFICER", "REG-005"),
        ("SANCTIONS_MATCH", "COMP-030"),
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
