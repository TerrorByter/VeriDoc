"""
tests/test_delivery_order.py
----------------------------
A delivery order: the supplier's note that it delivered goods by its own
transport to a buyer in Singapore, signed by the buyer on receipt. It stands
where a bill of lading stands for goods shipped abroad, so the checks that
trace an invoice's goods to their shipment read both. No AWS calls.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gldb import extract_llm, extract_stub
from gldb.checks import check_temp_trade_sequence, check_xdoc_bl_invoice
from gldb.forensics import hidden_text, mixed_font_tokens
from gldb.intake import classify, intake, read_text
from gldb.reconciliation import build_chains
from gldb.schemas import DeliveryOrder, ExtractedPack, Invoice, LineItem, PurchaseOrder, ShippedGoods
from gldb.trade_pdf import DeliveryOrderSheet, Packing, render_delivery_order

STEEL = Packing("BUNDLES", 10, Decimal("73.6"), Decimal("0.0094"))
SHEET = DeliveryOrderSheet(
    number="DO-SM-260128", supplier="STEELMEN PTE LTD.", supplier_address=("12 Tuas Avenue 10", "Singapore 639142"),
    supplier_contact="Tel +65 6123 4567  |  accounts@steelmen.example",
    consignee="ACMA LTD.", consignee_address=("67 19 Jurong Port Road, #25-550 Jurong Tower 2", "Singapore 619093"),
    delivered=date(2026, 1, 26), invoice_ref="SM-260128", order_ref="PO-4002",
    goods=(STEEL.cargo("Cold-rolled steel sheets, 3 mm", 120),),
)


@pytest.fixture(scope="module")
def delivery(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("do") / "delivery_order_DO-SM-260128.pdf"
    render_delivery_order(path, SHEET)
    return path


def _read(path: Path) -> DeliveryOrder:
    pack = extract_stub.parse_pack("t", intake("t", [path]), [path], None)
    (order,) = pack.delivery_orders
    return order


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------

def test_a_delivery_order_is_one_page_on_the_suppliers_letterhead_and_reads_as_one(delivery: Path) -> None:
    pages = read_text(delivery)
    assert len(pages) == 1 and classify(pages) == "delivery_order"
    assert pages[0].splitlines()[0] == "STEELMEN PTE LTD. DELIVERY ORDER"


@pytest.mark.parametrize("shown", [
    "DO NUMBER", "DO-SM-260128", "DELIVERY DATE", "26 Jan 2026", "INVOICE REF", "SM-260128", "ORDER REF", "PO-4002",
    "DELIVER TO", "ACMA LTD.", "Singapore 619093",
    "1 Cold-rolled steel sheets, 3 mm 120 12 BUNDLES 8,832.0 1.13", "TOTAL 12 PACKAGES 8,832.0 1.13",
    "Received the goods above in good order and condition", "For and on behalf of ACMA LTD.",
])
def test_every_part_of_a_delivery_order_is_there(delivery: Path, shown: str) -> None:
    assert shown in read_text(delivery)[0]


def test_it_mentions_no_other_kind_of_document_it_could_be_taken_for(delivery: Path) -> None:
    text = read_text(delivery)[0].upper()
    assert not any(other in text for other in ("TAX INVOICE", "PURCHASE ORDER", "BILL OF LADING"))


def test_nothing_in_it_trips_a_forensic_check(delivery: Path) -> None:
    assert hidden_text(delivery) == [] and mixed_font_tokens(delivery) == []


@pytest.mark.parametrize("goods", [SHEET.goods, (STEEL.cargo("Cold-rolled steel sheets, 3 mm", 90),
                                                 Packing("PALLETS", 4, Decimal("38"), Decimal("0.12")).cargo("Coils", 60))])
def test_a_delivery_order_reads_back_as_it_was_issued(tmp_path: Path, goods: tuple) -> None:
    sheet = replace(SHEET, goods=goods)
    path = tmp_path / "delivery_order.pdf"
    render_delivery_order(path, sheet)
    read = _read(path)
    assert (read.do_number, read.shipped_date, read.invoice_reference) == (sheet.number, sheet.delivered, sheet.invoice_ref)
    assert (read.shipper_name, read.consignee_name) == (sheet.supplier, sheet.consignee)
    assert [(g.description, g.quantity) for g in read.goods] == [(c.description, c.quantity) for c in goods]
    assert all(quote in read_text(path)[0] for quote in read.source_quotes.values())


def test_a_delivery_order_row_it_cannot_read_is_refused(delivery: Path) -> None:
    text = read_text(delivery)[0].replace("120 12 BUNDLES", "lots of BUNDLES")
    with pytest.raises(ValueError, match="cannot parse table row"):
        extract_stub._parse_delivery_order("do", [text])


def test_the_model_extractor_reads_delivery_orders_into_their_own_record() -> None:
    assert extract_llm._MODELS["delivery_order"] is DeliveryOrder


# ---------------------------------------------------------------------------
# The checks read it as the shipment of its invoice
# ---------------------------------------------------------------------------

def _invoice(quantity: int, number: str = "SM-260128", issued: date = date(2026, 1, 28)) -> Invoice:
    items = [LineItem(description="Cold-rolled steel sheets, 3 mm", quantity=quantity, unit_price=1, amount=quantity)]
    return Invoice(doc_id=f"invoice_{number}", invoice_number=number, invoice_date=issued, seller_name="STEELMEN PTE LTD.",
                   seller_uen="200709981R", buyer_name="ACMA LTD.", po_number="PO-4002", line_items=items,
                   subtotal=quantity, gst=0, total=quantity)


def _delivery(quantity: int, delivered: date = date(2026, 1, 26)) -> DeliveryOrder:
    return DeliveryOrder(doc_id="delivery_order_DO-SM-260128", do_number="DO-SM-260128", shipped_date=delivered,
                         shipper_name="STEELMEN PTE LTD.", consignee_name="ACMA LTD.", invoice_reference="SM-260128",
                         goods=[ShippedGoods(description="Cold-rolled steel sheets, 3 mm", quantity=quantity)])


def _pack(invoices: list[Invoice], deliveries: list[DeliveryOrder], orders: Sequence[PurchaseOrder] = ()) -> ExtractedPack:
    return ExtractedPack(case_id="t", docs=[], invoices=invoices, bank_statements=[], financial_statements=[],
                         purchase_orders=list(orders), delivery_orders=deliveries, extraction_mode="stub")


def test_an_invoice_delivered_in_full_is_traced_to_its_delivery_order() -> None:
    assert check_xdoc_bl_invoice(_pack([_invoice(120)], [_delivery(120)])) == []


def test_billing_more_than_was_delivered_is_flagged_against_the_delivery_order() -> None:
    (ev,) = check_xdoc_bl_invoice(_pack([_invoice(120)], [_delivery(90)]))
    assert (ev.severity, ev.expected, ev.observed) == ("high", "at most 90 delivered (DO-SM-260128)", "120 invoiced")


def test_an_invoice_with_neither_a_bill_of_lading_nor_a_delivery_order_is_flagged() -> None:
    (ev,) = check_xdoc_bl_invoice(_pack([_invoice(120), _invoice(5, "SM-260301")], [_delivery(120)]))
    assert ev.document == "invoice_SM-260301" and "bill of lading or delivery order" in ev.explanation


def test_goods_delivered_before_they_were_ordered_break_the_trade_sequence() -> None:
    order = PurchaseOrder(doc_id="purchase_order_PO-4002", po_number="PO-4002", po_date=date(2026, 1, 27),
                          buyer_name="ACMA LTD.", seller_name="STEELMEN PTE LTD.", seller_uen="200709981R",
                          line_items=[], total=120)
    (ev,) = check_temp_trade_sequence(_pack([_invoice(120)], [_delivery(120)], [order]))
    assert ev.document == "delivery_order_DO-SM-260128" and "Delivery order DO-SM-260128" in ev.explanation


def test_the_chain_of_an_invoice_names_its_delivery_order_as_the_shipment() -> None:
    (chain,) = build_chains(_pack([_invoice(120)], [_delivery(120)]), [])
    step = next(s for s in chain.steps if s.step == "bill_of_lading")
    assert step.status == "matched" and step.counterpart is not None
    assert step.counterpart.label == "Delivery order DO-SM-260128 delivered"
