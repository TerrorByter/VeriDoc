"""
gldb/reconciliation.py
----------------------
Transaction chains: each invoice traced through its purchase order, bill of
lading and bank payment, showing exactly where the chain breaks.

A projection, not a second rule engine. A step's status comes only from the
evidence the Cross-Document checks already produced (XDOC_PO_INVOICE,
XDOC_BL_INVOICE, XDOC_INVOICE_BANK), so the chain can never disagree with the
findings. Counterpart records are looked up only to show which document and
page the comparison was made against.

Statuses: matched (counterpart found, nothing flagged), contradicts (a check
flagged a difference), missing (a check found no counterpart), not_provided
(the pack has no documents of that type).
"""

from __future__ import annotations

from datetime import timedelta

from gldb.checks import normalise_name
from gldb.config import MATCH_WINDOW_DAYS, TOLERANCE_SGD
from gldb.schemas import (
    ChainStatus,
    ChainStep,
    ClaimRef,
    Evidence,
    ExtractedPack,
    Invoice,
    TransactionChain,
)

_BROKEN: frozenset[ChainStatus] = frozenset({"contradicts", "missing"})


def _findings(evidence: list[Evidence], check_id: str, doc_id: str) -> list[Evidence]:
    return [e for e in evidence if e.check_id == check_id and e.document == doc_id]


def _flag_status(flags: list[Evidence], found: bool) -> ChainStatus:
    serious = [e for e in flags if e.severity != "info"]
    if any(e.observed == "none" for e in serious):
        return "missing"
    if serious:
        return "contradicts"
    return "matched" if found else "missing"


def _goods(lines: list[tuple[str, int]]) -> str:
    return "; ".join(f"{qty} x {desc}" for desc, qty in lines)


def _po_step(inv: Invoice, pack: ExtractedPack, evidence: list[Evidence]) -> ChainStep:
    claim = ClaimRef(doc_id=inv.doc_id, page=inv.page, label="Invoice total", value=f"SGD {inv.total}")
    flags = _findings(evidence, "XDOC_PO_INVOICE", inv.doc_id)
    if not pack.purchase_orders:
        return ChainStep(step="purchase_order", status="not_provided", invoice_claim=claim)
    buyer = normalise_name(inv.buyer_name)
    orders = [po for po in pack.purchase_orders if normalise_name(po.buyer_name) == buyer]
    po = min(orders, key=lambda p: abs(p.total - inv.total)) if orders else None
    status = _flag_status(flags, po is not None)
    return ChainStep(
        step="purchase_order", status=status, invoice_claim=claim,
        counterpart=ClaimRef(doc_id=po.doc_id, page=po.page,
                             label=f"PO {po.po_number} total", value=f"SGD {po.total}")
        if po and status != "missing" else None,
        evidence_ids=[e.evidence_id for e in flags],
        note="; ".join(e.explanation for e in flags),
    )


def _bl_step(inv: Invoice, pack: ExtractedPack, evidence: list[Evidence]) -> ChainStep:
    claim = ClaimRef(
        doc_id=inv.doc_id, page=inv.page, label="Invoiced goods",
        value=_goods([(i.description, i.quantity) for i in inv.line_items]),
    )
    flags = _findings(evidence, "XDOC_BL_INVOICE", inv.doc_id)
    if not pack.bills_of_lading:
        return ChainStep(step="bill_of_lading", status="not_provided", invoice_claim=claim)
    bl = next((b for b in pack.bills_of_lading if b.invoice_reference == inv.invoice_number), None)
    status = _flag_status(flags, bl is not None)
    return ChainStep(
        step="bill_of_lading", status=status, invoice_claim=claim,
        counterpart=ClaimRef(doc_id=bl.doc_id, page=bl.page, label=f"B/L {bl.bl_number} shipped",
                             value=_goods([(g.description, g.quantity) for g in bl.goods]))
        if bl else None,
        evidence_ids=[e.evidence_id for e in flags],
        note="; ".join(e.explanation for e in flags),
    )


def _payment_step(inv: Invoice, pack: ExtractedPack, evidence: list[Evidence]) -> ChainStep:
    claim = ClaimRef(doc_id=inv.doc_id, page=inv.page, label="Invoice total", value=f"SGD {inv.total}")
    flags = _findings(evidence, "XDOC_INVOICE_BANK", inv.doc_id)
    if not pack.bank_statements:
        return ChainStep(step="payment", status="not_provided", invoice_claim=claim)
    # XDOC_INVOICE_BANK raises a non-info finding only when no credit matches
    # (its "observed" is the closest credit, not "none"), so any is "missing".
    status: ChainStatus = "missing" if any(e.severity != "info" for e in flags) else "matched"
    window_end = inv.invoice_date + timedelta(days=MATCH_WINDOW_DAYS)
    credit = next(
        (
            (stmt, txn)
            for stmt in pack.bank_statements
            for txn in stmt.transactions
            if txn.credit is not None
            and inv.invoice_date <= txn.date <= window_end
            and abs(txn.credit - inv.total) <= TOLERANCE_SGD
        ),
        None,
    ) if status == "matched" else None
    return ChainStep(
        step="payment", status=status, invoice_claim=claim,
        counterpart=ClaimRef(doc_id=credit[0].doc_id, page=credit[0].page,
                             label=f"Bank credit on {credit[1].date}", value=f"SGD {credit[1].credit}")
        if credit else None,
        evidence_ids=[e.evidence_id for e in flags],
        note="; ".join(e.explanation for e in flags),
    )


def build_chains(pack: ExtractedPack, evidence: list[Evidence]) -> list[TransactionChain]:
    """One chain per invoice: order, shipment and payment, in that order."""
    chains = []
    for inv in pack.invoices:
        steps = [
            _po_step(inv, pack, evidence),
            _bl_step(inv, pack, evidence),
            _payment_step(inv, pack, evidence),
        ]
        chains.append(TransactionChain(
            invoice_number=inv.invoice_number,
            doc_id=inv.doc_id,
            steps=steps,
            broken=any(s.status in _BROKEN for s in steps),
        ))
    return chains
