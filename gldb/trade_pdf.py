"""
gldb/trade_pdf.py
-----------------
Renders the rest of a trade's paperwork in the invoice's family
(gldb.invoice_pdf): the buyer's purchase order, the carrier's bill of lading
for goods shipped abroad and the supplier's delivery order for goods it
delivered in Singapore.

The invoice's rules hold here too: every piece of text is visible, labels and
values never touch in different fonts, blocks are stacked so each text line
holds one thing, and the figures add up. A purchase order's subtotal, GST and
total are worked out from its items, as an invoice's are. A bill of lading's
packages, weight and volume are worked out from each line's quantity and
packing, and its totals row is the sum of its lines; a delivery order's
cargo table is the same.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from gldb.data_factory import BL_PORT_OF_DISCHARGE, BL_PORT_OF_LOADING, BL_VESSEL
from gldb.invoice_pdf import (
    BOLD,
    INK,
    MARGIN,
    MUTED,
    PAGE_H,
    RIGHT,
    ROW_H,
    Colour,
    Page,
    Priced,
    block,
    brand_colour,
    details_strip,
    fmt_day,
    footer,
    item_table,
    letterhead,
    paragraph,
    tint,
    totals,
)

#: Both titles are wider than "TAX INVOICE", so they are set smaller to leave a long name room
TITLE_SIZE = 20
#: The vendor and delivery blocks and the signature take the room of three item rows
MAX_ORDER_ITEMS = 9
MAX_CARGO_LINES = 6
#: The fictional shipping line that carries every demo shipment
CARRIER = "LION CITY LINES PTE. LTD."
CARRIER_LINES = ("1 HarbourFront Avenue, #12-01 Keppel Bay Tower", "Singapore 098632",
                 "Tel +65 6270 8800  |  documentation@lioncitylines.example")
ORIGINALS = "THREE (3)"
KG, CBM = Decimal("0.1"), Decimal("0.01")


@dataclass(frozen=True, kw_only=True)
class OrderSheet(Priced):
    """Everything printed on a purchase order. A buyer the registry never
    looks up (a customer) has no UEN, and its letterhead prints none."""

    buyer: str
    buyer_uen: str
    buyer_address: tuple[str, ...]
    buyer_contact: str
    seller: str
    seller_uen: str
    seller_address: tuple[str, ...]
    issued: date
    deliver_by: date
    terms: str


@dataclass(frozen=True)
class Cargo:
    """One line of a bill of lading: goods, how many, and how they travel."""

    description: str
    quantity: int
    packages: int
    kind: str
    weight_kg: Decimal
    volume_cbm: Decimal


@dataclass(frozen=True)
class Packing:
    """How a kind of goods is packed: *per_package* units to a package of
    *kind*, each unit weighing *kg* and taking *cbm*."""

    kind: str
    per_package: int
    kg: Decimal
    cbm: Decimal

    def cargo(self, description: str, quantity: int) -> Cargo:
        """*quantity* units packed: a part package is still a package."""
        return Cargo(description, quantity, math.ceil(quantity / self.per_package), self.kind,
                     (quantity * self.kg).quantize(KG), (quantity * self.cbm).quantize(CBM))


@dataclass(frozen=True, kw_only=True)
class Consignment:
    """Goods sent to a buyer, line by line, and their totals."""

    number: str
    goods: tuple[Cargo, ...]

    @property
    def packages(self) -> int:
        return sum(c.packages for c in self.goods)

    @property
    def weight_kg(self) -> Decimal:
        return sum((c.weight_kg for c in self.goods), Decimal(0))

    @property
    def volume_cbm(self) -> Decimal:
        return sum((c.volume_cbm for c in self.goods), Decimal(0))


@dataclass(frozen=True, kw_only=True)
class BillOfLadingSheet(Consignment):
    """Everything printed on a bill of lading."""

    shipper: str
    shipper_address: tuple[str, ...]
    consignee: str
    consignee_address: tuple[str, ...]
    shipped: date
    invoice_ref: str
    vessel: str = BL_VESSEL
    port_of_loading: str = BL_PORT_OF_LOADING
    port_of_discharge: str = BL_PORT_OF_DISCHARGE

    @property
    def voyage(self) -> str:
        """The sailing: the year and week it left, northbound."""
        return f"{self.shipped:%y}{self.shipped.isocalendar().week:02d}N"


@dataclass(frozen=True, kw_only=True)
class DeliveryOrderSheet(Consignment):
    """Everything printed on a delivery order: the supplier's, for goods its
    own transport delivered, signed by the buyer on receipt."""

    supplier: str
    supplier_address: tuple[str, ...]
    supplier_contact: str
    consignee: str
    consignee_address: tuple[str, ...]
    delivered: date
    invoice_ref: str
    order_ref: str


def _signature(page: Page, top: float, signed_for: str, colour: Colour, signer: str = "Authorised signatory") -> float:
    """A signature over a line, who signs and for whom; the y of the last line."""
    c = page.c
    c.setStrokeColorRGB(*colour)
    c.setLineWidth(1.1)
    stroke = c.beginPath()
    stroke.moveTo(MARGIN + 6, top - 22)
    stroke.curveTo(MARGIN + 22, top + 4, MARGIN + 30, top - 30, MARGIN + 52, top - 12)
    stroke.curveTo(MARGIN + 70, top + 2, MARGIN + 84, top - 26, MARGIN + 120, top - 16)
    c.drawPath(stroke, stroke=1, fill=0)
    page.rule(top - 30, INK, MARGIN, MARGIN + 190)
    return page.lines(MARGIN, top - 42, (signer, signed_for), size=8, colour=MUTED, leading=11)


def _rows(page: Page, top: float, rows: tuple[tuple[str, str], ...]) -> float:
    """Label and value rows, ruled; the y below the last."""
    y = top
    for label, value in rows:
        page.text(MARGIN, y, label, font=BOLD, size=7, colour=MUTED)
        page.text(MARGIN + 120, y, value)
        page.rule(y - 7)
        y -= 18
    return y


def render_purchase_order(path: Path, sheet: OrderSheet) -> None:
    """Write *sheet* to *path* as a one-page PDF on the buyer's letterhead."""
    if len(sheet.items) > MAX_ORDER_ITEMS:
        raise ValueError(f"{sheet.number}: {len(sheet.items)} items do not fit one page (at most {MAX_ORDER_ITEMS})")
    page, colour = Page(path), brand_colour(sheet.buyer)
    registered = (f"UEN: {sheet.buyer_uen}",) if sheet.buyer_uen else ()
    letterhead(page, sheet.buyer, (*sheet.buyer_address, sheet.buyer_contact, *registered), "PURCHASE ORDER", colour,
               TITLE_SIZE)
    details_strip(page, (("PO NUMBER", sheet.number), ("ORDER DATE", fmt_day(sheet.issued)),
                         ("DELIVER BY", fmt_day(sheet.deliver_by)), ("PAYMENT TERMS", sheet.terms), ("CURRENCY", "SGD")),
                  colour)
    y = block(page, PAGE_H - 182, "VENDOR", sheet.seller, (f"UEN: {sheet.seller_uen}", *sheet.seller_address))
    y = block(page, y - 24, "DELIVER TO", sheet.buyer, sheet.buyer_address)
    y = totals(page, sheet, colour, item_table(page, sheet.items, colour, y - 18))
    y = paragraph(page, y - 28, "TERMS AND CONDITIONS",
                  f"Please quote this order number on your invoice, delivery order and shipping documents. The goods must "
                  f"match the description, quantity and price above and arrive by the delivery date. Payment is due "
                  f"{sheet.terms.lower()} from receipt of a correct invoice.")
    _signature(page, y - 22, f"For and on behalf of {sheet.buyer}", colour)
    footer(page, f"Purchase order {sheet.number}  |  Issued by {sheet.buyer}")
    page.save()


