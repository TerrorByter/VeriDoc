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
from typing import Any

import pdfplumber
import pytest

from gldb import demo_packs, demo_trade, forensics, official
from gldb.ai_team.checks import bank_rows, check_pattern, counterparty_id
from gldb.config import GST_RATE, PACKS_DIR
from gldb.evaluate import evaluate_pack
from gldb.extract_stub import _parse_acra_profile, parse_bank_statement_form
from gldb.intake import document_paths, load_manifest
from gldb.pipeline import assess
from gldb.schemas import Lead, LeadTest, Pattern, SpecialistName

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
    for supplier in demo_packs.suppliers():
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
    return demo_packs.trades(case)


def test_a_pack_is_the_samples_four_files_the_application_and_the_paperwork_of_its_trade(tmp_path: Path) -> None:
    case = next(c for c in demo_packs.CASES if c.name == "inflated_financials")
    demo_packs.build_pack(case, tmp_path / case.name)
    trades = _trades(case)
    trade_files = {name for t in trades for name in (
        f"invoice_{t.invoice.number}.pdf", f"purchase_order_{t.invoice.po_number}.pdf",
        f"{'delivery_order' if t.purchase else 'bill_of_lading'}_{t.shipment.number}.pdf")}
    assert len(trade_files) == 21 and sum(t.purchase for t in trades) == 5
    assert sorted(p.name for p in (tmp_path / case.name).iterdir()) == sorted({
        "Submitted financials.xlsx", "application.json", "bank_statement.pdf", "fatca_crs_entity.pdf",
        "fatca_crs_individual.pdf", "fatca_crs_individual_2.pdf", "ground_truth.json", *trade_files})
    statement = parse_bank_statement_form("s", tmp_path / case.name / "bank_statement.pdf")
    assert statement.account_holder == case.company.name
    assert statement.closing_balance == statement.transactions[-1].balance


@pytest.mark.parametrize("case", [c for c in demo_packs.CASES if not (c.trade_faults or c.tamper_credit or c.paid_in_parts)],
                         ids=lambda c: c.name)
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
    assert payments["CME-4101"] == "missing" and {n: s for n, s in payments.items() if n != "CME-4101"} == {
        n: "matched" for n in payments if n != "CME-4101"}
    assert all(step.status == "matched" for c in result.chains for step in c.steps[:2])


def test_the_mismatch_pack_breaks_each_chain_where_its_paperwork_was_made_to_disagree() -> None:
    result = assess("invoice_mismatch", document_paths(PACKS / "invoice_mismatch"), load_manifest(PACKS / "invoice_mismatch"),
                    team=None)
    steps = {c.invoice_number: {s.step: s.status for s in c.steps} for c in result.chains}
    matched = {"purchase_order": "matched", "bill_of_lading": "matched", "payment": "matched"}
    assert steps["OKF-6201"] == {**matched, "purchase_order": "contradicts", "payment": "missing"}  # overbilled
    assert steps["OKF-6202"] == {**matched, "bill_of_lading": "contradicts"}  # billed more goods than shipped
    assert steps["JSS-260122"] == {**matched, "purchase_order": "contradicts", "payment": "missing"}  # a supplier overbilled
    assert steps["TLE-260325"] == {**matched, "purchase_order": "missing"}  # names an order never issued
    assert sorted(n for n, s in steps.items() if s == matched) == ["BWC-251218", "KTC-260518", "SMT-260215"]


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


# ---------------------------------------------------------------------------
# The documents are what a bank would expect of each company
# ---------------------------------------------------------------------------

def test_acmas_statement_is_the_teams_row_for_row() -> None:
    team = parse_bank_statement_form("team", demo_packs.STATEMENT)
    ours = parse_bank_statement_form("ours", PACKS / "clean" / "bank_statement.pdf")
    assert [(t.date, t.description, t.credit, t.debit, t.balance) for t in ours.transactions] == [
        (t.date, t.description, t.credit, t.debit, t.balance) for t in team.transactions]


