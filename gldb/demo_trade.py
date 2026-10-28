"""
gldb/demo_trade.py
------------------
The paperwork behind a demo company's trade. For every sale and every goods
purchase on its bank statement there is an invoice, a purchase order and a bill
of lading that agree with the payment to the cent (the rent, utilities,
salaries and bond have none). A sale is an invoice the company issues, paid by
a credit; a purchase is a supplier's invoice to the company, paid by a debit.
The suppliers are registered at ACRA, so their records go into the mock
service too (gldb.demo_packs writes them with the demo companies').

A case may make one trade disagree (see FAULTS), so the checks that compare an
invoice with its order, shipment and payment have something to find.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from gldb.config import money
from gldb.data_factory import BL_SHIP_DAYS_BEFORE_INVOICE, PO_LEAD_DAYS, PRODUCER_REPORTLAB, _set_metadata, _sgt
from gldb.invoice_pdf import InvoiceSheet, items_for_total, render_invoice
from gldb.schemas import Transaction
from gldb.trade_pdf import BillOfLadingSheet, OrderSheet, Packing, render_bill_of_lading, render_purchase_order

BANK = "First Citizens Bank"
SUPPLIER_BRANCH = "Raffles Place Branch"
#: When the ACRA records of the suppliers were issued, as the demo companies' were
RECORDS_ISSUED_ON = "2026-07-09"
TERMS_DAYS = 30
#: Days between an invoice and the payment that settles it
SALE_PAID_AFTER_DAYS = 10
PURCHASE_PAID_AFTER_DAYS = 14
#: An overbilled invoice charges this times the price agreed and paid
OVERBILL = Decimal("1.25")


@dataclass(frozen=True)
class Partner:
    """A company the demo companies trade with, named as a statement names it
    after " - ". Only a supplier has a registry entry (UEN and the rest): a
    customer's UEN is never looked up."""

    name: str
    address: tuple[str, str]
    #: The goods bought or sold, and the service billed with them (never shipped)
    goods: tuple[str, str]
    quantity: int
    #: How the goods are packed for shipping
    packing: Packing
    code: str = ""
    uen: str = ""
    incorporated: str = ""
    account: str = ""
    #: A supplier's director: name and NRIC
    director: tuple[str, str] = ("", "")

    @property
    def legal_name(self) -> str:
        """As ACRA prints it."""
        return self.name.upper()


PARTNERS: tuple[Partner, ...] = (
    Partner("EFG Company", ("88 Tuas South Avenue 6", "Singapore 637051"),
            ("Air-handling unit AHU-450", "Commissioning kit and spares"), 40, Packing("CRATES", 1, Decimal("850"), Decimal("6.5"))),
    Partner("Metallica Inc.", ("15 Kallang Way 2A, #03-01", "Singapore 349336"),
            ("Chilled-water unit CWU-900", "Installation and commissioning"), 25, Packing("CRATES", 1, Decimal("1900"), Decimal("9.8"))),
    Partner("ABC electronics Ltd.", ("30 Pioneer Crescent, #02-11", "Singapore 628559"),
            ("Electronic components, assorted", "Freight and handling"), 500, Packing("CARTONS", 25, Decimal("0.8"), Decimal("0.004")),
            "ABC", "199804567H", "1998-03-12", "201-118-402-557", ("LEONG CHEE KEONG", "S6402345D")),
    Partner("SteelMen Pte Ltd.", ("12 Tuas Avenue 10", "Singapore 639142"),
            ("Cold-rolled steel sheets, 3 mm", "Cutting and delivery"), 120, Packing("BUNDLES", 10, Decimal("73.6"), Decimal("0.0094")),
            "SM", "200709981R", "2007-06-04", "202-663-018-340", ("CHUA HOCK SENG", "S6911234F")),
    Partner("Cool Robotics Ltd.", ("2 Science Park Drive, #05-02", "Singapore 118222"),
            ("Robotic arm assembly unit", "Installation and commissioning"), 2, Packing("CRATES", 1, Decimal("1450"), Decimal("4.2")),
            "CR", "201511222G", "2015-11-19", "203-770-551-216", ("RAJESH MENON", "S7412345H")),
    Partner("Caterpillarz Inc.", ("10 Jalan Besar, #04-17", "Singapore 208787"),
            ("Forklift, 3-tonne diesel", "Delivery and registration"), 4, Packing("UNITS", 1, Decimal("4600"), Decimal("11.5")),
            "CAT", "T98FC1234C", "1998-09-01", "204-082-947-663", ("DAVID HOLLIS", "S8503456J")),
    Partner("Tan & Co.", ("5 Woodlands Industrial Park D, #01-06", "Singapore 757919"),
            ("Air-conditioner compressors and coils", "Delivery and servicing"), 60, Packing("PALLETS", 4, Decimal("38"), Decimal("0.12")),
            "TC", "53123456K", "2009-05-21", "205-419-836-770", ("TAN KAH WEI", "S7101234L")),
)
_BY_NAME = {partner.name: partner for partner in PARTNERS}