def _cargo_table(page: Page, sheet: Consignment, colour: Colour, top: float) -> float:
    """The cargo lines and their totals row; the y below the totals."""
    page.box(MARGIN, top - 22, RIGHT - MARGIN, 22, tint(colour, 0.12))
    columns = (("NO.", MARGIN + 8, "left"), ("DESCRIPTION OF GOODS", MARGIN + 36, "left"), ("QTY", 290, "right"),
               ("PACKAGES", 366, "right"), ("GROSS WEIGHT (KG)", 448, "right"), ("MEASUREMENT (CBM)", RIGHT - 8, "right"))
    for label, x, align in columns:
        page.text(x, top - 14.5, label, font=BOLD, size=7, colour=MUTED, align=align)
    y = top - 22
    for n, cargo in enumerate(sheet.goods, start=1):
        y -= ROW_H
        cells = (str(n), cargo.description, str(cargo.quantity), f"{cargo.packages} {cargo.kind}",
                 f"{cargo.weight_kg:,.1f}", f"{cargo.volume_cbm:,.2f}")
        for (_, x, align), value in zip(columns, cells):
            page.text(x, y + 8, value, align=align)
        page.rule(y)
    y -= 26
    page.box(MARGIN, y, RIGHT - MARGIN, 26, tint(colour, 0.12), radius=3)
    page.text(MARGIN + 8, y + 9, "TOTAL", font=BOLD, size=9, colour=colour)
    for x, value in ((366, f"{sheet.packages} PACKAGES"), (448, f"{sheet.weight_kg:,.1f}"),
                     (RIGHT - 8, f"{sheet.volume_cbm:,.2f}")):
        page.text(x, y + 9, value, font=BOLD, size=9, align="right")
    return y


