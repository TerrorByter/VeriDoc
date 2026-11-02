"""
gldb/invoice_pdf.py
-------------------
Renders a tax invoice the way a company's accounts system would print it: a
letterhead with the seller's logo, an invoice details strip, the bill-to
block, an item table, totals, payment details and terms. The letterhead,
strip, item table and totals are shared with the purchase order and bill of
lading (gldb.trade_pdf), so a trade's paperwork is one family.

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
- GST is charged at 9%, or at 0% on a zero-rated supply (goods exported from
  Singapore), which the invoice says it is.
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


@dataclass(frozen=True, kw_only=True)
class Priced:
    """A document that prices goods: its items, and the figures that follow from them."""

    number: str
    items: tuple[LineItem, ...]
    rounding: Decimal = Decimal(0)
    #: 9%, or 0 for a zero-rated supply
    gst_rate: Decimal = GST_RATE

    def __post_init__(self) -> None:
        if self.gst_rate not in (GST_RATE, 0):
            raise ValueError(f"{self.number}: GST is charged at {GST_RATE:.0%} or zero-rated, not at {self.gst_rate:.0%}")
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
        return money(self.subtotal * self.gst_rate)

    @property
    def gst_label(self) -> str:
        return f"GST ({self.gst_rate:.0%})"

    @property
    def total(self) -> Decimal:
        return self.subtotal + self.gst + self.rounding


@dataclass(frozen=True, kw_only=True)
class InvoiceSheet(Priced):
    """Everything printed on an invoice."""

    seller: str
    seller_uen: str
    seller_address: tuple[str, ...]
    seller_contact: str
    buyer: str
    buyer_address: tuple[str, ...]
    issued: date
    due: date
    terms: str
    po_number: str
    bank: str
    account_no: str


def items_for_total(total: Decimal, descriptions: tuple[str, str], quantity: int,
                    gst_rate: Decimal = GST_RATE) -> tuple[tuple[LineItem, ...], Decimal]:
    """Two lines, the first of *quantity* units and the second of one, and the
    rounding, so that the invoice they make at *gst_rate* totals exactly
    *total*. The rounding is nothing for most totals; for the one in twelve
    that GST rounded to the cent cannot reach it is a cent either way, as the
    printed invoice shows. Without GST there is never any."""
    subtotal = money(total / (1 + gst_rate))
    rounding = total - subtotal - money(subtotal * gst_rate)  # at most a cent: GST moves a total by half a cent
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


def tint(colour: Colour, strength: float = 0.08) -> Colour:
    """*colour* washed out towards white: the background of a strip or header."""
    r, g, b = (1 - (1 - c) * strength for c in colour)
    return r, g, b


def fmt_money(value: Decimal) -> str:
    return f"{value:,.2f}"


def fmt_day(value: date) -> str:
    return f"{value:%d %b %Y}"


class Page:
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

    def lines(self, x: float, top: float, values: tuple[str, ...] | list[str], *, size: float = 9,
              colour: Colour = INK, leading: float = 12) -> float:
        """*values* one under another from *top*; the y of the last."""
        for n, value in enumerate(values):
            self.text(x, top - leading * n, value, size=size, colour=colour)
        return top - leading * (len(values) - 1)

    def save(self) -> None:
        self.c.showPage()
        self.c.save()


def _logo(page: Page, x: float, y: float, size: float, colour: Colour) -> None:
    """The issuer's mark: a rounded tile in its colour with three rising bars."""
    page.box(x, y, size, size, colour, radius=size * 0.22)
    for n, height in enumerate((0.36, 0.56, 0.78)):
        page.box(x + size * (0.20 + 0.22 * n), y + size * 0.18, size * 0.14, size * height * 0.8, (1, 1, 1))


def _name_size(name: str, title: str, title_size: float) -> float:
    """The size to set *name* at beside the title: full size if it fits, else the size that does."""
    free = RIGHT - stringWidth(title, BOLD, title_size) - NAME_GAP - NAME_X
    size = min(NAME_SIZE, NAME_SIZE * free / stringWidth(name, BOLD, NAME_SIZE))
    if size < MIN_NAME_SIZE:
        raise ValueError(f"{name}: too long for a letterhead (at most {free / MIN_NAME_SIZE * NAME_SIZE:.0f}pt at {NAME_SIZE}pt)")
    return size


def letterhead(page: Page, name: str, lines: tuple[str, ...], title: str, colour: Colour,
               title_size: float = TITLE_SIZE) -> None:
    """The issuer's logo, name and *lines* (address, contact, registration) beside the document's *title*."""
    page.box(0, PAGE_H - 8, PAGE_W, 8, colour)
    _logo(page, MARGIN, PAGE_H - 66, 40, colour)
    size = _name_size(name, title, title_size)
    # The name and the title share a text line, so their tops are level whatever the name's size
    page.text(NAME_X, PAGE_H - 44 + TOP_ABOVE_BASELINE * (title_size - size), name, font=BOLD, size=size)
    page.lines(NAME_X, PAGE_H - 53, lines, size=8, colour=MUTED, leading=11)
    page.text(RIGHT, PAGE_H - 44, title, font=BOLD, size=title_size, colour=colour, align="right")
    page.box(MARGIN, PAGE_H - 108, RIGHT - MARGIN, 1.4, colour)


