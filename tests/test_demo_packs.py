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

from gldb import demo_packs, demo_trade, forensics, official
from gldb.config import PACKS_DIR
from gldb.evaluate import evaluate_pack
from gldb.extract_stub import _parse_acra_profile, parse_bank_statement_form
from gldb.intake import document_paths, load_manifest
from gldb.pipeline import assess

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


def test_the_mock_service_holds_each_suppliers_record() -> None:
    acra = {c["uen"]: c for c in json.loads((demo_packs.MOCK_GOV_DIR / "acra.json").read_text())["companies"]}
    for supplier in demo_trade.suppliers():
        assert acra[supplier.uen] == demo_trade.acra_record(supplier)


def test_the_mock_service_holds_acmas_profile_as_the_team_issued_it() -> None:
    """ACMA's record is the team's ACRA profile (the sample), field for field, so uploading that PDF as a copy
    differs from the original in nothing. It lists both officers: the chairman and Matthew Tan, the CEO who signs."""
    with pdfplumber.open(demo_packs.SAMPLE_DIR / "ACMA ACRA.pdf") as pdf:
        profile = _parse_acra_profile("ACMA ACRA", [page.extract_text() or "" for page in pdf.pages])
    record = official.company(profile.uen)
    assert record is not None
    issued = {"doc_id", "issued_on", "page", "source_quotes"}
    assert record.model_dump(exclude=issued) == profile.model_dump(exclude=issued)
    assert [o.name for o in record.officers] == ["TAN BEE HUAT", "MATTHEW TAN"]
    assert record.officers[0].postal_code == "478236"  # the chairman's address, as the notice and the individual form give it


def test_the_mock_service_holds_the_ceos_notice_as_the_team_issued_it() -> None:
    """Matthew Tan, ACMA's CEO, guarantees the clean case: IRAS's record of him is his notice, figure for figure."""
    from gldb.extract_stub import parse_pack
    from gldb.intake import intake

    path = demo_packs.SAMPLE_DIR / "acma_NOA (Matthew Tan).pdf"
    issued = parse_pack("t", intake("t", [path]), [path], None).notices_of_assessment[0]
    held = official.notice("S8294716J")
    assert held is not None
    fields = {"name", "id_number", "year_of_assessment", "income", "deductions", "chargeable_income", "tax_payable",
              "postal_code"}
    assert held.model_dump(include=fields) == issued.model_dump(include=fields)


def test_the_clean_case_has_both_guarantors_each_with_an_individual_form_as_the_team_filled_it() -> None:
    from gldb.extract_stub import parse_individual_self_cert
    from gldb.intake import read_text

    manifest = load_manifest(PACKS / "clean")
    assert manifest is not None
    assert [(g.name, g.id_number) for g in manifest.guarantors] == [("TAN BEE HUAT", "S2015888E"), ("MATTHEW TAN", "S8294716J")]
    ceo = parse_individual_self_cert("ceo", PACKS / "clean" / "fatca_crs_individual_2.pdf")
    home = ceo.residential_address
    shown = (ceo.full_name, ceo.date_of_birth.strftime("%d/%m/%Y") if ceo.date_of_birth else None, home.block, home.street,
             home.floor, home.unit, home.postal_code, ceo.declaration_id_number)
    assert shown == ("MATTHEW TAN", "14/03/1979", "412", "Jurong West Street 42", "09", "318", "650412", "S8294716J")
    team = "\n".join(read_text(demo_packs.SAMPLE_DIR / "ACMA INDIV (Matthew Tan).pdf"))
    assert all(value in team for value in shown)  # every value is the one the team's (flattened) form prints


def _trades(case: demo_packs.Case) -> list[demo_trade.Trade]:
    return demo_trade.plan(demo_packs.seller_of(case.company), demo_packs._scaled_rows(case.company, case.statement_until)[1],
                           case.trade_faults)