def render_bill_of_lading(path: Path, sheet: BillOfLadingSheet) -> None:
    """Write *sheet* to *path* as a one-page PDF on the carrier's letterhead."""
    _fits(sheet)
    page, colour = Page(path), brand_colour(CARRIER)
    letterhead(page, CARRIER, CARRIER_LINES, "BILL OF LADING", colour, TITLE_SIZE)
    details_strip(page, (("B/L NO", sheet.number), ("SHIPPED ON BOARD", fmt_day(sheet.shipped)),
                         ("VOYAGE NO", sheet.voyage), ("INVOICE REF", sheet.invoice_ref), ("FREIGHT", "PREPAID")),
                  colour)
    y = block(page, PAGE_H - 182, "SHIPPER", sheet.shipper, sheet.shipper_address)
    y = block(page, y - 22, "CONSIGNEE", sheet.consignee, sheet.consignee_address)
    y = block(page, y - 22, "NOTIFY PARTY", "Same as consignee", ())
    y = _rows(page, y - 26, (("VESSEL", sheet.vessel), ("PORT OF LOADING", sheet.port_of_loading),
                             ("PORT OF DISCHARGE", sheet.port_of_discharge)))
    y = _cargo_table(page, sheet, colour, y - 4)
    y = paragraph(page, y - 24, "TERMS OF CARRIAGE",
                  "SHIPPED on board the vessel named above in apparent good order and condition, unless otherwise "
                  "stated, the goods described above for carriage to the port of discharge. Weight, measure, marks and "
                  "contents are as declared by the shipper and unknown to the carrier. One original, duly endorsed, "
                  "must be surrendered in exchange for the goods; the others then stand void.")
    y = _rows(page, y - 24, (("PLACE AND DATE OF ISSUE", f"{sheet.port_of_loading}, {fmt_day(sheet.shipped)}"),
                             ("NUMBER OF ORIGINALS", ORIGINALS)))
    _signature(page, y - 10, f"For {CARRIER}, as Carrier", colour)
    footer(page, "Original. Carriage is subject to the carrier's standard conditions, available on request.")
    page.save()


def _fits(sheet: Consignment) -> None:
    if len(sheet.goods) > MAX_CARGO_LINES:
        raise ValueError(f"{sheet.number}: {len(sheet.goods)} cargo lines do not fit one page (at most {MAX_CARGO_LINES})")


def render_delivery_order(path: Path, sheet: DeliveryOrderSheet) -> None:
    """Write *sheet* to *path* as a one-page PDF on the supplier's letterhead."""
    _fits(sheet)
    page, colour = Page(path), brand_colour(sheet.supplier)
    letterhead(page, sheet.supplier, (*sheet.supplier_address, sheet.supplier_contact), "DELIVERY ORDER", colour,
               TITLE_SIZE)
    details_strip(page, (("DO NUMBER", sheet.number), ("DELIVERY DATE", fmt_day(sheet.delivered)),
                         ("INVOICE REF", sheet.invoice_ref), ("ORDER REF", sheet.order_ref)), colour)
    y = block(page, PAGE_H - 182, "DELIVER TO", sheet.consignee, sheet.consignee_address)
    y = _cargo_table(page, sheet, colour, y - 26)
    y = paragraph(page, y - 24, "RECEIPT",
                  "Received the goods above in good order and condition. Any shortage or damage must be noted on this "
                  "delivery order when the goods are delivered; none can be claimed afterwards.")
    _signature(page, y - 22, f"For and on behalf of {sheet.consignee}", colour, "Received by")
    footer(page, f"Delivery order {sheet.number}  |  Delivered by {sheet.supplier}")
    page.save()
