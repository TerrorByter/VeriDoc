"""
tests/test_ai_team_checks.py
----------------------------
Code checks of the AI team's leads against the case data, with live run 5's
leads (CASE-20261004T163428Z-92b7) as regression fixtures: the planted
third-party payment verifies; invoice-after-shipment, a director-guarantor
who is not a signatory and the unchecked patterns never do. A condition code
cannot establish gives unverified, never a guess. Offline; no AWS.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from gldb import official

from gldb.ai_team.checks import (
    Outcome,
    bank_rows,
    check_pattern,
    corroborate,
    counterparties,
    payment_context,
    resolved_findings,
    shared_party,
    subject_locations,
)
from gldb.checks import check_temp_trade_sequence as check_trade_rules
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import Evidence, ExtractedPack, Lead
from gldb.screening import screening_key

PACKS = Path(__file__).parent / "fixtures" / "packs"


@cache
def _case(name: str) -> tuple[ExtractedPack, tuple[Evidence, ...]]:
    folder = PACKS / name
    result = assess(f"checks-{name}", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None)
    return result.pack, tuple(result.evidence)


def _rows(pack: ExtractedPack, text: str) -> list[str]:
    return [row_id for row_id, _, txn in bank_rows(pack) if text in txn.description]


def _party(pack: ExtractedPack, name: str) -> str:
    return counterparties(pack)[screening_key(name)]


def _lead(pattern: str | None, agent: str, kind: str = "pattern", **fields: Any) -> Lead:
    test = {"cites": ["R01"], "expected": "e", "observed": "o"}
    return Lead.model_validate({"agent": agent, "kind": kind, "pattern": pattern, "hypothesis": "h",
                                "tests": [test], "investigation_status": "supported", **fields})


def _tpp(pack: ExtractedPack, invoice: str, rows: list[str]) -> Lead:
    return _lead("third_party_payment", "transaction", invoice_number=invoice, transaction_ids=rows)


def _check(name: str, lead: Lead) -> Outcome:
    pack, evidence = _case(name)
    return check_pattern(lead, pack, list(evidence))


# ---------------------------------------------------------------------------
# third_party_payment
# ---------------------------------------------------------------------------


def test_tpp_l12_harbourline_is_verified() -> None:
    pack, _ = _case("third_party_payment")
    outcome = _check("third_party_payment", _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002")))
    assert outcome.status == "verified"
    assert (outcome.facts["payer"], outcome.facts["buyer"]) == ("HARBOURLINE LOGISTICS", "Bayview Marine Supplies Pte. Ltd.")


def test_tpp_names_the_rule_finding_it_confirms() -> None:
    """The rule XDOC_THIRD_PARTY_PAYER reports Harbourline's payment itself:
    the lead confirms that finding, so it is not a flag of its own."""
    pack, evidence = _case("third_party_payment")
    (rule,) = [e for e in evidence if e.check_id == "XDOC_THIRD_PARTY_PAYER"]
    outcome = _check("third_party_payment", _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002")))
    assert outcome.facts["rule_finding"] == rule.evidence_id
    # Without the rule's finding the same lead stands alone
    alone = check_pattern(_tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002")), pack,
                          [e for e in evidence if e.check_id != "XDOC_THIRD_PARTY_PAYER"])
    assert alone.status == "verified" and "rule_finding" not in alone.facts


def test_tpp_inv1001_orchid_lane_is_contradicted() -> None:
    pack, _ = _case("third_party_payment")
    outcome = _check("third_party_payment", _tpp(pack, "INV-1001", _rows(pack, "ORCHID LANE RETAIL INV-1001")))
    assert outcome.status == "contradicted" and outcome.facts["payer"] == "ORCHID LANE RETAIL"


def test_tpp_innocent_split_parts_are_contradicted() -> None:
    pack, _ = _case("innocent_split")
    rows = _rows(pack, "INV-1002 PART")
    assert len(rows) == 2
    assert _check("innocent_split", _tpp(pack, "INV-1002", rows)).status == "contradicted"


def _edited(name: str, row: str, **fields: Any) -> tuple[ExtractedPack, Lead]:
    """The pack *name* with bank row *row* changed, and a third_party_payment lead on INV-1002 and it."""
    pack, _ = _case(name)
    statements = []
    for statement in pack.bank_statements:
        txns = [t.model_copy(update=fields) if rid == row else t
                for rid, s, t in bank_rows(pack) if s is statement]
        statements.append(statement.model_copy(update={"transactions": txns}))
    return pack.model_copy(update={"bank_statements": statements}), _tpp(pack, "INV-1002", [row])


@pytest.mark.parametrize(("fields", "reason"), [
    ({"credit": None, "debit": 18530}, "is not a credit"),
    ({"description": "GIRO CR HARBOURLINE LOGISTICS"}, "does not name INV-1002"),
    ({"description": "PAYMENT RECEIVED INV-1002 HARBOURLINE"}, "payer cannot be read"),
    ({"description": "GIRO CR CUSTOMER RECEIPTS INV-1002"}, "generic payer"),
    ({"description": "GIRO CR BAYVIEW MARINE TRADING INV-1002"}, "cannot tell"),
])
def test_tpp_preconditions_not_met_are_unverified(fields: dict[str, Any], reason: str) -> None:
    pack, _ = _case("third_party_payment")
    row = _rows(pack, "HARBOURLINE LOGISTICS INV-1002")[0]
    edited, lead = _edited("third_party_payment", row, **fields)
    outcome = check_pattern(lead, edited, [])
    assert outcome.status == "unverified" and reason in outcome.reason, outcome


@pytest.mark.parametrize("description", [
    "FAST CR CASH DEPOSIT ATM INV-1002",  # a generic payer, with more words than the list's phrase
    "GIRO CR TRANSFER FROM ACC 12345 INV-1002",
    "GIRO CR REVERSAL OF INV-1002",
    "TT CR SGD 18530.00 INV-1002",  # an amount, not a name
    "GIRO CR HARBOURLINE REF 77 INV-1002",
])
def test_tpp_review_formats_are_unverified_not_verified(description: str) -> None:
    pack, _ = _case("third_party_payment")
    row = _rows(pack, "HARBOURLINE LOGISTICS INV-1002")[0]
    edited, lead = _edited("third_party_payment", row, description=description)
    outcome = check_pattern(lead, edited, [])
    assert outcome.status == "unverified", (description, outcome)


def test_tpp_two_invoices_in_one_transfer_is_not_a_third_party() -> None:
    # The buyer paying INV-1001 and INV-1002 together: the "payer" would read as "ACME INV-1001"
    pack, _ = _case("third_party_payment")
    row = _rows(pack, "HARBOURLINE LOGISTICS INV-1002")[0]
    edited, lead = _edited("third_party_payment", row, description="GIRO CR BAYVIEW MARINE SUPPLIES INV-1001 INV-1002")
    assert check_pattern(lead, edited, []).status == "unverified"


def test_tpp_the_case_data_decides_not_the_agents_choice_of_rows() -> None:
    # The buyer paid part (T-row listed by the agent); Harbourline paid the rest (not listed): not "clear"
    pack, _ = _case("innocent_split")
    parts = _rows(pack, "INV-1002 PART")
    statements = []
    for statement in pack.bank_statements:
        txns = [t.model_copy(update={"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 2"})
                if rid == parts[1] else t for rid, s, t in bank_rows(pack) if s is statement]
        statements.append(statement.model_copy(update={"transactions": txns}))
    edited = pack.model_copy(update={"bank_statements": statements})
    only_the_buyers = _tpp(pack, "INV-1002", [parts[0]])
    outcome = check_pattern(only_the_buyers, edited, [])
    assert outcome.status == "verified" and outcome.facts["transaction"] == parts[1]


def test_tpp_facts_carry_the_amount_and_date_a_question_may_state() -> None:
    pack, _ = _case("third_party_payment")
    outcome = _check("third_party_payment", _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002")))
    assert (outcome.facts["amount"], outcome.facts["date"]) == ("18530.00", "2026-07-12")


# ---------------------------------------------------------------------------
# sequence_anomaly
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("invoice", ["INV-1001", "INV-1002"])
def test_seq_l04_l08_invoice_after_shipment_is_contradicted(invoice: str) -> None:
    outcome = _check("third_party_payment", _lead("sequence_anomaly", "timeline", invoice_number=invoice))
    assert outcome.status == "contradicted"


def test_seq_retro_po_is_verified_and_names_its_rule_finding() -> None:
    pack, evidence = _case("retro_po")
    finding = next(e for e in evidence if e.check_id == "TEMP_TRADE_SEQUENCE"
                   and e.document in {i.doc_id for i in pack.invoices})
    invoice = next(i.invoice_number for i in pack.invoices if i.doc_id == finding.document)
    outcome = _check("retro_po", _lead("sequence_anomaly", "timeline", invoice_number=invoice))
    assert outcome.status == "verified" and outcome.facts["rule_finding"] == finding.evidence_id


def test_seq_payment_before_invoice_only_is_unverified() -> None:
    pack, _ = _case("third_party_payment")
    invoice = next(i for i in pack.invoices if i.invoice_number == "INV-1002")
    early = pack.model_copy(update={"invoices": [
        i.model_copy(update={"invoice_date": i.invoice_date.replace(month=7, day=20)})
        if i is invoice else i for i in pack.invoices]})
    outcome = check_pattern(_lead("sequence_anomaly", "timeline", invoice_number="INV-1002"), early, [])
    assert outcome.status == "unverified" and "payment before invoice" in outcome.reason


def test_seq_every_bill_of_lading_of_the_trade_counts() -> None:
    # A second bill of lading for INV-1002, shipped before its purchase order: the rule raises it, so must the check
    pack, evidence = _case("third_party_payment")
    first = next(b for b in pack.bills_of_lading if b.invoice_reference == "INV-1002")
    extra = first.model_copy(update={"bl_number": "BL-EXTRA", "doc_id": "bill_of_lading_BL-EXTRA",
                                     "shipped_date": first.shipped_date.replace(month=6, day=11)})
    edited = pack.model_copy(update={"bills_of_lading": [*pack.bills_of_lading, extra]})
    lead = _lead("sequence_anomaly", "timeline", invoice_number="INV-1002")
    assert check_pattern(lead, edited, []).status == "verified"
    assert any(e.check_id == "TEMP_TRADE_SEQUENCE" for e in check_trade_rules(edited))  # the rule agrees


# ---------------------------------------------------------------------------
# related_party
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("buyer", ["Orchid Lane Retail Pte. Ltd.", "Bayview Marine Supplies Pte. Ltd."])
def test_rp_l13_director_guarantor_is_contradicted(buyer: str) -> None:
    pack, _ = _case("third_party_payment")
    outcome = _check("third_party_payment", _lead("related_party", "kyc", counterparty=_party(pack, buyer)))
    assert outcome.status == "contradicted"


def test_rp_related_party_pack_is_verified() -> None:
    pack, _ = _case("related_party")
    outcome = _check("related_party", _lead("related_party", "kyc",
                                            counterparty=_party(pack, "Seabright Provisions Pte. Ltd.")))
    assert outcome.status == "verified" and outcome.facts["shared_officer"] == "Daniel Brooks"


def test_rp_ambiguous_register_match_is_unverified(monkeypatch: pytest.MonkeyPatch) -> None:
    pack, _ = _case("related_party")
    seabright = official.company("202205551D")
    assert seabright is not None
    twin = seabright.model_copy(update={"uen": "209999999Z"})
    monkeypatch.setattr(official, "search", lambda name: [seabright, twin])
    outcome = check_pattern(_lead("related_party", "kyc", counterparty=_party(pack, "Seabright Provisions Pte. Ltd.")),
                            pack, [])
    assert outcome.status == "unverified" and "more than one register entry" in outcome.reason


# ---------------------------------------------------------------------------
# The rest
# ---------------------------------------------------------------------------


def test_rp_no_shared_officer_is_not_clear_when_the_applicant_is_not_in_the_register(
        monkeypatch: pytest.MonkeyPatch) -> None:
    pack, _ = _case("third_party_payment")
    assert pack.official is not None
    without_applicant = {u: e for u, e in pack.official.companies.items() if u != "202301234K"}
    pack = pack.model_copy(update={"official": pack.official.model_copy(update={"companies": without_applicant})})
    outcome = check_pattern(_lead("related_party", "kyc", counterparty=_party(pack, "Orchid Lane Retail Pte. Ltd.")),
                            pack, [])
    assert outcome.status == "unverified" and "officers of both sides" in outcome.reason


def test_corroboration_documents_follow_the_patterns_own_subject_fields() -> None:
    pack, evidence = _case("third_party_payment")
    lead = _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002"))
    verified = check_pattern(lead, pack, list(evidence))
    elsewhere = _finding(next(i.doc_id for i in pack.invoices if i.invoice_number == "INV-1001"),
                         "META_MOD_AFTER_CREATE")
    widened = lead.model_copy(update={"counterparty": _party(pack, "Orchid Lane Retail Pte. Ltd.")})
    # The agent set a counterparty that third_party_payment does not use: it widens nothing
    assert corroborate(widened, verified, pack, [elsewhere]).status == "verified"


# ---------------------------------------------------------------------------
# round_trip
# ---------------------------------------------------------------------------


def _rt(rows: list[str]) -> Lead:
    return _lead("round_trip", "transaction", transaction_ids=rows)


def test_rt_supplier_payment_returning_as_a_receipt_is_verified() -> None:
    pack, _ = _case("round_trip")
    outcome = _check("round_trip", _rt(_rows(pack, "SUPPLIER PAYMENT HARBOURLINE")))
    assert outcome.status == "verified"
    assert outcome.facts == {
        "counterparty": "HARBOURLINE LOGISTICS",
        "out_transaction": _rows(pack, "SUPPLIER PAYMENT HARBOURLINE")[0], "out_amount": "15250.00",
        "out_date": "2026-06-28",
        "back_transaction": _rows(pack, "CUSTOMER RECEIPTS HARBOURLINE")[0], "back_amount": "15200.00",
        "back_date": "2026-07-03",
    }


def test_rt_the_case_data_finds_the_return_not_the_agents_choice_of_rows() -> None:
    """The lead names only the payment out; code searches every later credit."""
    pack, _ = _case("round_trip")
    rows = [*_rows(pack, "SUPPLIER PAYMENT HARBOURLINE"), *_rows(pack, "ORCHID LANE RETAIL INV-1001")]
    assert _check("round_trip", _rt(rows)).status == "verified"


def test_rt_a_payment_nothing_returns_from_is_contradicted() -> None:
    pack, _ = _case("third_party_payment")
    outcome = _check("third_party_payment", _rt(_rows(pack, "OFFICE RENT")[:1]))
    assert outcome.status == "contradicted"


def test_rt_without_a_payment_out_is_unverified() -> None:
    pack, _ = _case("round_trip")
    outcome = _check("round_trip", _rt(_rows(pack, "CUSTOMER RECEIPTS HARBOURLINE")))
    assert (outcome.status, outcome.reason) == ("unverified", "round_trip needs a payment out (a debit)")


def test_rt_a_legal_suffix_alone_is_not_the_same_party() -> None:
    assert shared_party("TT DR ABC ELECTRONICS PTE LTD", "GIRO CR SUNNY PROPERTIES PTE LTD") is None
    assert shared_party("SUPPLIER PAYMENT HARBOURLINE LOGISTICS",
                        "GIRO CR CUSTOMER RECEIPTS HARBOURLINE LOGISTICS") == "HARBOURLINE LOGISTICS"


@pytest.mark.parametrize(("pattern", "agent"), [("document_inconsistency", "forensics"),  # run 5's L02
                                                 ("business_mismatch", "kyc")])  # run 5's L14
def test_unchecked_patterns_are_unverified(pattern: str, agent: str) -> None:
    outcome = _check("third_party_payment", _lead(pattern, agent))
    assert (outcome.status, outcome.reason) == ("unverified", f"no code check for {pattern}")


def test_triage_is_unverified() -> None:
    assert _check("third_party_payment", _lead(None, "transaction", kind="triage", finding_ids=[])).status == \
        "unverified"


def test_a_refuted_lead_code_verifies_is_verified() -> None:
    pack, _ = _case("third_party_payment")
    lead = _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002"))
    assert _check("third_party_payment", lead.model_copy(update={"investigation_status": "refuted"})).status == \
        "verified"


def test_checks_never_read_lead_prose() -> None:
    pack, _ = _case("third_party_payment")
    lead = _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002"))
    other = lead.model_copy(update={"hypothesis": "INV-1002 was paid by its buyer", "tests": [],
                                    "missing_evidence": ["anything"]})
    assert _check("third_party_payment", lead) == _check("third_party_payment", other)


def _finding(document: str, check_id: str, severity: str = "high") -> Evidence:
    return Evidence(evidence_id="E99", check_id=check_id, severity=severity, document=document, page=1,
                    method="deterministic", explanation="x")


def _without_payer_rule(evidence: tuple[Evidence, ...]) -> list[Evidence]:
    """*evidence* without XDOC_THIRD_PARTY_PAYER: the third-party lead as it
    stands when no rule reports the payment."""
    return [e for e in evidence if e.check_id != "XDOC_THIRD_PARTY_PAYER"]


def test_corroborated_only_from_verified() -> None:
    pack, evidence = _case("third_party_payment")
    lead = _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002"))
    verified = check_pattern(lead, pack, _without_payer_rule(evidence))
    invoice = next(i.doc_id for i in pack.invoices if i.invoice_number == "INV-1002")
    other_family = [_finding(invoice, "META_MOD_AFTER_CREATE")]
    assert corroborate(lead, verified, pack, other_family).status == "corroborated"
    # The same data source (bank against invoice) is not independent; nor is an info finding
    assert corroborate(lead, verified, pack, [_finding(invoice, "XDOC_INVOICE_BANK")]).status == "verified"
    assert corroborate(lead, verified, pack, [_finding(invoice, "META_MOD_AFTER_CREATE", "info")]).status == \
        "verified"
    for status in ("unverified", "contradicted"):
        assert corroborate(lead, replace(verified, status=status), pack, other_family).status == status


def test_two_agents_agreeing_is_not_corroboration() -> None:
    pack, evidence = _case("third_party_payment")
    lead = _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002"))
    alone = _without_payer_rule(evidence)
    verified = check_pattern(lead, pack, alone)
    # Corroboration reads rule findings only: there is no input through which another lead could count
    assert corroborate(lead, verified, pack, alone).status == "verified"


def test_a_rule_confirmed_third_party_is_not_corroborated() -> None:
    pack, evidence = _case("third_party_payment")
    lead = _tpp(pack, "INV-1002", _rows(pack, "HARBOURLINE LOGISTICS INV-1002"))
    outcome = check_pattern(lead, pack, list(evidence))
    invoice = next(i.doc_id for i in pack.invoices if i.invoice_number == "INV-1002")
    assert corroborate(lead, outcome, pack, list(evidence) + [_finding(invoice, "META_X")]).status == "verified"


def test_a_rule_confirmed_sequence_is_not_corroborated() -> None:
    pack, evidence = _case("retro_po")
    finding = next(e for e in evidence if e.check_id == "TEMP_TRADE_SEQUENCE"
                   and e.document in {i.doc_id for i in pack.invoices})
    invoice = next(i.invoice_number for i in pack.invoices if i.doc_id == finding.document)
    lead = _lead("sequence_anomaly", "timeline", invoice_number=invoice)
    outcome = check_pattern(lead, pack, list(evidence))
    assert corroborate(lead, outcome, pack, list(evidence) + [_finding(finding.document, "META_X")]).status == \
        "verified"


# ---------------------------------------------------------------------------
# The payment context handed to the agents: facts code read, not leads
# ---------------------------------------------------------------------------


def _context_line(context: str, invoice: str) -> str:
    return next(line for line in context.splitlines() if line.startswith(f"- {invoice},"))


def test_payment_context_states_who_paid_each_invoice_and_whether_that_is_the_buyer() -> None:
    pack, _ = _case("third_party_payment")
    context = payment_context(pack)
    (harbourline,) = _rows(pack, "HARBOURLINE LOGISTICS INV-1002")
    (orchid,) = _rows(pack, "ORCHID LANE RETAIL INV-1001")
    assert f"{harbourline} 2026-07-12 credit 18530.00 from HARBOURLINE LOGISTICS (not the buyer)" in \
        _context_line(context, "INV-1002")
    assert "buyer Bayview Marine Supplies Pte. Ltd." in _context_line(context, "INV-1002")
    assert f"{orchid} 2026-06-18 credit 32699.50 from ORCHID LANE RETAIL (the buyer)" in \
        _context_line(context, "INV-1001")


def test_payment_context_says_so_when_no_credit_names_an_invoice() -> None:
    pack, _ = _case("third_party_payment")
    statements = [s.model_copy(update={"transactions": [t for t in s.transactions if "INV-1002" not in t.description]})
                  for s in pack.bank_statements]
    context = payment_context(pack.model_copy(update={"bank_statements": statements}))
    assert _context_line(context, "INV-1002").endswith("no credit names it")


@pytest.mark.parametrize(("description", "said"), [
    ("CASH DEPOSIT ATM INV-1002", "the payer cannot be read"),
    ("GIRO CR BAYVIEW MARINE SUPPLIES PTE LTD INV-1002", "(the buyer)"),
    ("GIRO CR BAYVIEW MARINE INV-1002", "(may or may not be the buyer)"),
])
def test_payment_context_does_not_claim_more_than_the_check_can_read(description: str, said: str) -> None:
    pack, _ = _case("third_party_payment")
    row = _rows(pack, "HARBOURLINE LOGISTICS INV-1002")[0]
    edited, _ = _edited("third_party_payment", row, description=description)
    assert said in _context_line(payment_context(edited), "INV-1002")


# ---------------------------------------------------------------------------
# Split-payment reconciliation: a rule finding code can explain
# ---------------------------------------------------------------------------


def _split(**changes: dict[str, Any]) -> ExtractedPack:
    """innocent_split with its two INV-1002 part-payments (PART 1, PART 2) changed as given."""
    pack, _ = _case("innocent_split")
    parts = dict(zip(("PART 1", "PART 2"), (_rows(pack, f"INV-1002 {n}")[0] for n in ("PART 1", "PART 2")),
                     strict=True))
    by_row = {parts[name.replace("_", " ").upper()]: fields for name, fields in changes.items()}
    statements = []
    for statement in pack.bank_statements:
        txns = [t.model_copy(update=by_row.get(rid, {})) for rid, s, t in bank_rows(pack) if s is statement]
        statements.append(statement.model_copy(update={"transactions": txns}))
    return pack.model_copy(update={"bank_statements": statements})


def _unmatched(evidence: tuple[Evidence, ...]) -> str:
    return next(e.evidence_id for e in evidence if e.check_id == "XDOC_INVOICE_BANK" and e.severity != "info")


def test_two_buyer_credits_that_sum_to_the_invoice_resolve_its_unmatched_finding() -> None:
    pack, evidence = _case("innocent_split")
    resolved = resolved_findings(pack, list(evidence))
    assert list(resolved) == [_unmatched(evidence)]
    (row1, row2) = _rows(pack, "INV-1002 PART")
    assert resolved[_unmatched(evidence)] == (
        f"INV-1002 was paid in parts by its buyer Bayview Marine Supplies Pte. Ltd.: {row1}, {row2}, "
        "together 18530.00 of 18530.00")


def test_a_sum_within_the_rule_tolerance_resolves() -> None:
    pack = _split(part_2={"credit": Decimal("8529.50")})
    _, evidence = _case("innocent_split")
    assert _unmatched(evidence) in resolved_findings(pack, list(evidence))


@pytest.mark.parametrize(("changes", "why"), [
    # Not the buyer's money at all: a third-party payment is not explained by splitting it
    ({"part_1": {"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 1"},
      "part_2": {"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 2"}}, "third party"),
    ({"part_2": {"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 2"}}, "mixed payers"),
    ({"part_2": {"credit": Decimal("8000.00")}}, "underpaid"),
    ({"part_2": {"credit": Decimal("9000.00")}}, "overpaid"),
    ({"part_2": {"date": date(2026, 9, 1)}}, "a part outside the match window"),
    ({"part_2": {"description": "CASH DEPOSIT INV-1002 PART 2"}}, "a payer that cannot be read"),
    ({"part_2": {"description": "GIRO CR BAYVIEW MARINE INV-1002 PART 2"}}, "a payer that may not be the buyer"),
])
def test_anything_but_the_buyer_paying_the_whole_total_in_time_resolves_nothing(
        changes: dict[str, dict[str, Any]], why: str) -> None:
    _, evidence = _case("innocent_split")
    assert resolved_findings(_split(**changes), list(evidence)) == {}, why


def test_one_credit_is_not_a_split() -> None:
    _, evidence = _case("innocent_split")
    pack = _split(part_2={"description": "INTEREST"})
    assert resolved_findings(pack, list(evidence)) == {}


# ---------------------------------------------------------------------------
# Where a lead's subject is: the document and page the analyst is taken to
# ---------------------------------------------------------------------------


def test_subject_locations_name_the_document_and_page_of_each_subject() -> None:
    pack, _ = _case("third_party_payment")
    (harbourline,) = _rows(pack, "HARBOURLINE LOGISTICS INV-1002")
    where = subject_locations(pack)
    assert where[harbourline] == ("bank_statement_2026-07", 1)
    assert where["INV-1002"] == ("invoice_INV-1002", 1)
    # Every counterparty is placed on a document it appears on
    assert set(counterparties(pack).values()) <= set(where)
    assert all(doc in {d.doc_id for d in pack.docs} for doc, _ in where.values())
