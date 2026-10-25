"""
gldb/invoice_pdf.py
-------------------
Renders a tax invoice the way a company's accounts system would print it: a
letterhead with the seller's logo, an invoice details strip, the bill-to
block, an item table, totals, payment details and terms.

Rules the rest of the system relies on:
- Every piece of text is visible (never white or tiny), and a label and its
  value are never drawn touching in different fonts, so the forensic checks
  find nothing in a genuine invoice.
- The page's text, read top to bottom, is the invoice: the title says
  "TAX INVOICE", every figure is printed once as written to the page, and the
  blocks are stacked rather than side by side, so each text line holds one thing.
- An invoice always adds up: its subtotal, GST and total are worked out from
  its items, never given, and a line that is not quantity times unit price
  cannot be issued. The only figure besides the items is a rounding of at most
  a cent, printed as its own line, for a total GST rounded to the cent cannot reach.
"""

from __future__ import annotations

import colorsys
import hashlib
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas as rl_canvas

from gldb.config import GST_RATE, money
from gldb.schemas import LineItem

PAGE_W, PAGE_H = A4
MARGIN = 42.0
RIGHT = PAGE_W - MARGIN
ROW_H = 22.0
#: More item rows than this would run into the terms and footer
MAX_ITEMS = 12
#: The most an invoice may round its total, in either direction
MAX_ROUNDING = Decimal("0.01")
GST_LABEL = "GST (9%)"
TITLE = "TAX INVOICE"
TITLE_SIZE = 24
#: The seller's name is set at its full size unless the title would touch it, then smaller; below this it is refused
NAME_SIZE, MIN_NAME_SIZE = 16, 11
NAME_X = MARGIN + 54
NAME_GAP = 12
#: How far a character's top lies above its baseline, as a share of its size: text lines are grouped by their tops
TOP_ABOVE_BASELINE = 0.793
#: The share of an invoice's subtotal that its first line carries when items are made to fit a total
MAIN_LINE_SHARE = Decimal("0.9")

REGULAR, BOLD = "Helvetica", "Helvetica-Bold"
INK = (0.10, 0.11, 0.13)
MUTED = (0.38, 0.40, 0.44)
RULE = (0.80, 0.82, 0.85)

Colour = tuple[float, float, float]


@dataclass(frozen=True)
class InvoiceSheet:
    """Everything printed on an invoice."""

    seller: str
    seller_uen: str
    seller_address: tuple[str, ...]
    seller_contact: str
    buyer: str
    buyer_address: tuple[str, ...]
    number: str
    issued: date
    due: date
    terms: str
    po_number: str
    items: tuple[LineItem, ...]
    bank: str
    account_no: str
    rounding: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if abs(self.rounding) > MAX_ROUNDING:
            raise ValueError(f"{self.number}: a rounding of {self.rounding} is more than {MAX_ROUNDING}")
        for item in self.items:
            if money(item.quantity * item.unit_price) != item.amount:
                raise ValueError(f"{self.number}: {item.description}: {item.quantity} x {item.unit_price} is not {item.amount}")

    @property
    def subtotal(self) -> Decimal:
        return sum((item.amount for item in self.items), Decimal(0))

    @property
    def gst(self) -> Decimal:
        return money(self.subtotal * GST_RATE)

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.gst + self.rounding


def items_for_total(total: Decimal, descriptions: tuple[str, str], quantity: int) -> tuple[tuple[LineItem, ...], Decimal]:
    """Two lines, the first of *quantity* units and the second of one, and the
    rounding, so that the invoice they make totals exactly *total*. The rounding
    is nothing for most totals; for the one in twelve that GST rounded to the
    cent cannot reach it is a cent either way, as the printed invoice shows."""
    subtotal = money(total / (1 + GST_RATE))
    rounding = total - subtotal - money(subtotal * GST_RATE)  # at most a cent: GST moves a total by half a cent
    unit_price = money(subtotal * MAIN_LINE_SHARE / quantity)
    main = quantity * unit_price
    return (
        LineItem(description=descriptions[0], quantity=quantity, unit_price=unit_price, amount=main),
        LineItem(description=descriptions[1], quantity=1, unit_price=subtotal - main, amount=subtotal - main),
    ), rounding