def suppliers() -> tuple[Partner, ...]:
    return tuple(partner for partner in PARTNERS if partner.uen)


@dataclass(frozen=True)
class Seller:
    """The company whose statement the trades are read from, as its invoices print it."""

    name: str
    uen: str
    address: tuple[str, ...]
    bank: str
    account: str


@dataclass(frozen=True)
class Trade:
    """One sale or goods purchase, with the documents behind it."""

    partner: Partner
    purchase: bool
    invoice: InvoiceSheet
    order: OrderSheet
    #: No order file is written when this is False
    ordered: bool
    bill: BillOfLadingSheet


def _contact(name: str) -> str:
    digest = hashlib.sha256(name.encode()).digest()
    mail = re.sub(r"[^a-z0-9]", "", name.split()[0].lower())
    return f"Tel +65 6{digest[0] % 10}{digest[1] % 10}{digest[2] % 10} {int.from_bytes(digest[3:5]) % 10000:04d}  |  accounts@{mail}.example"


def _invoice(seller: Seller, partner: Partner, purchase: bool, row: Transaction, number: int) -> Trade:
    paid = row.debit if purchase else row.credit
    assert paid is not None
    issued = row.date - timedelta(days=PURCHASE_PAID_AFTER_DAYS if purchase else SALE_PAID_AFTER_DAYS)
    items, rounding = items_for_total(paid, partner.goods, partner.quantity)
    party: tuple[str, str, tuple[str, ...], str, str]  # name, UEN, address, bank, account of the invoice's seller
    billed: tuple[str, tuple[str, ...]]
    if purchase:
        party = (partner.legal_name, partner.uen, partner.address, f"{BANK}, {SUPPLIER_BRANCH}", partner.account)
        billed = (seller.name, seller.address)
        invoice_no, po_no, bl_no = f"{partner.code}-{issued:%y%m%d}", f"PO-{4000 + number}", f"BL-{5000 + number}"
    else:
        party = (seller.name, seller.uen, seller.address, seller.bank, seller.account)
        billed = (partner.name, partner.address)
        invoice_no, po_no, bl_no = f"INV-{1000 + number}", f"PO-{2000 + number}", f"BL-{3000 + number}"
    terms = f"Net {TERMS_DAYS} days"
    sheet = InvoiceSheet(
        seller=party[0], seller_uen=party[1], seller_address=party[2], seller_contact=_contact(party[0]),
        buyer=billed[0], buyer_address=billed[1], number=invoice_no, issued=issued,
        due=issued + timedelta(days=TERMS_DAYS), terms=terms, po_number=po_no, items=items,
        bank=party[3], account_no=party[4], rounding=rounding,
    )
    # The order is the buyer's, priced as the invoice is; a customer's letterhead carries no UEN
    order = OrderSheet(
        buyer=sheet.buyer, buyer_uen=seller.uen if purchase else "", buyer_address=sheet.buyer_address,
        buyer_contact=_contact(sheet.buyer), seller=sheet.seller, seller_uen=sheet.seller_uen,
        seller_address=sheet.seller_address, number=po_no, issued=issued - timedelta(days=PO_LEAD_DAYS),
        deliver_by=issued, terms=terms, items=items, rounding=rounding,
    )
    # The goods ship; the service billed with them does not
    bill = BillOfLadingSheet(
        number=bl_no, shipper=sheet.seller, shipper_address=sheet.seller_address, consignee=sheet.buyer,
        consignee_address=sheet.buyer_address, shipped=issued - timedelta(days=BL_SHIP_DAYS_BEFORE_INVOICE),
        invoice_ref=invoice_no, goods=(partner.packing.cargo(items[0].description, items[0].quantity),),
    )
    return Trade(partner=partner, purchase=purchase, invoice=sheet, order=order, ordered=True, bill=bill)