def test_every_other_company_trades_with_its_own_partners_in_its_own_goods_on_its_own_days() -> None:
    team = parse_bank_statement_form("team", demo_packs.STATEMENT).transactions
    companies = [c for c in demo_packs.companies() if c is not demo_packs.ACMA]
    for company in companies:
        rows = demo_packs._scaled_rows(company, None)[1]
        assert [t.date.month for t in rows] == [t.date.month for t in team]
        assert [t.date for t in rows] != [t.date for t in team], company.name
    everyone = [*demo_packs.ACMA.partners, *(p for c in companies for p in c.partners)]
    for what in ("name", "code"):
        values = [getattr(p, what) for p in everyone]
        assert len(set(values)) == len(values), what
    sold = {c.name: {p.goods[0] for p in c.partners} for c in demo_packs.companies()}
    assert all(not (sold[a] & sold[b]) for a in sold for b in sold if a < b)
    prefixes = [c.invoice_prefix for c in demo_packs.companies()]
    assert len(set(prefixes)) == len(prefixes)


def test_the_suppliers_bank_at_many_branches() -> None:
    branches = {s.bank for s in demo_packs.suppliers()}
    assert len(branches) > 10 and all(b.count(", ") == 1 and b.endswith(" Branch") for b in branches)


def test_no_two_people_share_a_home_or_a_birthday() -> None:
    people = [p for c in demo_packs.companies() for p in (c.chairman, c.ceo)]
    for what in (lambda p: p.home, lambda p: p.born, lambda p: p.id_number):
        values = [what(p) for p in people]
        assert len(set(values)) == len(values)


@pytest.mark.parametrize("case", demo_packs.CASES, ids=lambda c: c.name)
def test_sales_are_zero_rated_exports_shipped_to_malaysia_and_purchases_carry_gst_and_are_delivered(case: demo_packs.Case) -> None:
    from gldb.extract_stub import parse_pack
    from gldb.intake import intake

    paths = document_paths(PACKS / case.name)
    pack = parse_pack(case.name, intake(case.name, paths), paths, None)
    sales = [i for i in pack.invoices if i.seller_uen == case.company.uen]
    purchases = [i for i in pack.invoices if i.seller_uen != case.company.uen]
    trades = _trades(case)
    assert (len(sales), len(purchases)) == (sum(not t.purchase for t in trades), sum(t.purchase for t in trades))
    assert all(i.gst_rate == 0 and i.gst == 0 for i in sales) and all(i.gst_rate == GST_RATE for i in purchases)
    shipped = {b.invoice_reference: b for b in pack.bills_of_lading}
    delivered = {d.invoice_reference: d for d in pack.delivery_orders}
    assert set(shipped) == {i.invoice_number for i in sales} and set(delivered) == {i.invoice_number for i in purchases}
    assert {b.port_of_discharge for b in shipped.values()} <= {"Port Klang", "Penang", "Pasir Gudang", "Kuantan"}
    assert all(b.port_of_loading == "Singapore" for b in shipped.values())


@pytest.mark.parametrize("case", demo_packs.CASES, ids=lambda c: c.name)
def test_the_entity_form_ticks_listed_for_acma_and_an_active_business_for_a_private_company(case: demo_packs.Case) -> None:
    from gldb.extract_stub import parse_entity_self_cert

    form = parse_entity_self_cert("e", PACKS / case.name / "fatca_crs_entity.pdf")
    assert form.entity_types_ticked == (["K"] if case.company.name == "ACMA LTD." else ["G"])


@pytest.mark.parametrize("case", demo_packs.CASES, ids=lambda c: c.name)
def test_every_signatory_and_guarantor_has_an_individual_form(case: demo_packs.Case) -> None:
    from gldb.extract_stub import parse_individual_self_cert

    manifest = load_manifest(PACKS / case.name)
    assert manifest is not None
    forms = [parse_individual_self_cert(p.stem, p) for p in sorted((PACKS / case.name).glob("fatca_crs_individual*.pdf"))]
    declared = {f.declaration_id_number for f in forms}
    people = {case.company.chairman.name: case.company.chairman.id_number, case.company.ceo.name: case.company.ceo.id_number}
    assert {people[name] for name in manifest.authorised_signatories} <= declared
    assert {g.id_number for g in manifest.guarantors} <= declared


@pytest.mark.parametrize("case", demo_packs.CASES, ids=lambda c: c.name)
def test_the_company_prints_its_office_on_its_invoices_as_its_bank_statement_does(case: demo_packs.Case) -> None:
    statement_line = demo_packs.seller_of(case.company).address[0]
    office = case.company.office
    assert statement_line.startswith(f"{office.block} {office.street.title()}, #{office.floor}-{office.unit}")
    for trade in (t for t in _trades(case) if not t.purchase):
        assert trade.invoice.seller_address[0] == statement_line


