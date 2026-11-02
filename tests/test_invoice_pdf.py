"""
tests/test_invoice_pdf.py
-------------------------
The tax invoice the demo packs carry: a letterhead with the seller's logo, an
invoice details strip, bill-to block, item table, totals, payment details and
terms. It must read as an invoice, show every figure, add up (every line is
quantity times unit price, the subtotal is their sum, GST is 9% of the
subtotal and the total is the two together, less or plus a printed rounding of
at most a cent), and trip no forensic check by itself (no hidden or white
text, no font change inside a figure). No AWS calls.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber
import pytest

from gldb import extract_stub
from gldb.checks import check_inv_arith
from gldb.config import GST_RATE, money
from gldb.forensics import hidden_text, mixed_font_tokens
from gldb.intake import classify, intake, read_text
from gldb.invoice_pdf import MAX_ITEMS, MAX_ROUNDING, InvoiceSheet, brand_colour, items_for_total, render_invoice
from gldb.schemas import Invoice, LineItem
from tests.acma_records import pack


def _item(description: str, quantity: int, unit: str, amount: str) -> LineItem:
    return LineItem(description=description, quantity=quantity, unit_price=Decimal(unit), amount=Decimal(amount))


SHEET = InvoiceSheet(
    seller="ACMA LTD.", seller_uen="196500233E",
    seller_address=("19 Jurong Port Road, #25-550 Jurong Tower 2", "Singapore 619093"),
    seller_contact="Tel +65 6555 0142  |  billing@acma.example",
    buyer="EFG Company", buyer_address=("12 Tuas Avenue 8", "Singapore 639246"),
    number="INV-1001", issued=date(2026, 1, 5), due=date(2026, 2, 4), terms="Net 30 days", po_number="PO-2001",
    items=(_item("Air-handling unit AHU-450", 40, "34480.12", "1379204.80"),
           _item("Commissioning kit and spares", 1, "153244.95", "153244.95")),
    bank="First Citizens Bank, Tampines Branch", account_no="111-234-567-890",
)
LONG_ITEMS = tuple(_item(f"Spare part {n:02d}", n, "10.00", f"{n * 10}.00") for n in range(1, MAX_ITEMS + 1))


@pytest.fixture(scope="module")
def invoice(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("invoice") / "invoice_INV-1001.pdf"
    render_invoice(path, SHEET)
    return path


def test_it_is_one_page_and_reads_as_a_tax_invoice(invoice: Path) -> None:
    pages = read_text(invoice)
    assert len(pages) == 1 and classify(pages) == "invoice"


@pytest.mark.parametrize("shown", [
    "ACMA LTD.", "UEN: 196500233E", "19 Jurong Port Road, #25-550 Jurong Tower 2", "billing@acma.example",
    "BILL TO", "EFG Company", "12 Tuas Avenue 8", "Singapore 639246",
    "INVOICE NO", "INV-1001", "05 Jan 2026", "04 Feb 2026", "Net 30 days", "PO-2001",
    "Air-handling unit AHU-450", "1,379,204.80", "34,480.12", "Commissioning kit and spares", "153,244.95",
    "Subtotal", "1,532,449.75", "GST (9%)", "137,920.48", "1,670,370.23",
    "PAYMENT DETAILS", "First Citizens Bank, Tampines Branch", "111-234-567-890", "computer-generated",
])
def test_every_part_of_an_invoice_is_there(invoice: Path, shown: str) -> None:
    assert shown in read_text(invoice)[0]


def _filled_with(shapes: list[dict[str, Any]], colour: tuple[float, ...]) -> list[dict[str, Any]]:
    return [s for s in shapes if tuple(round(c, 3) for c in (s["non_stroking_color"] or ())) == colour]


def test_the_accent_bar_rule_and_logo_are_drawn_in_the_brand_colour(invoice: Path) -> None:
    colour = tuple(round(c, 3) for c in brand_colour(SHEET.seller))
    with pdfplumber.open(str(invoice)) as pdf:
        page = pdf.pages[0]
        assert len(_filled_with(page.rects, colour)) == 2  # the bar across the top and the rule under the letterhead
        assert len(_filled_with(page.curves, colour)) == 1  # the logo's tile


def test_a_company_keeps_its_colour_and_two_companies_differ() -> None:
    assert brand_colour("ACMA LTD.") == brand_colour("ACMA LTD.")
    assert brand_colour("ACMA LTD.") != brand_colour("Fernleaf Industries Pte. Ltd.")


def test_nothing_in_it_trips_a_forensic_check(invoice: Path) -> None:
    assert hidden_text(invoice) == [] and mixed_font_tokens(invoice) == []


def test_more_items_than_a_page_holds_are_refused(tmp_path: Path) -> None:
    items = tuple(_item(f"Spare part {n:02d}", 1, "10.00", "10.00") for n in range(MAX_ITEMS + 1))
    with pytest.raises(ValueError, match="do not fit one page"):
        render_invoice(tmp_path / "too_long.pdf", replace(SHEET, items=items))


def test_a_long_invoice_still_fits_its_page(tmp_path: Path) -> None:
    path = tmp_path / "long.pdf"
    render_invoice(path, replace(SHEET, items=LONG_ITEMS))
    pages = read_text(path)
    assert len(pages) == 1 and f"Spare part {MAX_ITEMS}" in pages[0] and f"{replace(SHEET, items=LONG_ITEMS).total:,.2f}" in pages[0]


def test_a_long_invoice_with_a_rounding_line_still_fits_its_page(tmp_path: Path) -> None:
    path = tmp_path / "long_rounded.pdf"
    render_invoice(path, replace(SHEET, items=LONG_ITEMS, rounding=Decimal("0.01")))
    pages = read_text(path)
    assert len(pages) == 1 and "Rounding" in pages[0] and "TERMS AND CONDITIONS" in pages[0]
    assert hidden_text(path) == [] and mixed_font_tokens(path) == []
    with pdfplumber.open(str(path)) as pdf:
        page = pdf.pages[0]
        last_terms_word = max((w for w in page.extract_words() if w["text"] == "full."), key=lambda w: w["bottom"])
        footer_rule = max(r["top"] for r in page.rects if r["height"] < 1)  # the lowest rule on the page
        assert last_terms_word["bottom"] < footer_rule  # the terms end above the footer


# ---------------------------------------------------------------------------
# Long names
# ---------------------------------------------------------------------------

#: The longest names of the demo companies and suppliers
LONG_NAMES = ["BRIGHTWAVE COMPONENTS PTE. LTD.", "CORVID MARINE ENGINEERING PTE. LTD.", "OAKRIDGE FABRICATION PTE. LTD.",
              "ABC ELECTRONICS LTD.", "DUSKFIELD TRADING PTE. LTD."]


@pytest.mark.parametrize("name", LONG_NAMES)
def test_a_long_company_name_shares_the_header_with_the_title_without_touching_it(tmp_path: Path, name: str) -> None:
    path = tmp_path / "long_name.pdf"
    render_invoice(path, replace(SHEET, seller=name))
    pages = read_text(path)
    assert classify(pages) == "invoice" and pages[0].splitlines()[0] == f"{name} TAX INVOICE"
    with pdfplumber.open(str(path)) as pdf:
        header = sorted((w for w in pdf.pages[0].extract_words() if w["top"] < 45), key=lambda w: w["x0"])
    assert all(left["x1"] <= right["x0"] for left, right in zip(header, header[1:])), "words in the header overlap"
    assert header[-1]["x1"] <= 553.5  # inside the right margin


def test_a_short_name_keeps_the_full_size_and_a_long_one_is_set_smaller(tmp_path: Path) -> None:
    sizes = {}
    for name in ("ACMA LTD.", "CORVID MARINE ENGINEERING PTE. LTD."):
        path = tmp_path / f"{len(name)}.pdf"
        render_invoice(path, replace(SHEET, seller=name))
        with pdfplumber.open(str(path)) as pdf:
            sizes[name] = max(c["size"] for c in pdf.pages[0].chars if c["top"] < 45 and c["text"] not in "TAXINVOICE ")
    assert sizes["ACMA LTD."] == 16 and 11 <= sizes["CORVID MARINE ENGINEERING PTE. LTD."] < 16


def test_a_name_too_long_for_the_letterhead_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="too long"):
        render_invoice(tmp_path / "x.pdf", replace(SHEET, seller="THE VERY LONG NAMED INTERNATIONAL TRADING AND ENGINEERING COMPANY PTE. LTD."))


# ---------------------------------------------------------------------------
# The numbers add up
# ---------------------------------------------------------------------------

_MONEY = r"(-?[\d,]+\.\d{2})"
_ROW = re.compile(rf"^(\d+) (.+) (\d+) {_MONEY} {_MONEY}$")


def _figure(text: str, label: str) -> Decimal:
    (found,) = re.findall(rf"^{re.escape(label)} {_MONEY}$", text, re.MULTILINE)
    return Decimal(found.replace(",", ""))


def _assert_adds_up(path: Path) -> None:
    """Add up what the page itself shows, at the GST rate it prints."""
    text = read_text(path)[0]
    rows = [m.groups() for line in text.splitlines() if (m := _ROW.match(line))]
    assert rows
    amounts = []
    for _, description, quantity, unit, amount in rows:
        unit_price, line_amount = (Decimal(v.replace(",", "")) for v in (unit, amount))
        assert money(int(quantity) * unit_price) == line_amount, description
        amounts.append(line_amount)
    (rate,) = re.findall(r"^GST \((\d+)%\) ", text, re.MULTILINE)
    subtotal, gst, total = _figure(text, "Subtotal"), _figure(text, f"GST ({rate}%)"), _figure(text, "TOTAL (SGD)")
    rounding = _figure(text, "Rounding") if "\nRounding " in text else Decimal(0)
    assert sum(amounts) == subtotal
    assert money(subtotal * Decimal(rate) / 100) == gst
    assert abs(rounding) <= MAX_ROUNDING
    assert subtotal + gst + rounding == total


def test_the_subtotal_gst_and_total_follow_from_the_items() -> None:
    assert (SHEET.subtotal, SHEET.gst, SHEET.total) == (Decimal("1532449.75"), Decimal("137920.48"), Decimal("1670370.23"))
    assert replace(SHEET, items=LONG_ITEMS).subtotal == Decimal(10 * MAX_ITEMS * (MAX_ITEMS + 1) // 2)  # 10 + 20 + ... + 10 * MAX_ITEMS


def test_a_line_that_is_not_quantity_times_unit_price_cannot_be_issued() -> None:
    with pytest.raises(ValueError, match="Spare part"):
        replace(SHEET, items=(_item("Spare part", 3, "10.00", "31.00"),))


@pytest.mark.parametrize("sheet", [
    SHEET, replace(SHEET, items=LONG_ITEMS), replace(SHEET, items=LONG_ITEMS, rounding=Decimal("-0.01")),
    replace(SHEET, **dict(zip(("items", "rounding"), items_for_total(Decimal("1497280.63"), ("Pumps", "Spares"), 25)))),
    replace(SHEET, **dict(zip(("items", "rounding"), items_for_total(Decimal("309372.82"), ("Steel sheets", "Cutting"), 120)))),
])
def test_the_figures_printed_on_the_page_add_up(tmp_path: Path, sheet: InvoiceSheet) -> None:
    path = tmp_path / "invoice.pdf"
    render_invoice(path, sheet)
    _assert_adds_up(path)


def test_the_rounding_line_is_printed_only_when_there_is_rounding(tmp_path: Path) -> None:
    for rounding, shown in ((Decimal(0), False), (Decimal("0.01"), True), (Decimal("-0.01"), True)):
        path = tmp_path / f"r{rounding}.pdf"
        render_invoice(path, replace(SHEET, rounding=rounding))
        assert ("Rounding" in read_text(path)[0]) is shown
    assert replace(SHEET, rounding=Decimal("0.01")).total == Decimal("1670370.24")


def test_more_than_a_cent_of_rounding_cannot_be_issued() -> None:
    with pytest.raises(ValueError, match="rounding"):
        replace(SHEET, rounding=MAX_ROUNDING + Decimal("0.01"))


@pytest.mark.parametrize("total", ["1670370.23", "1497280.63", "1000000.00", "1000000.01", "250.00", "309372.82", "1000000.09"])
def test_items_can_be_made_to_total_exactly(total: str) -> None:
    items, rounding = items_for_total(Decimal(total), ("Air-handling unit AHU-450", "Commissioning kit and spares"), 40)
    sheet = replace(SHEET, items=items, rounding=rounding)
    assert sheet.total == Decimal(total) and sheet.gst == money(sheet.subtotal * GST_RATE)
    assert [i.description for i in items] == ["Air-handling unit AHU-450", "Commissioning kit and spares"]
    assert items[0].quantity == 40 and items[1].quantity == 1


def test_a_total_that_gst_alone_cannot_reach_takes_a_one_cent_rounding() -> None:
    """GST rounded to the cent skips about one total in twelve: SGD 1,000,000.09 is one."""
    assert items_for_total(Decimal("1000000.09"), ("Pumps", "Spares"), 40)[1] in (MAX_ROUNDING, -MAX_ROUNDING)
    assert items_for_total(Decimal("1000000.00"), ("Pumps", "Spares"), 40)[1] == 0


def test_every_total_is_met_and_rounding_is_used_only_where_gst_cannot_reach() -> None:
    rounded = 0
    for cents in range(100_000_000, 100_000_300):
        total = Decimal(cents) / 100
        items, rounding = items_for_total(total, ("Pumps", "Spares"), 40)
        assert replace(SHEET, items=items, rounding=rounding).total == total
        if rounding:
            rounded += 1
            wide = (Decimal(c) / 100 for c in range(cents * 100 // 109 - 500, cents * 100 // 109 + 500))
            assert not any(s + money(s * GST_RATE) == total for s in wide), total  # no subtotal reaches it without rounding
    assert 0 < rounded < 40  # about one in twelve


# ---------------------------------------------------------------------------
# Reading it back: what the pipeline makes of the page is what was issued
# ---------------------------------------------------------------------------

def _read(path: Path) -> Invoice:
    docs = intake("t", [path])
    (invoice,) = extract_stub.parse_pack("t", docs, [path], None).invoices
    return invoice


ISSUED = [
    pytest.param(SHEET, id="two items"),
    pytest.param(replace(SHEET, items=LONG_ITEMS), id="a full page"),
    pytest.param(replace(SHEET, rounding=Decimal("-0.01"), po_number="PO-4107", terms="Net 60 days"), id="rounded down, 60 days"),
    pytest.param(replace(SHEET, seller="Tan & Co.", buyer="ACMA LTD.", number="TC-2026-0087"), id="a supplier's, to the applicant"),
]


@pytest.mark.parametrize("sheet", ISSUED)
def test_a_rendered_invoice_reads_back_as_it_was_issued(tmp_path: Path, sheet: InvoiceSheet) -> None:
    path = tmp_path / f"invoice_{sheet.number}.pdf"
    render_invoice(path, sheet)
    read = _read(path)
    assert (read.invoice_number, read.invoice_date, read.po_number) == (sheet.number, sheet.issued, sheet.po_number)
    assert (read.seller_name, read.seller_uen, read.buyer_name) == (sheet.seller, sheet.seller_uen, sheet.buyer)
    assert [(i.description, i.quantity, i.unit_price, i.amount) for i in read.line_items] == [
        (i.description, i.quantity, i.unit_price, i.amount) for i in sheet.items]
    assert (read.subtotal, read.gst, read.total) == (sheet.subtotal, sheet.gst, sheet.total)
    assert (read.doc_id, read.page) == (path.stem, 1)


@pytest.mark.parametrize("sheet", ISSUED)
def test_the_pipelines_arithmetic_check_finds_nothing_on_what_it_read(tmp_path: Path, sheet: InvoiceSheet) -> None:
    path = tmp_path / "invoice.pdf"
    render_invoice(path, sheet)
    assert check_inv_arith(pack(invoices=[_read(path)])) == []


def test_the_figures_it_quotes_are_lines_of_the_page(tmp_path: Path) -> None:
    path = tmp_path / "invoice.pdf"
    render_invoice(path, SHEET)
    quotes = _read(path).source_quotes
    assert quotes == {"Subtotal": "Subtotal 1,532,449.75", "GST": "GST (9%) 137,920.48", "Total": "TOTAL (SGD) 1,670,370.23"}
    assert all(q in read_text(path)[0] for q in quotes.values())


# ---------------------------------------------------------------------------
# Zero-rated: goods exported from Singapore carry GST at 0%
# ---------------------------------------------------------------------------

EXPORT = replace(SHEET, buyer="Perdana Klang Sdn. Bhd.", gst_rate=Decimal(0),
                 buyer_address=("Lot 7, Jalan Kemajuan 2", "42000 Port Klang, Selangor, Malaysia"))


def test_an_export_invoice_charges_no_gst_and_says_why(tmp_path: Path) -> None:
    path = tmp_path / "export.pdf"
    render_invoice(path, EXPORT)
    text = read_text(path)[0]
    assert "GST (0%) 0.00" in text and "Zero-rated supply: goods exported from Singapore" in text
    assert EXPORT.total == EXPORT.subtotal == Decimal("1532449.75")
    _assert_adds_up(path)


def test_an_export_invoice_reads_back_zero_rated_and_adds_up_to_the_check(tmp_path: Path) -> None:
    path = tmp_path / "export.pdf"
    render_invoice(path, EXPORT)
    read = _read(path)
    assert (read.gst_rate, read.gst, read.total) == (0, 0, EXPORT.total)
    assert read.source_quotes["GST"] == "GST (0%) 0.00"
    assert check_inv_arith(pack(invoices=[read])) == []


def test_a_standard_rated_invoice_reads_back_at_nine_percent(tmp_path: Path) -> None:
    assert _read(_issued(tmp_path)).gst_rate == GST_RATE


@pytest.mark.parametrize("total", ["1670370.23", "1000000.09", "250.00"])
def test_items_can_be_made_to_total_exactly_with_no_gst(total: str) -> None:
    items, rounding = items_for_total(Decimal(total), ("Pumps", "Spares"), 40, gst_rate=Decimal(0))
    sheet = replace(SHEET, items=items, rounding=rounding, gst_rate=Decimal(0))
    assert rounding == 0 and sheet.gst == 0 and sheet.total == sheet.subtotal == Decimal(total)


def test_a_table_row_it_cannot_read_is_refused_not_skipped(tmp_path: Path) -> None:
    text = "\n".join(read_text(_issued(tmp_path))[0].splitlines()).replace("34,480.12", "thirty-four thousand")
    with pytest.raises(ValueError, match="cannot parse table row"):
        extract_stub._parse_invoice("i", [text])


def test_an_invoice_without_its_details_strip_is_refused(tmp_path: Path) -> None:
    text = read_text(_issued(tmp_path))[0].replace("INVOICE NO", "NUMBER")
    with pytest.raises(ValueError, match="INVOICE NO"):
        extract_stub._parse_invoice("i", [text])


def _issued(tmp_path: Path) -> Path:
    path = tmp_path / "invoice.pdf"
    render_invoice(path, SHEET)
    return path