def details_strip(page: Page, cells: tuple[tuple[str, str], ...], colour: Colour) -> None:
    """The document's key facts under the letterhead: one row of labels, one of values."""
    top = PAGE_H - 122
    page.box(MARGIN, top - 38, RIGHT - MARGIN, 38, tint(colour), radius=4)
    for n, (label, value) in enumerate(cells):
        x = MARGIN + 14 + n * 106
        page.text(x, top - 14, label, font=BOLD, size=7, colour=MUTED)
        page.text(x, top - 29, value, font=BOLD, size=9.5)


def block(page: Page, top: float, label: str, name: str, lines: tuple[str, ...]) -> float:
    """A labelled party (BILL TO, VENDOR, SHIPPER...): its name in bold, then *lines*; the y of the last line."""
    page.text(MARGIN, top, label, font=BOLD, size=7, colour=MUTED)
    page.text(MARGIN, top - 15, name, font=BOLD, size=11)
    return page.lines(MARGIN, top - 28, lines) if lines else top - 15


def item_table(page: Page, items: tuple[LineItem, ...], colour: Colour, top: float) -> float:
    """The priced item table from *top*; the y just below its last row."""
    page.box(MARGIN, top - 22, RIGHT - MARGIN, 22, tint(colour, 0.12))
    columns = (("NO.", MARGIN + 8, "left"), ("DESCRIPTION", MARGIN + 36, "left"), ("QTY", 340, "right"),
               ("UNIT PRICE (SGD)", 450, "right"), ("AMOUNT (SGD)", RIGHT - 8, "right"))
    for label, x, align in columns:
        page.text(x, top - 14.5, label, font=BOLD, size=7, colour=MUTED, align=align)
    y = top - 22
    for n, item in enumerate(items, start=1):
        y -= ROW_H
        cells = (str(n), item.description, str(item.quantity), fmt_money(item.unit_price), fmt_money(item.amount))
        for (_, x, align), value in zip(columns, cells):
            page.text(x, y + 8, value, align=align)
        page.rule(y)
    return y


def totals(page: Page, priced: Priced, colour: Colour, below: float) -> float:
    """Subtotal, GST, any rounding and the total, under the item table; the y below the total."""
    left, y = 350.0, below - 22
    figures = [("Subtotal", priced.subtotal), (priced.gst_label, priced.gst),
               *([("Rounding", priced.rounding)] if priced.rounding else [])]
    for label, value in figures:
        page.text(left + 12, y, label, colour=MUTED)
        page.text(RIGHT - 8, y, fmt_money(value), align="right")
        y -= 20
    page.box(left, y - 9, RIGHT - left, 26, tint(colour, 0.12), radius=3)
    page.text(left + 12, y, "TOTAL (SGD)", font=BOLD, size=10, colour=colour)
    page.text(RIGHT - 8, y, fmt_money(priced.total), font=BOLD, size=11, align="right")
    return y - 9


def paragraph(page: Page, top: float, label: str, text: str) -> float:
    """A labelled paragraph of small print; the y of its last line."""
    page.text(MARGIN, top, label, font=BOLD, size=7, colour=MUTED)
    return page.lines(MARGIN, top - 13, simpleSplit(text, REGULAR, 8, RIGHT - MARGIN), size=8, colour=MUTED, leading=11)


def footer(page: Page, note: str) -> None:
    page.rule(62)
    page.text(PAGE_W / 2, 48, note, size=7.5, colour=MUTED, align="centre")
    page.text(PAGE_W / 2, 37, "Page 1 of 1", size=7.5, colour=MUTED, align="centre")


def _payment_and_terms(page: Page, sheet: InvoiceSheet, below: float) -> None:
    y = below - 30
    page.text(MARGIN, y, "PAYMENT DETAILS", font=BOLD, size=7, colour=MUTED)
    page.lines(MARGIN, y - 14, (f"Account Name: {sheet.seller}", f"Bank: {sheet.bank}",
                                f"Account No.: {sheet.account_no}", f"Reference: {sheet.number}"))
    zero_rated = "Zero-rated supply: goods exported from Singapore. " if sheet.gst_rate == 0 else ""
    paragraph(page, y - 78, "TERMS AND CONDITIONS",
              f"{zero_rated}Payment is due on or before the due date shown above. Please quote the invoice number with "
              f"your payment. Goods remain the property of {sheet.seller} until paid in full.")


def render_invoice(path: Path, sheet: InvoiceSheet) -> None:
    """Write *sheet* to *path* as a one-page PDF."""
    if len(sheet.items) > MAX_ITEMS:
        raise ValueError(f"{sheet.number}: {len(sheet.items)} items do not fit one page (at most {MAX_ITEMS})")
    page, colour = Page(path), brand_colour(sheet.seller)
    letterhead(page, sheet.seller, (*sheet.seller_address, sheet.seller_contact,
                                    f"UEN: {sheet.seller_uen}  |  GST Reg. No.: {sheet.seller_uen}"), TITLE, colour)
    details_strip(page, (("INVOICE NO", sheet.number), ("INVOICE DATE", fmt_day(sheet.issued)),
                         ("DUE DATE", fmt_day(sheet.due)), ("TERMS", sheet.terms), ("PO REFERENCE", sheet.po_number)),
                  colour)
    block(page, PAGE_H - 182, "BILL TO", sheet.buyer, sheet.buyer_address)
    _payment_and_terms(page, sheet, totals(page, sheet, colour, item_table(page, sheet.items, colour, PAGE_H - 232)))
    footer(page, "This is a computer-generated invoice and requires no signature.")
    page.save()
