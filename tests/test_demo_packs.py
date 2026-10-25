"""
tests/test_demo_packs.py
------------------------
The demo packs (gldb.demo_packs): edited copies of the team's sample, each
company's ACRA and IRAS records in the mock service's data. Every pack in
data/packs must meet its ground truth through the whole pipeline (stub
extraction, official records in-process). No AWS calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pdfplumber
import pytest

from gldb import demo_packs, forensics, official
from gldb.config import PACKS_DIR
from gldb.evaluate import evaluate_pack
from gldb.extract_stub import _parse_acra_profile, parse_bank_statement_form

PACKS = Path(PACKS_DIR)


@pytest.mark.parametrize("case", demo_packs.CASES, ids=lambda c: c.name)
def test_each_pack_meets_its_ground_truth(case: demo_packs.Case) -> None:
    result, _ = evaluate_pack(PACKS / case.name)
    assert result.error is None
    assert (result.band, result.readiness, result.path) == (
        case.expected_band, case.expected_readiness, case.expected_path)
    assert (result.missed, result.unexpected, result.untraceable) == ([], [], [])
    assert all(attack.detected for attack in result.attacks)


def test_the_packs_on_disk_are_the_cases() -> None:
    assert sorted(p.name for p in PACKS.iterdir() if p.is_dir()) == sorted(c.name for c in demo_packs.CASES)
    for case in demo_packs.CASES:
        truth = json.loads((PACKS / case.name / "ground_truth.json").read_text())
        assert truth == demo_packs._truth(case)


def test_the_mock_service_holds_each_companys_records() -> None:
    acra = {c["uen"]: c for c in json.loads((demo_packs.MOCK_GOV_DIR / "acra.json").read_text())["companies"]}
    iras = {n["id_number"]: n for n in json.loads((demo_packs.MOCK_GOV_DIR / "iras.json").read_text())["notices"]}
    for company in {c.company.uen: c.company for c in demo_packs.CASES}.values():
        assert acra[company.uen] == demo_packs.acra_record(company)
        assert all(iras[n["id_number"]] == n for n in demo_packs.iras_records(company))


def test_the_mock_service_holds_acmas_profile_as_the_team_issued_it() -> None:
    """ACMA's record is the team's ACRA profile (the sample), field for field, so uploading that PDF as a copy
    differs from the original in nothing. Matthew Tan, who signs as CEO, is not on it: a new CEO ACRA does not
    list yet, which the demo keeps rather than edit the profile."""
    with pdfplumber.open(demo_packs.SAMPLE_DIR / "ACRA UPDATED.pdf") as pdf:
        profile = _parse_acra_profile("ACRA UPDATED", [page.extract_text() or "" for page in pdf.pages])
    record = official.company(profile.uen)
    assert record is not None
    issued = {"doc_id", "issued_on", "page", "source_quotes"}
    assert record.model_dump(exclude=issued) == profile.model_dump(exclude=issued)
    assert [o.name for o in record.officers] == ["TAN BEE HUAT"]
    assert record.officers[0].postal_code == "478236"  # the chairman's address, as the notice and the individual form give it


def test_a_pack_is_the_samples_four_files_and_the_application(tmp_path: Path) -> None:
    case = next(c for c in demo_packs.CASES if c.name == "inflated_financials")
    demo_packs.build_pack(case, tmp_path / case.name)
    assert sorted(p.name for p in (tmp_path / case.name).iterdir()) == [
        "Submitted financials.xlsx", "application.json", "bank_statement.pdf", "fatca_crs_entity.pdf",
        "fatca_crs_individual.pdf", "ground_truth.json",
    ]
    statement = parse_bank_statement_form("s", tmp_path / case.name / "bank_statement.pdf")
    assert statement.account_holder == case.company.name
    assert statement.closing_balance == statement.transactions[-1].balance


def test_only_the_tampered_statement_was_saved_twice() -> None:
    for case in demo_packs.CASES:
        revisions = forensics.incremental_updates(PACKS / case.name / "bank_statement.pdf")
        assert (revisions > 0) is (case.tamper_credit is not None), case.name


def test_the_api_offers_the_demo_packs() -> None:
    from fastapi.testclient import TestClient

    from gldb.api import app

    offered = TestClient(app).get("/api/packs").json()
    assert offered == [{"name": c.name, "note": c.note} for c in sorted(demo_packs.CASES, key=lambda c: c.name)]