def test_a_pack_is_the_samples_four_files_the_application_and_the_paperwork_of_its_trade(tmp_path: Path) -> None:
    case = next(c for c in demo_packs.CASES if c.name == "inflated_financials")
    demo_packs.build_pack(case, tmp_path / case.name)
    trade_files = {name for t in _trades(case) for name in (
        f"invoice_{t.invoice.number}.pdf", f"purchase_order_{t.invoice.po_number}.pdf", f"bill_of_lading_{t.bill.number}.pdf")}
    assert len(trade_files) == 21
    assert sorted(p.name for p in (tmp_path / case.name).iterdir()) == sorted({
        "Submitted financials.xlsx", "application.json", "bank_statement.pdf", "fatca_crs_entity.pdf",
        "fatca_crs_individual.pdf", "ground_truth.json", *trade_files})
    statement = parse_bank_statement_form("s", tmp_path / case.name / "bank_statement.pdf")
    assert statement.account_holder == case.company.name
    assert statement.closing_balance == statement.transactions[-1].balance


@pytest.mark.parametrize("case", [c for c in demo_packs.CASES if not (c.trade_faults or c.tamper_credit)], ids=lambda c: c.name)
def test_every_sale_and_goods_purchase_of_a_pack_is_traced_to_its_order_shipment_and_payment(case: demo_packs.Case) -> None:
    result = assess(case.name, document_paths(PACKS / case.name), load_manifest(PACKS / case.name), team=None)
    trades = _trades(case)
    assert sorted(c.invoice_number for c in result.chains) == sorted(t.invoice.number for t in trades)
    assert all(not chain.broken for chain in result.chains)
    assert all(step.status == "matched" for chain in result.chains for step in chain.steps)
    payments = {c.invoice_number: c.steps[-1].counterpart.label.split()[1] for c in result.chains if c.steps[-1].counterpart}
    assert payments == {t.invoice.number: "debit" if t.purchase else "credit" for t in trades}


def test_the_tampered_statements_raised_credit_is_not_the_one_its_invoice_was_paid_by() -> None:
    folder = PACKS / "tampered_statement"
    result = assess("tampered_statement", document_paths(folder), load_manifest(folder), team=None)
    payments = {c.invoice_number: c.steps[-1].status for c in result.chains}
    assert payments["INV-1001"] == "missing" and {n: s for n, s in payments.items() if n != "INV-1001"} == {
        n: "matched" for n in payments if n != "INV-1001"}
    assert all(step.status == "matched" for c in result.chains for step in c.steps[:2])


def test_the_mismatch_pack_breaks_each_chain_where_its_paperwork_was_made_to_disagree() -> None:
    result = assess("invoice_mismatch", document_paths(PACKS / "invoice_mismatch"), load_manifest(PACKS / "invoice_mismatch"),
                    team=None)
    steps = {c.invoice_number: {s.step: s.status for s in c.steps} for c in result.chains}
    matched = {"purchase_order": "matched", "bill_of_lading": "matched", "payment": "matched"}
    assert steps["INV-1001"] == {**matched, "purchase_order": "contradicts", "payment": "missing"}  # overbilled
    assert steps["INV-1002"] == {**matched, "bill_of_lading": "contradicts"}  # billed more goods than shipped
    assert steps["SM-260128"] == {**matched, "purchase_order": "contradicts", "payment": "missing"}  # a supplier overbilled
    assert steps["CAT-260331"] == {**matched, "purchase_order": "missing"}  # names an order never issued
    assert [n for n, s in steps.items() if s == matched] == ["ABC-251219", "CR-260221", "TC-260518"]


def test_only_the_tampered_statement_was_saved_twice() -> None:
    for case in demo_packs.CASES:
        revisions = forensics.incremental_updates(PACKS / case.name / "bank_statement.pdf")
        assert (revisions > 0) is (case.tamper_credit is not None), case.name


def test_the_api_offers_the_demo_packs() -> None:
    from fastapi.testclient import TestClient

    from gldb.api import app

    offered = TestClient(app).get("/api/packs").json()
    assert offered == [{"name": c.name, "note": c.note, "expected_path": c.expected_path}
                       for c in sorted(demo_packs.CASES, key=lambda c: c.name)]
