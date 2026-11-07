"""
tests/test_third_party_payer.py
-------------------------------
The rule XDOC_THIRD_PARTY_PAYER (gldb.checks): a sale whose credit names a
payer who is clearly not the invoice's buyer is a high finding on the
statement row; a payer the application discloses is info; a buyer's own
payment, a purchase, and a payer code cannot read or cannot tell from the
buyer raise nothing.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from gldb.checks import bank_rows, check_xdoc_third_party_payer
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import Disclosure, ExtractedPack

PACKS = Path(__file__).parent / "fixtures" / "packs"


@cache
def _pack(name: str) -> ExtractedPack:
    folder = PACKS / name
    return assess(f"tpp-{name}", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None).pack


def _disclosing(pack: ExtractedPack, counterparty: str) -> ExtractedPack:
    manifest = pack.manifest.model_copy(update={"disclosures": [
        Disclosure(kind="payment_agent", counterparty=counterparty, detail="settles the buyer's invoices")]})
    return pack.model_copy(update={"manifest": manifest})


def _described(pack: ExtractedPack, ending: str, description: str) -> ExtractedPack:
    """*pack* with the bank row ending in *ending* re-described."""
    statements = [s.model_copy(update={"transactions": [
        t.model_copy(update={"description": description}) if t.description.endswith(ending) else t
        for t in s.transactions]}) for s in pack.bank_statements]
    return pack.model_copy(update={"bank_statements": statements})


def test_a_sale_paid_by_someone_else_is_a_high_finding_on_the_statement_row() -> None:
    pack = _pack("third_party_payment")
    (finding,) = check_xdoc_third_party_payer(pack)
    row = next(t for _, _, t in bank_rows(pack) if t.description.endswith("HARBOURLINE LOGISTICS INV-1002"))
    statement = next(s for _, s, t in bank_rows(pack) if t is row)
    assert (finding.category, finding.severity) == ("fraud_signal", "high")
    assert (finding.document, finding.page, finding.quote) == (statement.doc_id, statement.page, row.description)
    assert "INV-1002" in finding.explanation and "HARBOURLINE LOGISTICS" in finding.explanation
    assert finding.observed == "payment from HARBOURLINE LOGISTICS"


def test_sales_paid_by_their_buyers_raise_nothing() -> None:
    assert check_xdoc_third_party_payer(_pack("clean")) == []
    # Paid in two parts, each by the buyer
    assert check_xdoc_third_party_payer(_pack("innocent_split")) == []


def test_a_disclosed_payer_is_info() -> None:
    (finding,) = check_xdoc_third_party_payer(_disclosing(_pack("third_party_payment"), "Harbourline Logistics Pte. Ltd."))
    assert finding.severity == "info"
    assert "discloses" in finding.explanation


def test_an_agent_paying_for_the_buyer_is_matched_to_its_disclosure_by_its_words() -> None:
    """The statement reads "<AGENT> FOR <BUYER>": every word of the declared
    agent's name is in it, though not as one run of text once sorted."""
    pack = _described(_pack("third_party_payment"), "HARBOURLINE LOGISTICS INV-1002",
                      "GIRO CR MERIDIAN TRADE FINANCE FOR HARBOURLINE LOGISTICS INV-1002")
    assert [f.severity for f in check_xdoc_third_party_payer(pack)] == ["high"]
    disclosed = _disclosing(pack, "MERIDIAN TRADE FINANCE PTE. LTD.")
    assert [f.severity for f in check_xdoc_third_party_payer(disclosed)] == ["info"]


def test_a_payer_code_cannot_read_raises_nothing() -> None:
    pack = _described(_pack("third_party_payment"), "HARBOURLINE LOGISTICS INV-1002",
                      "GIRO CR CUSTOMER RECEIPTS INV-1002")
    assert check_xdoc_third_party_payer(pack) == []


def test_the_rule_runs_in_the_pipeline_and_routes_the_case_to_review() -> None:
    folder = PACKS / "third_party_payment"
    result = assess("tpp-route", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None)
    assert [e.check_id for e in result.evidence if e.severity != "info"] == ["XDOC_THIRD_PARTY_PAYER"]
    assert (result.risk.band, result.routing.path) == ("Medium", "standard_review")
