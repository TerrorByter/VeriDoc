"""
tests/test_purchase_orders.py
-----------------------------
Purchase order (trade record): classification, stub parsing and the
XDOC_PO_INVOICE / XDOC_IDENTITY checks. Built from the real clean pack,
changing one thing per test. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gldb.checks import check_xdoc_identity, check_xdoc_po_invoice
from gldb.intake import classify, read_text
from gldb.pipeline import pack_from_folder
from gldb.schemas import ExtractedPack

PACKS = Path(__file__).parent.parent / "data" / "packs"


@pytest.fixture
def clean() -> ExtractedPack:
    return pack_from_folder(PACKS / "clean")


def _mutate(pack: ExtractedPack, *, invoice: dict | None = None, po: dict | None = None,
            drop_po: int | None = None) -> ExtractedPack:
    invoices = [pack.invoices[0].model_copy(update=invoice or {}), *pack.invoices[1:]]
    pos = list(pack.purchase_orders)
    if po:
        pos[0] = pos[0].model_copy(update=po)
    if drop_po is not None:
        del pos[drop_po]
    return pack.model_copy(update={"invoices": invoices, "purchase_orders": pos})


def test_purchase_order_is_classified_and_parsed(clean: ExtractedPack) -> None:
    path = PACKS / "clean" / "purchase_order_PO-2001.pdf"
    assert classify(read_text(path)) == "purchase_order"
    po = next(p for p in clean.purchase_orders if p.po_number == "PO-2001")
    assert po.buyer_name == "Orchid Lane Retail Pte. Ltd."
    assert po.seller_uen == "202301234K"
    assert po.po_date == date(2026, 5, 27)
    assert po.total == Decimal("32700.00")
    assert [i.amount for i in po.line_items] == [Decimal("18000.00"), Decimal("12000.00")]
    assert po.source_quotes["Total"].startswith("Total (incl. GST):")
    assert len(clean.purchase_orders) == 2


def test_clean_pack_has_no_po_findings(clean: ExtractedPack) -> None:
    assert check_xdoc_po_invoice(clean) == []
    assert [e for e in check_xdoc_identity(clean) if e.document.startswith("purchase")] == []


def test_invoice_above_po_is_high(clean: ExtractedPack) -> None:
    inv = clean.invoices[0]
    pack = _mutate(clean, invoice={"total": inv.total + Decimal("500.00")})
    (ev,) = check_xdoc_po_invoice(pack)
    assert (ev.check_id, ev.severity, ev.category) == ("XDOC_PO_INVOICE", "high", "fraud_signal")
    assert ev.observed == f"SGD {inv.total + Decimal('500.00')}"


def test_invoice_within_tolerance_or_below_po_is_fine(clean: ExtractedPack) -> None:
    inv = clean.invoices[0]
    assert check_xdoc_po_invoice(_mutate(clean, invoice={"total": inv.total + Decimal("1.00")})) == []
    assert check_xdoc_po_invoice(_mutate(clean, invoice={"total": inv.total - Decimal("5000.00")})) == []


def test_invoice_dated_before_its_po_is_medium(clean: ExtractedPack) -> None:
    pack = _mutate(clean, invoice={"invoice_date": date(2026, 5, 1)})
    (ev,) = check_xdoc_po_invoice(pack)
    assert (ev.severity, ev.confidence) == ("medium", 0.9)
    assert "before its purchase order" in ev.explanation


def test_invoice_with_no_po_from_that_buyer_is_flagged(clean: ExtractedPack) -> None:
    pack = _mutate(clean, drop_po=0)  # PO-2001 removed: INV-1001's buyer has no order
    (ev,) = check_xdoc_po_invoice(pack)
    assert ev.check_id == "XDOC_PO_INVOICE" and ev.severity == "medium"
    assert ev.document == "invoice_INV-1001"


def test_pack_without_any_po_is_not_penalised(clean: ExtractedPack) -> None:
    pack = clean.model_copy(update={"purchase_orders": []})
    assert check_xdoc_po_invoice(pack) == []


def test_po_seller_identity_is_checked(clean: ExtractedPack) -> None:
    pack = _mutate(clean, po={"seller_uen": "202399999Z", "seller_name": "Other Trading Pte. Ltd."})
    found = [e for e in check_xdoc_identity(pack) if e.document.startswith("purchase")]
    assert sorted(e.severity for e in found) == ["high", "medium"]


def test_tampered_pack_is_caught_by_po_check() -> None:
    pack = pack_from_folder(PACKS / "tampered")
    (ev,) = [e for e in check_xdoc_po_invoice(pack) if e.severity == "high"]
    assert ev.document == "invoice_INV-1002"
    assert ev.expected == "at most SGD 18530.00" and ev.observed == "SGD 48505.00"