def brand_colour(name: str) -> Colour:
    """The seller's colour: a deep, saturated hue taken from its name, so a
    company's documents share one and two companies rarely do."""
    hue = hashlib.sha256(name.encode()).digest()[0] / 255
    return colorsys.hls_to_rgb(hue, 0.30, 0.55)


def _tint(colour: Colour, strength: float = 0.08) -> Colour:
    """*colour* washed out towards white: the background of a strip or header."""
    r, g, b = (1 - (1 - c) * strength for c in colour)
    return r, g, b


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _day(value: date) -> str:
    return f"{value:%d %b %Y}"


class _Page:
    """Drawing helpers over one canvas: text in a font, size and colour."""

    def __init__(self, path: Path) -> None:
        self.c = rl_canvas.Canvas(str(path), pagesize=A4)

    def text(self, x: float, y: float, value: str, *, font: str = REGULAR, size: float = 9, colour: Colour = INK,
             align: str = "left") -> None:
        self.c.setFont(font, size)
        self.c.setFillColorRGB(*colour)
        {"left": self.c.drawString, "right": self.c.drawRightString, "centre": self.c.drawCentredString}[align](x, y, value)

    def box(self, x: float, y: float, w: float, h: float, colour: Colour, radius: float = 0) -> None:
        self.c.setFillColorRGB(*colour)
        if radius:
            self.c.roundRect(x, y, w, h, radius, stroke=0, fill=1)
        else:
            self.c.rect(x, y, w, h, stroke=0, fill=1)

    def rule(self, y: float, colour: Colour = RULE, x0: float = MARGIN, x1: float = RIGHT) -> None:
        self.box(x0, y, x1 - x0, 0.6, colour)


def _logo(page: _Page, x: float, y: float, size: float, colour: Colour) -> None:
    """The seller's mark: a rounded tile in its colour with three rising bars."""
    page.box(x, y, size, size, colour, radius=size * 0.22)
    for n, height in enumerate((0.36, 0.56, 0.78)):
        page.box(x + size * (0.20 + 0.22 * n), y + size * 0.18, size * 0.14, size * height * 0.8, (1, 1, 1))


def _name_size(name: str) -> float:
    """The size to set *name* at beside the title: full size if it fits, else the size that does."""
    free = RIGHT - stringWidth(TITLE, BOLD, TITLE_SIZE) - NAME_GAP - NAME_X
    size = min(NAME_SIZE, NAME_SIZE * free / stringWidth(name, BOLD, NAME_SIZE))
    if size < MIN_NAME_SIZE:
        raise ValueError(f"{name}: too long for a letterhead (at most {free / MIN_NAME_SIZE * NAME_SIZE:.0f}pt at {NAME_SIZE}pt)")
    return size


def _letterhead(page: _Page, sheet: InvoiceSheet, colour: Colour) -> None:
    page.box(0, PAGE_H - 8, PAGE_W, 8, colour)
    _logo(page, MARGIN, PAGE_H - 66, 40, colour)
    size = _name_size(sheet.seller)
    # The name and the title share a text line, so their tops are level whatever the name's size
    page.text(NAME_X, PAGE_H - 44 + TOP_ABOVE_BASELINE * (TITLE_SIZE - size), sheet.seller, font=BOLD, size=size)
    lines = (*sheet.seller_address, sheet.seller_contact, f"UEN: {sheet.seller_uen}  |  GST Reg. No.: {sheet.seller_uen}")
    for n, line in enumerate(lines):
        page.text(NAME_X, PAGE_H - 53 - 11 * n, line, size=8, colour=MUTED)
    page.text(RIGHT, PAGE_H - 44, TITLE, font=BOLD, size=TITLE_SIZE, colour=colour, align="right")
    page.box(MARGIN, PAGE_H - 108, RIGHT - MARGIN, 1.4, colour)


def _details_strip(page: _Page, sheet: InvoiceSheet, colour: Colour) -> None:
    top = PAGE_H - 122
    page.box(MARGIN, top - 38, RIGHT - MARGIN, 38, _tint(colour), radius=4)
    cells = (("INVOICE NO", sheet.number), ("INVOICE DATE", _day(sheet.issued)), ("DUE DATE", _day(sheet.due)),
             ("TERMS", sheet.terms), ("PO REFERENCE", sheet.po_number))
    for n, (label, value) in enumerate(cells):
        x = MARGIN + 14 + n * 106
        page.text(x, top - 14, label, font=BOLD, size=7, colour=MUTED)
        page.text(x, top - 29, value, font=BOLD, size=9.5)