def _overbilled(trade: Trade) -> Trade:
    """The invoice charges more than was ordered and paid, for the same goods."""
    items, rounding = items_for_total(money(trade.invoice.total * OVERBILL), trade.partner.goods, trade.partner.quantity)
    return replace(trade, invoice=replace(trade.invoice, items=items, rounding=rounding))


def _short_shipped(trade: Trade) -> Trade:
    """The invoice bills the goods ordered; the bill of lading shows three quarters of them shipped."""
    (cargo,) = trade.bill.goods
    shipped = trade.partner.packing.cargo(cargo.description, cargo.quantity * 3 // 4)
    return replace(trade, bill=replace(trade.bill, goods=(shipped,)))


def _no_order(trade: Trade) -> Trade:
    """The invoice names an order that was never issued."""
    return replace(trade, ordered=False)


FAULTS: dict[str, Callable[[Trade], Trade]] = {
    "overbilled": _overbilled, "short_shipped": _short_shipped, "no_order": _no_order,
}


def plan(seller: Seller, rows: list[Transaction], faults: tuple[tuple[str, str], ...]) -> list[Trade]:
    """The trades on a statement's *rows*, in statement order, with each of
    *faults* (partner name, fault name) applied to that partner's trade."""
    counts = {True: 0, False: 0}
    trades = []
    for row in rows:
        partner = _BY_NAME.get(row.description.partition(" - ")[2])
        if partner is not None:
            purchase = row.debit is not None
            counts[purchase] += 1
            trades.append(_invoice(seller, partner, purchase, row, counts[purchase]))
    by_partner = {trade.partner.name: trade for trade in trades}
    for name, fault in faults:
        if name not in by_partner:
            raise ValueError(f"trade fault {fault!r} is for {name!r}, which has no trade on this statement")
        if fault not in FAULTS:
            raise ValueError(f"unknown trade fault {fault!r}; the faults are {', '.join(FAULTS)}")
        by_partner[name] = FAULTS[fault](by_partner[name])
    return [by_partner[trade.partner.name] for trade in trades]


def _stamp(path: Path, day: date) -> None:
    """Make *path* look made on *day*: created and last saved that day, and never again."""
    _set_metadata(path, PRODUCER_REPORTLAB, _sgt(day), _sgt(day))


def write(folder: Path, trade: Trade) -> None:
    """Write a trade's invoice, order (unless none was issued) and bill of lading into *folder*."""
    invoice = trade.invoice
    path = folder / f"invoice_{invoice.number}.pdf"
    render_invoice(path, invoice)
    _stamp(path, invoice.issued)
    if trade.ordered:
        path = folder / f"purchase_order_{trade.order.number}.pdf"
        render_purchase_order(path, trade.order)
        _stamp(path, trade.order.issued)
    path = folder / f"bill_of_lading_{trade.bill.number}.pdf"
    render_bill_of_lading(path, trade.bill)
    _stamp(path, trade.bill.shipped)


def acra_record(supplier: Partner) -> dict[str, Any]:
    """ACRA's record of *supplier*, as the mock service serves it."""
    postal = re.search(r"\d{6}", supplier.address[1])
    assert postal is not None
    director, nric = supplier.director
    return {
        "company_name": supplier.legal_name, "uen": supplier.uen, "incorporation_date": supplier.incorporated,
        "status": "LIVE COMPANY", "registered_address": f"{supplier.address[0].replace(',', '').upper()} ({postal.group()})",
        "issued_on": RECORDS_ISSUED_ON,
        "officers": [{"name": director, "id_number": nric, "position": "DIRECTOR", "appointed": supplier.incorporated,
                      "postal_code": postal.group()}],
        "shareholders": [{"name": director, "shares": 100000}],
        "financials": None,
    }
