"""
tests/test_demo_trade.py
-----------------------
The paperwork behind a demo company's trade (gldb.demo_trade): for each sale
and each goods purchase on its bank statement, an invoice, a purchase order
and a bill of lading that agree with the payment to the cent, and the
suppliers' ACRA records. A case may also make one of them disagree. No AWS calls.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from gldb import demo_packs, demo_trade, extract_stub
from gldb.config import MATCH_WINDOW_DAYS
from gldb.intake import classify, intake, read_text
from gldb.schemas import AcraProfile, Transaction

ACMA = demo_packs.ACMA
SALES = {"EFG Company": Decimal("1670370.23"), "Metallica Inc.": Decimal("1497280.63")}
PURCHASES = {
    "ABC electronics Ltd.": Decimal("283932.40"), "SteelMen Pte Ltd.": Decimal("309372.82"),
    "Cool Robotics Ltd.": Decimal("120000.00"), "Caterpillarz Inc.": Decimal("184895.80"),
    "Tan & Co.": Decimal("730824.00"),
}


def _rows() -> list[Transaction]:
    return demo_packs._scaled_rows(ACMA, None)[1]


def _trades(faults: tuple[tuple[str, str], ...] = ()) -> list[demo_trade.Trade]:
    return demo_trade.plan(_seller(), _rows(), faults)


def _seller() -> demo_trade.Seller:
    return demo_packs.seller_of(ACMA)


def _by_partner(trades: list[demo_trade.Trade]) -> dict[str, demo_trade.Trade]:
    return {t.partner.name: t for t in trades}


# ---------------------------------------------------------------------------
# What is traded
# ---------------------------------------------------------------------------

def test_every_sale_and_goods_purchase_on_the_statement_has_a_trade_in_statement_order() -> None:
    trades = _trades()
    assert [t.partner.name for t in trades] == [
        "ABC electronics Ltd.", "EFG Company", "SteelMen Pte Ltd.", "Cool Robotics Ltd.", "Caterpillarz Inc.",
        "Metallica Inc.", "Tan & Co."]
    assert [t.purchase for t in trades] == [True, False, True, True, True, False, True]


def test_rent_utilities_salaries_and_the_bond_have_no_trade() -> None:
    described = " ".join(t.partner.name for t in _trades())
    for other in ("Sunny Properties", "SP group", "PUB", "Salaries", "Bond", "Abolo", "Chill Parties", "Best Stationery"):
        assert other not in described


@pytest.mark.parametrize("scale", ["1", "0.7", "1.4"])
def test_each_invoice_totals_exactly_the_payment_it_was_paid_by(scale: str) -> None:
    company = replace(ACMA, scale=Decimal(scale))
    rows = demo_packs._scaled_rows(company, None)[1]
    trades = demo_trade.plan(demo_packs.seller_of(company), rows, ())
    paid = {r.description.split(" - ")[1]: (r.debit if r.debit is not None else r.credit) for r in rows if " - " in r.description}
    assert len(trades) == 7
    for trade in trades:
        assert trade.invoice.total == paid[trade.partner.name]


def test_the_totals_on_the_statement_are_the_ones_the_trades_were_planned_from() -> None:
    totals = {t.partner.name: t.invoice.total for t in _trades()}
    assert totals == {**SALES, **PURCHASES}


def test_an_invoice_is_issued_before_its_payment_within_the_matching_window() -> None:
    rows = {r.description.split(" - ")[1]: r for r in _rows() if " - " in r.description}
    for trade in _trades():
        paid_on = rows[trade.partner.name].date
        assert trade.invoice.issued <= paid_on <= trade.invoice.issued + timedelta(days=MATCH_WINDOW_DAYS)
        assert trade.invoice.due == trade.invoice.issued + timedelta(days=30)


def test_the_order_comes_before_the_invoice_and_the_shipment_before_the_invoice() -> None:
    for trade in _trades():
        assert trade.order_date < trade.shipped_date < trade.invoice.issued


def test_the_seller_of_a_sale_is_the_company_and_the_buyer_of_a_purchase_is_the_company() -> None:
    for trade in _trades():
        company = (trade.invoice.buyer, trade.invoice.seller)[not trade.purchase]
        assert company == "ACMA LTD."
        assert trade.invoice.seller_uen == ("196500233E" if not trade.purchase else trade.partner.uen)


def test_every_number_in_a_pack_is_its_own() -> None:
    trades = _trades()
    for numbers in ([t.invoice.number for t in trades], [t.invoice.po_number for t in trades], [t.bl_number for t in trades]):
        assert len(set(numbers)) == len(numbers) == 7


def test_goods_ship_and_the_service_line_is_not_shipped() -> None:
    for trade in _trades():
        assert trade.shipped == ((trade.invoice.items[0].description, trade.invoice.items[0].quantity),)


# ---------------------------------------------------------------------------
# The suppliers
# ---------------------------------------------------------------------------

def test_every_supplier_has_an_acra_record_a_profile_can_be_read_from() -> None:
    suppliers = demo_trade.suppliers()
    assert sorted(s.name for s in suppliers) == sorted(PURCHASES)
    for supplier in suppliers:
        record = AcraProfile.model_validate({**demo_trade.acra_record(supplier), "doc_id": "ACRA"})
        assert record.company_name == supplier.legal_name and record.uen == supplier.uen
        assert record.status == "LIVE COMPANY" and record.officers and record.shareholders


def test_no_two_companies_share_a_uen_and_none_is_an_applicant() -> None:
    uens = [s.uen for s in demo_trade.suppliers()] + list({c.company.uen for c in demo_packs.CASES})
    assert len(set(uens)) == len(uens)


# ---------------------------------------------------------------------------
# The documents
# ---------------------------------------------------------------------------

def test_a_trade_is_written_as_an_invoice_an_order_and_a_bill_of_lading(tmp_path: Path) -> None:
    trade = _by_partner(_trades())["SteelMen Pte Ltd."]
    demo_trade.write(tmp_path, trade)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([
        f"invoice_{trade.invoice.number}.pdf", f"purchase_order_{trade.invoice.po_number}.pdf",
        f"bill_of_lading_{trade.bl_number}.pdf"])
    assert sorted(classify(read_text(p)) for p in tmp_path.iterdir()) == ["bill_of_lading", "invoice", "purchase_order"]


def test_what_is_written_reads_back_as_planned(tmp_path: Path) -> None:
    for trade in _trades():
        folder = tmp_path / trade.invoice.number
        folder.mkdir()
        demo_trade.write(folder, trade)
        paths = sorted(folder.iterdir())
        pack = extract_stub.parse_pack("t", intake("t", paths), paths, None)
        (invoice,), (order,), (bill,) = pack.invoices, pack.purchase_orders, pack.bills_of_lading
        assert (invoice.invoice_number, invoice.total, invoice.po_number) == (
            trade.invoice.number, trade.invoice.total, trade.invoice.po_number)
        assert (order.po_number, order.total, order.buyer_name, order.seller_name, order.seller_uen) == (
            trade.invoice.po_number, trade.invoice.total, trade.invoice.buyer, trade.invoice.seller, trade.invoice.seller_uen)
        assert (bill.bl_number, bill.invoice_reference, bill.shipper_name, bill.consignee_name) == (
            trade.bl_number, trade.invoice.number, trade.invoice.seller, trade.invoice.buyer)
        assert [(g.description, g.quantity) for g in bill.goods] == list(trade.shipped)


def test_nothing_it_writes_trips_a_forensic_check(tmp_path: Path) -> None:
    from gldb.forensics import hidden_text, incremental_updates, mixed_font_tokens

    for trade in _trades():
        demo_trade.write(tmp_path, trade)
    for path in tmp_path.iterdir():
        assert hidden_text(path) == [] and mixed_font_tokens(path) == [] and incremental_updates(path) == 0, path.name


def test_a_documents_dates_in_the_file_are_the_day_it_was_made(tmp_path: Path) -> None:
    import pypdf

    trade = _by_partner(_trades())["EFG Company"]
    demo_trade.write(tmp_path, trade)
    made = {p.name.split("_")[0]: pypdf.PdfReader(str(p)).metadata for p in tmp_path.iterdir()}
    assert made["invoice"]["/CreationDate"] == made["invoice"]["/ModDate"]
    assert made["invoice"]["/CreationDate"].startswith(f"D:{trade.invoice.issued:%Y%m%d}")
    assert made["purchase"]["/CreationDate"].startswith(f"D:{trade.order_date:%Y%m%d}")
    assert made["bill"]["/CreationDate"].startswith(f"D:{trade.shipped_date:%Y%m%d}")


# ---------------------------------------------------------------------------
# Making one disagree
# ---------------------------------------------------------------------------

def test_an_overbilled_invoice_is_dearer_than_its_order_and_its_payment() -> None:
    trade = _by_partner(_trades((("EFG Company", "overbilled"),)))["EFG Company"]
    assert trade.invoice.total == (SALES["EFG Company"] * Decimal("1.25")).quantize(Decimal("0.01"))
    assert trade.order_total == SALES["EFG Company"]
    assert trade.invoice.items[0].quantity == trade.shipped[0][1]  # the same goods, at a higher price


def test_a_short_shipped_invoice_bills_more_goods_than_the_bill_of_lading_shows() -> None:
    trade = _by_partner(_trades((("Metallica Inc.", "short_shipped"),)))["Metallica Inc."]
    assert trade.shipped[0][1] < trade.invoice.items[0].quantity
    assert trade.invoice.total == SALES["Metallica Inc."]  # it still matches its order and its payment


def test_an_invoice_with_no_order_names_one_that_was_never_issued(tmp_path: Path) -> None:
    trade = _by_partner(_trades((("Caterpillarz Inc.", "no_order"),)))["Caterpillarz Inc."]
    demo_trade.write(tmp_path, trade)
    assert not any(p.name.startswith("purchase_order") for p in tmp_path.iterdir())
    assert trade.invoice.po_number


def test_a_fault_names_a_trade_on_the_statement_and_a_fault_that_exists() -> None:
    with pytest.raises(ValueError, match="Nobody Ltd"):
        _trades((("Nobody Ltd", "overbilled"),))
    with pytest.raises(ValueError, match="mislaid"):
        _trades((("EFG Company", "mislaid"),))