def _bill_to(page: _Page, sheet: InvoiceSheet) -> None:
    top = PAGE_H - 182
    page.text(MARGIN, top, "BILL TO", font=BOLD, size=7, colour=MUTED)
    page.text(MARGIN, top - 15, sheet.buyer, font=BOLD, size=11)
    for n, line in enumerate(sheet.buyer_address):
        page.text(MARGIN, top - 28 - 12 * n, line, size=9)


def _items(page: _Page, sheet: InvoiceSheet, colour: Colour) -> float:
    """The item table; the y just below its last row."""
    top = PAGE_H - 232
    page.box(MARGIN, top - 22, RIGHT - MARGIN, 22, _tint(colour, 0.12))
    columns = (("NO.", MARGIN + 8, "left"), ("DESCRIPTION", MARGIN + 36, "left"), ("QTY", 340, "right"),
               ("UNIT PRICE (SGD)", 450, "right"), ("AMOUNT (SGD)", RIGHT - 8, "right"))
    for label, x, align in columns:
        page.text(x, top - 14.5, label, font=BOLD, size=7, colour=MUTED, align=align)
    y = top - 22
    for n, item in enumerate(sheet.items, start=1):
        y -= ROW_H
        cells = (str(n), item.description, str(item.quantity), _money(item.unit_price), _money(item.amount))
        for (_, x, align), value in zip(columns, cells):
            page.text(x, y + 8, value, align=align)
        page.rule(y)
    return y


def _totals(page: _Page, sheet: InvoiceSheet, colour: Colour, below: float) -> float:
    left, y = 350.0, below - 22
    figures = [("Subtotal", sheet.subtotal), (GST_LABEL, sheet.gst), *([("Rounding", sheet.rounding)] if sheet.rounding else [])]
    for label, value in figures:
        page.text(left + 12, y, label, colour=MUTED)
        page.text(RIGHT - 8, y, _money(value), align="right")
        y -= 20
    page.box(left, y - 9, RIGHT - left, 26, _tint(colour, 0.12), radius=3)
    page.text(left + 12, y, "TOTAL (SGD)", font=BOLD, size=10, colour=colour)
    page.text(RIGHT - 8, y, _money(sheet.total), font=BOLD, size=11, align="right")
    return y - 9


def _payment_and_terms(page: _Page, sheet: InvoiceSheet, below: float) -> None:
    y = below - 30
    page.text(MARGIN, y, "PAYMENT DETAILS", font=BOLD, size=7, colour=MUTED)
    for n, line in enumerate((f"Account Name: {sheet.seller}", f"Bank: {sheet.bank}",
                              f"Account No.: {sheet.account_no}", f"Reference: {sheet.number}")):
        page.text(MARGIN, y - 14 - 12 * n, line, size=9)
    y -= 78
    page.text(MARGIN, y, "TERMS AND CONDITIONS", font=BOLD, size=7, colour=MUTED)
    terms = (f"Payment is due on or before the due date shown above. Please quote the invoice number with your payment. "
             f"Goods remain the property of {sheet.seller} until paid in full.")
    for n, line in enumerate(simpleSplit(terms, REGULAR, 8, RIGHT - MARGIN)):
        page.text(MARGIN, y - 13 - 11 * n, line, size=8, colour=MUTED)


def _footer(page: _Page) -> None:
    page.rule(62)
    page.text(PAGE_W / 2, 48, "This is a computer-generated invoice and requires no signature.", size=7.5, colour=MUTED,
              align="centre")
    page.text(PAGE_W / 2, 37, "Page 1 of 1", size=7.5, colour=MUTED, align="centre")


def render_invoice(path: Path, sheet: InvoiceSheet) -> None:
    """Write *sheet* to *path* as a one-page PDF."""
    if len(sheet.items) > MAX_ITEMS:
        raise ValueError(f"{sheet.number}: {len(sheet.items)} items do not fit one page (at most {MAX_ITEMS})")
    page, colour = _Page(path), brand_colour(sheet.seller)
    _letterhead(page, sheet, colour)
    _details_strip(page, sheet, colour)
    _bill_to(page, sheet)
    _payment_and_terms(page, sheet, _totals(page, sheet, colour, _items(page, sheet, colour)))
    _footer(page)
    page.c.showPage()
    page.c.save()
