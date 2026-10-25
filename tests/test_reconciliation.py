"""
tests/test_reconciliation.py
----------------------------
Transaction chains (invoice -> purchase order -> bill of lading -> payment)
projected from the checks' evidence. Stub extraction, no AWS calls.
"""

from __future__ import annotations

from pathlib import Path

from gldb.checks import run_rule_checks
from gldb.pipeline import pack_from_folder
from gldb.reconciliation import build_chains
from gldb.schemas import TransactionChain

PACKS = Path(__file__).parent / "fixtures" / "packs"


def _chains(pack_name: str) -> dict[str, TransactionChain]:
    pack = pack_from_folder(PACKS / pack_name)
    evidence, _ = run_rule_checks(pack)
    return {c.invoice_number: c for c in build_chains(pack, evidence)}


def _statuses(chain: TransactionChain) -> dict[str, str]:
    return {s.step: s.status for s in chain.steps}


def test_clean_pack_chains_are_unbroken() -> None:
    chains = _chains("clean")
    assert set(chains) == {"INV-1001", "INV-1002"}
    for chain in chains.values():
        assert _statuses(chain) == {
            "purchase_order": "matched", "bill_of_lading": "matched", "payment": "matched",
        }
        assert not chain.broken


def test_counterparts_point_to_their_source_documents() -> None:
    steps = {s.step: s for s in _chains("clean")["INV-1002"].steps}
    assert steps["purchase_order"].counterpart is not None
    assert steps["purchase_order"].counterpart.doc_id == "purchase_order_PO-2002"
    assert steps["bill_of_lading"].counterpart is not None
    assert steps["bill_of_lading"].counterpart.doc_id == "bill_of_lading_BL-3002"
    payment = steps["payment"].counterpart
    assert payment is not None and payment.doc_id.startswith("bank_statement_2026-07")
    assert payment.value == "SGD 18530.00"


def test_rounding_difference_is_matched_with_a_note() -> None:
    payment = next(s for s in _chains("clean")["INV-1001"].steps if s.step == "payment")
    assert payment.status == "matched" and "within tolerance" in payment.note
    assert payment.evidence_ids  # the info finding behind the note


def test_tampered_invoice_shows_exactly_where_the_chain_breaks() -> None:
    chain = _chains("tampered")["INV-1002"]
    assert chain.broken
    assert _statuses(chain) == {
        "purchase_order": "contradicts", "bill_of_lading": "contradicts", "payment": "missing",
    }
    assert all(s.evidence_ids for s in chain.steps)  # every break is backed by evidence
    assert chain.steps[0].invoice_claim.value == "SGD 48505.00"


def test_untouched_invoice_in_tampered_pack_stays_matched() -> None:
    assert not _chains("tampered")["INV-1001"].broken


def test_missing_document_type_is_not_provided() -> None:
    pack = pack_from_folder(PACKS / "clean")
    pack = pack.model_copy(update={"bills_of_lading": []})
    evidence, _ = run_rule_checks(pack)
    chain = next(c for c in build_chains(pack, evidence) if c.invoice_number == "INV-1002")
    assert _statuses(chain)["bill_of_lading"] == "not_provided"
    assert not chain.broken
