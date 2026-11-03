"""
gldb/demo_trade.py
------------------
The paperwork behind a demo company's trade. For every sale and every goods
purchase on its bank statement there is an invoice, a purchase order and a
shipping document that agree with the payment to the cent (the rent,
utilities, salaries and bond have none). A sale is an invoice the company
issues, paid by a credit; a purchase is a supplier's invoice to the company,
paid by a debit.

Every demo company exports what it sells and buys in Singapore:
- A sale is to a buyer in Malaysia: the goods go by sea, so a carrier's bill
  of lading takes them to the buyer's port, and the invoice is zero-rated (GST
  at 0%, as on any export of goods). The buyer is not registered in Singapore,
  so ACRA is never asked about it.
- A purchase is from a supplier in Singapore, registered at ACRA (its records
  go into the mock service with the demo companies', see gldb.demo_packs): it
  charges GST at 9% and delivers by its own transport, which its delivery
  order, signed on receipt, records.

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

from gldb.config import GST_RATE, money
from gldb.data_factory import BL_SHIP_DAYS_BEFORE_INVOICE, PO_LEAD_DAYS, _set_metadata, _sgt
from gldb.invoice_pdf import InvoiceSheet, items_for_total, render_invoice
from gldb.schemas import Transaction
from gldb.trade_pdf import (
    CARRIER,
    BillOfLadingSheet,
    DeliveryOrderSheet,
    OrderSheet,
    Packing,
    render_bill_of_lading,
    render_delivery_order,
    render_purchase_order,
)

#: The demo companies' bank: the one whose statement form the team's sample is
BANK = "First Citizens Bank"
#: When the ACRA records of the suppliers were issued, as the demo companies' were
RECORDS_ISSUED_ON = "2026-07-09"
TERMS_DAYS = 30
#: Days between an invoice and the payment that settles it
SALE_PAID_AFTER_DAYS = 10
PURCHASE_PAID_AFTER_DAYS = 14
#: An overbilled invoice charges this times the price agreed and paid
OVERBILL = Decimal("1.25")
#: Dialling prefixes: a Singapore office line, a Malaysian one
SINGAPORE_LINE, MALAYSIA_LINE = "+65 6", "+60 3"


@dataclass(frozen=True)
class Partner:
    """A company a demo company trades with, named as its statement names it
    after " - ". A customer is in Malaysia and has a port of discharge; a
    supplier is in Singapore and has a registry entry (UEN and the rest)."""

    name: str
    address: tuple[str, str]
    #: What the statement says a payment to or from it is for: "Sales Revenue", "Steel Sheets"
    paid_for: str
    #: The goods bought or sold, and the service billed with them (never shipped)
    goods: tuple[str, str]
    quantity: int
    #: How the goods are packed for shipping
    packing: Packing
    #: Starts a customer's order numbers, and a supplier's invoice and delivery order numbers
    code: str
    #: Where a customer's goods are discharged
    port: str = ""
    uen: str = ""
    incorporated: str = ""
    #: A supplier's bank and branch, and its account there
    bank: str = ""
    account: str = ""
    #: A supplier's director: name and NRIC
    director: tuple[str, str] = ("", "")

    @property
    def legal_name(self) -> str:
        """As ACRA prints it."""
        return self.name.upper()

    @property
    def supplier(self) -> bool:
        return bool(self.uen)

    @property
    def described(self) -> str:
        """As a bank statement describes a payment to or from it."""
        return f"{self.paid_for} - {self.name}"


@dataclass(frozen=True)
class Seller:
    """The company whose statement the trades are read from, as its invoices
    print it, and where its numbering starts: its sales invoices are
    <invoice_prefix>-<numbers_from + n>, its own orders PO-<numbers_from + 3000 + n>."""

    name: str
    uen: str
    address: tuple[str, ...]
    bank: str
    account: str
    invoice_prefix: str
    numbers_from: int


#: The company's own orders are numbered this far above its invoices
ORDER_NUMBERS_ABOVE = 3000


@dataclass(frozen=True)
class Trade:
    """One sale or goods purchase, with the documents behind it."""

    partner: Partner
    purchase: bool
    invoice: InvoiceSheet
    order: OrderSheet
    #: No order file is written when this is False
    ordered: bool
    #: A sale's bill of lading, a purchase's delivery order
    shipment: BillOfLadingSheet | DeliveryOrderSheet


def _contact(name: str, line: str) -> str:
    digest = hashlib.sha256(name.encode()).digest()
    mail = re.sub(r"[^a-z0-9]", "", name.split()[0].lower())
    return f"Tel {line}{digest[0] % 10}{digest[1] % 10}{digest[2] % 10} {int.from_bytes(digest[3:5]) % 10000:04d}  |  accounts@{mail}.example"


def _invoice(seller: Seller, partner: Partner, purchase: bool, row: Transaction, number: int) -> Trade:
    if purchase is not partner.supplier:
        raise ValueError(f"{partner.name}: the demo companies buy only from suppliers in Singapore "
                         "and sell only to buyers abroad")
    paid = row.debit if purchase else row.credit
    assert paid is not None
    issued = row.date - timedelta(days=PURCHASE_PAID_AFTER_DAYS if purchase else SALE_PAID_AFTER_DAYS)
    ordered, sent = issued - timedelta(days=PO_LEAD_DAYS), issued - timedelta(days=BL_SHIP_DAYS_BEFORE_INVOICE)
    # A sale is an export, zero-rated; a purchase in Singapore carries GST
    rate = GST_RATE if purchase else Decimal(0)
    items, rounding = items_for_total(paid, partner.goods, partner.quantity, rate)
    party: tuple[str, str, tuple[str, ...], str, str, str]  # name, UEN, address, contact, bank, account of the invoice's seller
    billed: tuple[str, tuple[str, ...], str]  # name, address, contact of its buyer
    if purchase:
        party = (partner.legal_name, partner.uen, partner.address, _contact(partner.name, SINGAPORE_LINE), partner.bank,
                 partner.account)
        billed = (seller.name, seller.address, _contact(seller.name, SINGAPORE_LINE))
        invoice_no, po_no = f"{partner.code}-{issued:%y%m%d}", f"PO-{seller.numbers_from + ORDER_NUMBERS_ABOVE + number}"
    else:
        party = (seller.name, seller.uen, seller.address, _contact(seller.name, SINGAPORE_LINE), seller.bank,
                 seller.account)
        billed = (partner.name, partner.address, _contact(partner.name, MALAYSIA_LINE))
        invoice_no, po_no = f"{seller.invoice_prefix}-{seller.numbers_from + number}", f"PO{ordered:%y%m}-{partner.code}"
    terms = f"Net {TERMS_DAYS} days"
    sheet = InvoiceSheet(
        seller=party[0], seller_uen=party[1], seller_address=party[2], seller_contact=party[3],
        buyer=billed[0], buyer_address=billed[1], number=invoice_no, issued=issued,
        due=issued + timedelta(days=TERMS_DAYS), terms=terms, po_number=po_no, items=items,
        bank=party[4], account_no=party[5], rounding=rounding, gst_rate=rate,
    )
    # The order is the buyer's, priced as the invoice is; a buyer abroad has no UEN to print
    order = OrderSheet(
        buyer=sheet.buyer, buyer_uen=seller.uen if purchase else "", buyer_address=sheet.buyer_address,
        buyer_contact=billed[2], seller=sheet.seller, seller_uen=sheet.seller_uen,
        seller_address=sheet.seller_address, number=po_no, issued=ordered,
        deliver_by=issued, terms=terms, items=items, rounding=rounding, gst_rate=rate,
    )
    # The goods go to the buyer; the service billed with them does not
    goods = (partner.packing.cargo(items[0].description, items[0].quantity),)
    shipment: BillOfLadingSheet | DeliveryOrderSheet
    if purchase:
        shipment = DeliveryOrderSheet(
            number=f"DO-{partner.code}-{sent:%y%m%d}", supplier=sheet.seller, supplier_address=sheet.seller_address,
            supplier_contact=sheet.seller_contact, consignee=sheet.buyer, consignee_address=sheet.buyer_address,
            delivered=sent, invoice_ref=invoice_no, order_ref=po_no, goods=goods,
        )
    else:
        shipment = BillOfLadingSheet(
            number=f"LCL{sent:%y%m%d}{number}", shipper=sheet.seller, shipper_address=sheet.seller_address,
            consignee=sheet.buyer, consignee_address=sheet.buyer_address, shipped=sent, invoice_ref=invoice_no,
            goods=goods, port_of_discharge=partner.port,
        )
    return Trade(partner=partner, purchase=purchase, invoice=sheet, order=order, ordered=True, shipment=shipment)


def _overbilled(trade: Trade) -> Trade:
    """The invoice charges more than was ordered and paid, for the same goods."""
    invoice = trade.invoice
    items, rounding = items_for_total(money(invoice.total * OVERBILL), trade.partner.goods, trade.partner.quantity,
                                      invoice.gst_rate)
    return replace(trade, invoice=replace(invoice, items=items, rounding=rounding))


def _short_shipped(trade: Trade) -> Trade:
    """The invoice bills the goods ordered; the shipment shows three quarters of them sent."""
    (cargo,) = trade.shipment.goods
    sent = trade.partner.packing.cargo(cargo.description, cargo.quantity * 3 // 4)
    return replace(trade, shipment=replace(trade.shipment, goods=(sent,)))


def _no_order(trade: Trade) -> Trade:
    """The invoice names an order that was never issued."""
    return replace(trade, ordered=False)


FAULTS: dict[str, Callable[[Trade], Trade]] = {
    "overbilled": _overbilled, "short_shipped": _short_shipped, "no_order": _no_order,
}


def plan(seller: Seller, rows: list[Transaction], partners: tuple[Partner, ...],
         faults: tuple[tuple[str, str], ...]) -> list[Trade]:
    """The trades on a statement's *rows* with *partners*, in statement order,
    with each of *faults* (partner name, fault name) applied to that partner's trade.
    A row is a trade only as the statement describes one (Partner.described):
    any other payment to or from a partner, such as a refund, has no paperwork."""
    by_description = {partner.described: partner for partner in partners}
    counts = {True: 0, False: 0}
    trades = []
    for row in rows:
        partner = by_description.get(row.description)
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


#: The software companies make their documents with, as a PDF's Producer
#: names it. Each issuer uses one (see _software), as a real company's
#: accounts or ERP system does, so documents from different companies differ.
ISSUER_SOFTWARE: tuple[str, ...] = (
    "Microsoft: Print To PDF",
    "Microsoft® Word for Microsoft 365",
    "Xero",
    "QuickBooks Online",
    "Oracle BI Publisher 12.2.1.4.0",
    "SAP NetWeaver 7.50",
    "Sage 300 2024",
    "Odoo",
    "macOS Version 14.5 (Build 23F79) Quartz PDFContext",
    "Skia/PDF m126",
)
#: Office hours a document is made in: from this hour, for this many minutes
WORKDAY_START_HOUR = 8
WORKDAY_MINUTES = 10 * 60


def _pick(key: str, n: int) -> int:
    """A stable choice among *n* for *key*: the same on every run and machine."""
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % n


def _software(issuer: str) -> str:
    """The software *issuer* makes its documents with."""
    return ISSUER_SOFTWARE[_pick(issuer, len(ISSUER_SOFTWARE))]


def _stamp(path: Path, issuer: str, number: str, day: date) -> None:
    """Make *path* look made by *issuer* on *day*: with its own software, at a
    time in office hours that is the document's own, and never saved again."""
    minutes = _pick(number, WORKDAY_MINUTES)
    made = _sgt(day, WORKDAY_START_HOUR + minutes // 60, minutes % 60)
    _set_metadata(path, _software(issuer), made, made)


def write(folder: Path, trade: Trade) -> None:
    """Write a trade's invoice, order (unless none was issued) and shipping
    document into *folder*, each made by the company whose letterhead it is on."""
    invoice = trade.invoice
    path = folder / f"invoice_{invoice.number}.pdf"
    render_invoice(path, invoice)
    _stamp(path, invoice.seller, invoice.number, invoice.issued)
    if trade.ordered:
        order = trade.order
        path = folder / f"purchase_order_{order.number}.pdf"
        render_purchase_order(path, order)
        _stamp(path, order.buyer, order.number, order.issued)
    shipment = trade.shipment
    if isinstance(shipment, DeliveryOrderSheet):
        path = folder / f"delivery_order_{shipment.number}.pdf"
        render_delivery_order(path, shipment)
        _stamp(path, shipment.supplier, shipment.number, shipment.delivered)
    else:
        path = folder / f"bill_of_lading_{shipment.number}.pdf"
        render_bill_of_lading(path, shipment)
        _stamp(path, CARRIER, shipment.number, shipment.shipped)


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