# ---------------------------------------------------------------------------
# Beyond the rules: what only the AI team can find, and code confirms
# ---------------------------------------------------------------------------

def _assessed(name: str):
    return assess(name, document_paths(PACKS / name), load_manifest(PACKS / name), team=None)


def _lead(pattern: Pattern, **subject: Any) -> Lead:
    agent: SpecialistName = "kyc" if pattern == "related_party" else "transaction"
    return Lead(agent=agent, kind="pattern", pattern=pattern, hypothesis="x", investigation_status="supported",
                tests=[LeadTest(cites=["R01"], expected="x", observed="x")], **subject)


def _row(result, description: str) -> str:
    return next(row_id for row_id, _, t in bank_rows(result.pack) if description in t.description)


def test_the_third_party_payment_is_confirmed_by_code_from_who_the_statement_says_paid() -> None:
    result = _assessed("third_party_payment")
    invoice = next(i for i in result.pack.invoices if i.buyer_name == "Kulim Hi-Tech Packaging Sdn. Bhd.")
    outcome = check_pattern(_lead("third_party_payment", invoice_number=invoice.invoice_number,
                                  transaction_ids=[_row(result, invoice.invoice_number)]), result.pack, result.evidence)
    assert outcome.status == "verified" and outcome.facts["payer"] == "GOLDEN REEF TRADING LIMITED"
    assert "rule_finding" in outcome.facts  # the payer rule reports it: the lead confirms it
    # Every other sale was paid by its own buyer
    for other in (i for i in result.pack.invoices if i is not invoice and i.seller_uen == demo_packs.KESTREL.uen):
        lead = _lead("third_party_payment", invoice_number=other.invoice_number,
                     transaction_ids=[_row(result, other.invoice_number)])
        assert check_pattern(lead, result.pack, result.evidence).status == "contradicted"


def test_the_related_supplier_is_confirmed_by_code_from_acra_to_be_run_by_the_applicants_ceo() -> None:
    result = _assessed("related_party")
    party = counterparty_id(result.pack, "Harbour Ridge Filtration Pte. Ltd.")
    outcome = check_pattern(_lead("related_party", counterparty=party), result.pack, result.evidence)
    assert outcome.status == "verified" and outcome.facts["shared_officer"] == demo_packs.LUMEN.ceo.name
    # Its other suppliers share no one with it
    other = counterparty_id(result.pack, "Paya Lebar Pumps Pte. Ltd.")
    assert check_pattern(_lead("related_party", counterparty=other), result.pack, result.evidence).status == "contradicted"


def test_the_round_trip_is_confirmed_by_code_from_the_money_coming_back() -> None:
    result = _assessed("round_trip")
    out = _row(result, "Factory Machine - Kranji Refrigeration")
    outcome = check_pattern(_lead("round_trip", transaction_ids=[out]), result.pack, result.evidence)
    assert outcome.status == "verified"
    assert outcome.facts["back_transaction"] == _row(result, f"{demo_packs.ROUND_TRIP_RECEIPT} - Kranji Refrigeration")
    # Another supplier's payment does not come back
    other = _row(result, "Reach Trucks - Benoi Material Handling")
    assert check_pattern(_lead("round_trip", transaction_ids=[other]), result.pack, result.evidence).status == "contradicted"


def test_the_rules_fast_track_the_sale_its_buyer_paid_in_parts() -> None:
    result = _assessed("split_payment")
    assert result.routing is not None and result.routing.path == "fast_track"
    assert not [e for e in result.evidence if e.severity != "info"]
    assert any(e.check_id == "XDOC_INVOICE_BANK" and "paid in 2 parts by its buyer" in e.explanation
               for e in result.evidence)


def test_the_rules_send_the_third_party_payment_for_review() -> None:
    result = _assessed("third_party_payment")
    assert result.routing is not None and result.routing.path == "standard_review"
    assert [e.check_id for e in result.evidence if e.severity != "info"] == ["XDOC_THIRD_PARTY_PAYER"]


@pytest.mark.parametrize("name", ["related_party", "round_trip"])
def test_the_rules_alone_would_fast_track_what_only_the_ai_team_can_find(name: str) -> None:
    result = _assessed(name)
    assert result.routing is not None and result.routing.path == "fast_track"
    assert not [e for e in result.evidence if e.severity != "info"]
