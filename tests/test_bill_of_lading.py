"""
tests/test_bill_of_lading.py
----------------------------
Bill of lading: parsing, classification, the invoice-vs-shipment check
(XDOC_BL_INVOICE), and the generated packs. No AWS calls.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from gldb import extract_llm
from gldb.checks import AGENTS, check_xdoc_bl_invoice
from gldb.data_factory import ENTITY_TEMPLATE, generate_packs
from gldb.extract_stub import _parse_bill_of_lading
from gldb.intake import classify, read_text
from gldb.schemas import BillOfLading, ExtractedPack, Invoice, LineItem, ShippedGoods
from gldb.signal_library import BY_CHECK

_BL_TEXT = """BILL OF LADING
B/L No: BL-3002
Shipper: Lumen Harbour Trading Pte. Ltd.
Consignee: Bayview Marine Supplies Pte. Ltd.
Vessel: MV Straits Pioneer
Port of Loading: Singapore
Port of Discharge: Port Klang
Shipped on Board: 2026-07-02
Invoice Ref: INV-1002
Description | Quantity
Marine rope 50m | 100
Deck fittings set | 50"""


def _invoice(lines: list[tuple[str, int]], number: str = "INV-1002") -> Invoice:
    items = [LineItem(description=d, quantity=q, unit_price=1, amount=q) for d, q in lines]
    return Invoice(
        doc_id=f"invoice_{number}", invoice_number=number, invoice_date=date(2026, 7, 5),
        seller_name="S", seller_uen="1", buyer_name="B", line_items=items,
        subtotal=1, gst=0, total=1,
    )


def _bl(goods: list[tuple[str, int]], ref: str = "INV-1002") -> BillOfLading:
    return BillOfLading(
        doc_id="bill_of_lading_BL-3002", bl_number="BL-3002", shipped_date=date(2026, 7, 2),
        shipper_name="S", consignee_name="B", vessel="V", port_of_loading="SG",
        port_of_discharge="MY", invoice_reference=ref,
        goods=[ShippedGoods(description=d, quantity=q) for d, q in goods],
    )


def _pack(invoices: list[Invoice], bls: list[BillOfLading]) -> ExtractedPack:
    return ExtractedPack(case_id="t", docs=[], invoices=invoices, bank_statements=[],
                         financial_statements=[], bills_of_lading=bls, extraction_mode="stub")


def test_parser_reads_a_bill_of_lading() -> None:
    bl = _parse_bill_of_lading("bill_of_lading_BL-3002", [_BL_TEXT])
    assert (bl.bl_number, bl.invoice_reference, bl.shipped_date) == ("BL-3002", "INV-1002", date(2026, 7, 2))
    assert [(g.description, g.quantity) for g in bl.goods] == [("Marine rope 50m", 100), ("Deck fittings set", 50)]
    assert classify([_BL_TEXT]) == "bill_of_lading"


def test_billing_more_than_was_shipped_is_flagged_per_line() -> None:
    inv = _invoice([("Marine rope 50m", 300), ("Deck fittings set", 50)])
    (ev,) = check_xdoc_bl_invoice(_pack([inv], [_bl([("Marine rope 50m", 100), ("Deck fittings set", 50)])]))
    assert (ev.check_id, ev.category, ev.severity, ev.document) == (
        "XDOC_BL_INVOICE", "fraud_signal", "high", "invoice_INV-1002",
    )
    assert (ev.expected, ev.observed) == ("at most 100 shipped (BL-3002)", "300 invoiced")


def test_services_absent_from_the_bill_of_lading_are_not_flagged() -> None:
    inv = _invoice([("LED panel lights", 200), ("Installation service", 1)], "INV-1001")
    assert check_xdoc_bl_invoice(_pack([inv], [_bl([("LED panel lights", 200)], "INV-1001")])) == []


def test_invoice_without_any_bill_of_lading_is_flagged_medium() -> None:
    other = _invoice([("Marine rope 50m", 1)], "INV-9999")
    (ev,) = check_xdoc_bl_invoice(_pack([other], [_bl([("Marine rope 50m", 1)])]))
    assert (ev.severity, ev.observed) == ("medium", "none")


def test_pack_without_bills_of_lading_is_not_checked() -> None:
    assert check_xdoc_bl_invoice(_pack([_invoice([("x", 1)])], [])) == []


def test_bill_of_lading_is_wired_in() -> None:
    cross = next(a for a in AGENTS if a.name == "Cross-Document")
    assert check_xdoc_bl_invoice in cross.checks
    assert BY_CHECK["XDOC_BL_INVOICE"].signal_id == "TXN-003"
    assert extract_llm._MODELS["bill_of_lading"] is BillOfLading


@pytest.fixture(scope="module")
def packs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not ENTITY_TEMPLATE.exists():
        pytest.skip("AcroForm templates not present")
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base


def test_every_pack_ships_both_invoices(packs: Path) -> None:
    for name in ("clean", "tampered", "incomplete", "scanned"):
        assert sorted(p.name for p in (packs / name).glob("bill_of_lading_*.pdf")) == [
            "bill_of_lading_BL-3001.pdf", "bill_of_lading_BL-3002.pdf",
        ]
    clean = _parse_bill_of_lading("b", read_text(packs / "clean" / "bill_of_lading_BL-3002.pdf"))
    assert [(g.description, g.quantity) for g in clean.goods] == [
        ("Marine rope 50m", 100), ("Deck fittings set", 50),
    ]


def test_tampered_ground_truth_expects_the_shipment_mismatch(packs: Path) -> None:
    truth = json.loads((packs / "tampered" / "ground_truth.json").read_text())
    assert "XDOC_BL_INVOICE" in truth["expected_check_ids"]
    assert any(t["type"] == "over_invoiced_vs_shipment" for t in truth["tampers"])
