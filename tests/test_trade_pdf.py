"""
tests/test_trade_pdf.py
-----------------------
The purchase order and bill of lading the demo packs carry, in the invoice's
family (gldb.trade_pdf). A purchase order is the buyer's: its letterhead, the
vendor and delivery blocks, the item table and figures that add up as an
invoice's do. A bill of lading is the carrier's: shipper, consignee and notify
blocks, vessel and ports, and a cargo table whose totals are the sum of its
rows. Each must read as what it is, read back as it was issued, and trip no
forensic check by itself. No AWS calls.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gldb import extract_stub
from gldb.forensics import hidden_text, mixed_font_tokens
from gldb.intake import classify, intake, read_text
from gldb.invoice_pdf import items_for_total
from gldb.schemas import BillOfLading, LineItem, PurchaseOrder
from gldb.trade_pdf import (
    CARRIER,
    MAX_ORDER_ITEMS,
    BillOfLadingSheet,
    OrderSheet,
    Packing,
    render_bill_of_lading,
    render_purchase_order,
)
from tests.test_invoice_pdf import LONG_ITEMS, LONG_NAMES, _assert_adds_up

FULL_ORDER = LONG_ITEMS[:MAX_ORDER_ITEMS]
ITEMS, ROUNDING = items_for_total(Decimal("1670370.23"), ("Air-handling unit AHU-450", "Commissioning kit and spares"), 40)

ORDER = OrderSheet(
    buyer="EFG Company", buyer_uen="", buyer_address=("88 Tuas South Avenue 6", "Singapore 637051"),
    buyer_contact="Tel +65 6123 4567  |  accounts@efg.example",
    seller="ACMA LTD.", seller_uen="196500233E",
    seller_address=("19 Jurong Port Road, #25-550 Jurong Tower 2", "Singapore 619093"),
    number="PO-2001", issued=date(2025, 12, 22), deliver_by=date(2026, 1, 5), terms="Net 30 days",
    items=ITEMS, rounding=ROUNDING,
)
PURCHASE = replace(ORDER, buyer="ACMA LTD.", buyer_uen="196500233E", buyer_address=ORDER.seller_address,
                   seller="STEELMEN PTE LTD.", seller_uen="200709981R", seller_address=("12 Tuas Avenue 10", "Singapore 639142"),
                   number="PO-4002")

STEEL = Packing("BUNDLES", 10, Decimal("73.6"), Decimal("0.0094"))
BILL = BillOfLadingSheet(
    number="BL-3001", shipper="ACMA LTD.", shipper_address=ORDER.seller_address,
    consignee="EFG Company", consignee_address=ORDER.buyer_address, shipped=date(2026, 1, 2), invoice_ref="INV-1001",
    goods=(Packing("CRATES", 1, Decimal("850"), Decimal("6.5")).cargo("Air-handling unit AHU-450", 40),),
)


@pytest.fixture(scope="module")
def order(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("po") / "purchase_order_PO-2001.pdf"
    render_purchase_order(path, ORDER)
    return path


@pytest.fixture(scope="module")
def bill(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("bl") / "bill_of_lading_BL-3001.pdf"
    render_bill_of_lading(path, BILL)
    return path


def _read(path: Path) -> PurchaseOrder | BillOfLading:
    pack = extract_stub.parse_pack("t", intake("t", [path]), [path], None)
    (doc,) = [*pack.purchase_orders, *pack.bills_of_lading]
    return doc


# ---------------------------------------------------------------------------
# The purchase order
# ---------------------------------------------------------------------------

def test_a_purchase_order_is_one_page_and_reads_as_one(order: Path) -> None:
    pages = read_text(order)
    assert len(pages) == 1 and classify(pages) == "purchase_order"
    assert pages[0].splitlines()[0] == "EFG Company PURCHASE ORDER"


@pytest.mark.parametrize("shown", [
    "88 Tuas South Avenue 6", "accounts@efg.example",
    "PO NUMBER", "PO-2001", "22 Dec 2025", "05 Jan 2026", "Net 30 days", "SGD",
    "VENDOR", "ACMA LTD.", "UEN: 196500233E", "19 Jurong Port Road, #25-550 Jurong Tower 2", "DELIVER TO",
    "Air-handling unit AHU-450", "Commissioning kit and spares", "Subtotal", "GST (9%)", "TOTAL (SGD) 1,670,370.23",
    "TERMS AND CONDITIONS", "Authorised signatory", "For and on behalf of EFG Company",
])
def test_every_part_of_a_purchase_order_is_there(order: Path, shown: str) -> None:
    assert shown in read_text(order)[0]


def test_a_buyer_without_a_uen_prints_none_and_one_with_a_uen_prints_it(tmp_path: Path) -> None:
    path = tmp_path / "po.pdf"
    render_purchase_order(path, PURCHASE)
    assert "UEN: 196500233E" in read_text(path)[0].split("PO NUMBER")[0]
    efg = tmp_path / "efg.pdf"
    render_purchase_order(efg, ORDER)
    assert "UEN" not in read_text(efg)[0].split("PO NUMBER")[0]


@pytest.mark.parametrize("sheet", [
    pytest.param(ORDER, id="a sale"), pytest.param(PURCHASE, id="a purchase"),
    pytest.param(replace(ORDER, items=FULL_ORDER, rounding=Decimal("0.01")), id="a full page, rounded"),
])
def test_the_figures_on_a_purchase_order_add_up(tmp_path: Path, sheet: OrderSheet) -> None:
    path = tmp_path / "po.pdf"
    render_purchase_order(path, sheet)
    _assert_adds_up(path)


def test_a_full_purchase_order_still_fits_its_page(tmp_path: Path) -> None:
    import pdfplumber

    path = tmp_path / "po.pdf"
    render_purchase_order(path, replace(ORDER, items=FULL_ORDER, rounding=Decimal("0.01")))
    assert len(read_text(path)) == 1
    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[0]
        signed = max(w["bottom"] for w in page.extract_words() if w["text"] == "Authorised")
        footer_rule = max(r["top"] for r in page.rects if r["height"] < 1)
        assert signed < footer_rule


def test_more_items_than_a_purchase_order_holds_are_refused(tmp_path: Path) -> None:
    items = tuple(LineItem(description=f"Part {n}", quantity=1, unit_price=Decimal(1), amount=Decimal(1))
                  for n in range(MAX_ORDER_ITEMS + 1))
    with pytest.raises(ValueError, match="do not fit one page"):
        render_purchase_order(tmp_path / "po.pdf", replace(ORDER, items=items, rounding=Decimal(0)))


@pytest.mark.parametrize("name", LONG_NAMES)
def test_a_long_buyer_name_shares_the_header_with_the_title(tmp_path: Path, name: str) -> None:
    path = tmp_path / "po.pdf"
    render_purchase_order(path, replace(ORDER, buyer=name))
    assert read_text(path)[0].splitlines()[0] == f"{name} PURCHASE ORDER"


@pytest.mark.parametrize("sheet", [ORDER, PURCHASE, replace(ORDER, items=FULL_ORDER, rounding=Decimal("-0.01"))])
def test_a_purchase_order_reads_back_as_it_was_issued(tmp_path: Path, sheet: OrderSheet) -> None:
    path = tmp_path / f"purchase_order_{sheet.number}.pdf"
    render_purchase_order(path, sheet)
    read = _read(path)
    assert isinstance(read, PurchaseOrder)
    assert (read.po_number, read.po_date, read.buyer_name) == (sheet.number, sheet.issued, sheet.buyer)
    assert (read.seller_name, read.seller_uen, read.total) == (sheet.seller, sheet.seller_uen, sheet.total)
    assert [(i.description, i.quantity, i.unit_price, i.amount) for i in read.line_items] == [
        (i.description, i.quantity, i.unit_price, i.amount) for i in sheet.items]
    assert read.source_quotes == {"Total": f"TOTAL (SGD) {sheet.total:,.2f}"}
    assert read.source_quotes["Total"] in read_text(path)[0]


def test_a_purchase_order_row_it_cannot_read_is_refused(order: Path) -> None:
    text = read_text(order)[0].replace("34,", "thirty-four ")
    with pytest.raises(ValueError, match="cannot parse table row"):
        extract_stub._parse_purchase_order("po", [text])


# ---------------------------------------------------------------------------
# The bill of lading
# ---------------------------------------------------------------------------

def test_a_bill_of_lading_is_one_page_and_reads_as_one(bill: Path) -> None:
    pages = read_text(bill)
    assert len(pages) == 1 and classify(pages) == "bill_of_lading"
    assert pages[0].splitlines()[0] == f"{CARRIER} BILL OF LADING"


@pytest.mark.parametrize("shown", [
    "B/L NO", "BL-3001", "SHIPPED ON BOARD", "02 Jan 2026", "INV-1001", "PREPAID",
    "SHIPPER", "CONSIGNEE", "EFG Company", "NOTIFY PARTY", "VESSEL MV Straits Pioneer",
    "PORT OF LOADING Singapore", "PORT OF DISCHARGE Port Klang",
    "1 Air-handling unit AHU-450 40 40 CRATES 34,000.0 260.00", "TOTAL 40 PACKAGES 34,000.0 260.00",
    "apparent good order and condition", "THREE (3)", f"For {CARRIER}, as Carrier",
])
def test_every_part_of_a_bill_of_lading_is_there(bill: Path, shown: str) -> None:
    assert shown in read_text(bill)[0]


def test_cargo_is_packed_weighed_and_measured_from_its_quantity() -> None:
    sheets = STEEL.cargo("Cold-rolled steel sheets, 3 mm", 120)
    assert (sheets.packages, sheets.kind, sheets.weight_kg, sheets.volume_cbm) == (
        12, "BUNDLES", Decimal("8832.0"), Decimal("1.13"))
    assert STEEL.cargo("Cold-rolled steel sheets, 3 mm", 95).packages == 10  # a part bundle is still a bundle


_CARGO = re.compile(r"^\d+ .+ (\d+) (\d+) [A-Z]+ ([\d,]+\.\d) ([\d,]+\.\d{2})$")
_TOTAL = re.compile(r"^TOTAL (\d+) PACKAGES ([\d,]+\.\d) ([\d,]+\.\d{2})$", re.MULTILINE)


def _number(text: str) -> Decimal:
    return Decimal(text.replace(",", ""))


@pytest.mark.parametrize("goods", [
    pytest.param(BILL.goods, id="one line"),
    pytest.param((*BILL.goods, STEEL.cargo("Cold-rolled steel sheets, 3 mm", 120),
                  Packing("PALLETS", 4, Decimal("38"), Decimal("0.12")).cargo("Air-conditioner compressors and coils", 60)),
                 id="three lines"),
])
def test_the_cargo_totals_printed_are_the_sum_of_its_rows(tmp_path: Path, goods: tuple) -> None:
    path = tmp_path / "bl.pdf"
    render_bill_of_lading(path, replace(BILL, goods=goods))
    text = read_text(path)[0]
    rows = [m.groups() for line in text.splitlines() if (m := _CARGO.match(line))]
    assert len(rows) == len(goods)
    (total,) = _TOTAL.findall(text)
    assert int(total[0]) == sum(int(r[1]) for r in rows)
    assert _number(total[1]) == sum(_number(r[2]) for r in rows)
    assert _number(total[2]) == sum(_number(r[3]) for r in rows)


@pytest.mark.parametrize("goods", [BILL.goods, (STEEL.cargo("Cold-rolled steel sheets, 3 mm", 90),)])
def test_a_bill_of_lading_reads_back_as_it_was_issued(tmp_path: Path, goods: tuple) -> None:
    sheet = replace(BILL, goods=goods)
    path = tmp_path / f"bill_of_lading_{sheet.number}.pdf"
    render_bill_of_lading(path, sheet)
    read = _read(path)
    assert isinstance(read, BillOfLading)
    assert (read.bl_number, read.shipped_date, read.invoice_reference) == (sheet.number, sheet.shipped, sheet.invoice_ref)
    assert (read.shipper_name, read.consignee_name) == (sheet.shipper, sheet.consignee)
    assert (read.vessel, read.port_of_loading, read.port_of_discharge) == ("MV Straits Pioneer", "Singapore", "Port Klang")
    assert [(g.description, g.quantity) for g in read.goods] == [(c.description, c.quantity) for c in goods]
    assert all(quote in read_text(path)[0] for quote in read.source_quotes.values())


def test_a_bill_of_lading_row_it_cannot_read_is_refused(bill: Path) -> None:
    text = read_text(bill)[0].replace("40 40 CRATES", "forty CRATES")
    with pytest.raises(ValueError, match="cannot parse table row"):
        extract_stub._parse_bill_of_lading("bl", [text])


# ---------------------------------------------------------------------------
# Both
# ---------------------------------------------------------------------------

def test_nothing_in_either_trips_a_forensic_check(order: Path, bill: Path) -> None:
    for path in (order, bill):
        assert hidden_text(path) == [] and mixed_font_tokens(path) == [], path.name


def test_the_plain_layouts_the_test_packs_use_still_read() -> None:
    folder = Path("tests/fixtures/packs/clean")
    paths = sorted(folder.glob("*.pdf"))
    pack = extract_stub.parse_pack("t", intake("t", paths), paths, None)
    assert pack.purchase_orders and pack.bills_of_lading
