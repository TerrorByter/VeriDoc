"""
gldb/extract_stub.py
--------------------
Deterministic parser for the GLDB synthetic pack layout produced by
gldb/data_factory.py.

Rendering contract (must match data_factory.py exactly):
- A4 page, Helvetica 10pt, 14pt line spacing, top-to-bottom.
- Label lines: "Label: value"  (money as "SGD 1,234.56")
- Table rows: cells separated by " | "  (money no SGD prefix; empty cell = "-")
- First line of every document is its title.

An invoice may instead be laid out as gldb/invoice_pdf.py prints it (a
letterhead, a details strip, a bill-to block, a numbered item table and a totals
box), and a purchase order or bill of lading as gldb/trade_pdf.py prints it;
each parser reads either layout.

This module also handles FATCA/CRS AcroForms via gldb/form_maps.py.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pypdf

from gldb.config import TEMPLATES_DIR, money
from gldb.form_maps import (
    BANK_STATEMENT_FIELD_MAP,
    BANK_STATEMENT_ROW_COLUMNS,
    BANK_STATEMENT_ROW_FIELD,
    ENTITY_FIELD_MAP,
    INDIVIDUAL_FIELD_MAP,
)
from gldb.intake import parse_pdf_date, read_text
from gldb.workbook import Sheet, cell_text, is_workbook, read_workbook
from gldb.schemas import (
    AcraFinancials,
    AcraOfficer,
    AcraProfile,
    AcraShareholder,
    Address,
    ApplicationManifest,
    BankStatement,
    BillOfLading,
    ControllingPerson,
    DocMeta,
    EntitySelfCert,
    FinancialStatementSummary,
    IndividualSelfCert,
    Invoice,
    LineItem,
    NoticeOfAssessment,
    PurchaseOrder,
    ShippedGoods,
    Signatory,
    TaxResidencyRow,
    Transaction,
)


# ---------------------------------------------------------------------------
# Money parsing helper
# ---------------------------------------------------------------------------

def _parse_money(s: str) -> Decimal:
    """Parse a money string like "1,234.56" or "SGD 1,234.56" → Decimal."""
    s = s.strip().lstrip("SGD").replace(",", "").strip()
    return money(s)


def _parse_date(s: str) -> date:
    return date.fromisoformat(s.strip())


#: OCR (Textract) reads the " | " cell divider as " I ".
_OCR_DIVIDER = re.compile(r" [|I] ")


def _cells(line: str, n: int) -> list[str] | None:
    """Split a table row into exactly *n* cells: on " | ", else on the OCR
    divider. None when neither gives *n* cells (for example a description
    containing " I " in OCR text), so the caller can refuse the row."""
    for parts in (line.split(" | "), _OCR_DIVIDER.split(line)):
        if len(parts) == n:
            return [p.strip() for p in parts]
    return None


def _row_error(doc_id: str, line: str) -> ValueError:
    """A table row we cannot read. Raised rather than skipped: a silently
    dropped row would let balance and arithmetic checks pass on missing data."""
    return ValueError(f"{doc_id}: cannot parse table row {line!r}")


def _parse_line_items(doc_id: str, lines: list[str], stop_prefix: str) -> list[LineItem]:
    """Rows of the "Description | Qty | Unit Price | Amount" table, up to the
    line starting with *stop_prefix*."""
    items: list[LineItem] = []
    in_table = False
    for l in lines:
        if not in_table:
            in_table = (_cells(l, 4) or [])[:2] == ["Description", "Qty"]
            continue
        if l.startswith(stop_prefix):
            break
        parts = _cells(l, 4)
        try:
            if parts is None:
                raise ValueError
            desc, qty_s, up_s, amt_s = parts
            items.append(LineItem(
                description=desc,
                quantity=int(qty_s),
                unit_price=_parse_money(up_s),
                amount=_parse_money(amt_s),
            ))
        except (ValueError, ArithmeticError):
            raise _row_error(doc_id, l) from None
    return items


# ---------------------------------------------------------------------------
# Invoice parser
# ---------------------------------------------------------------------------

_STYLED_DAY = "%d %b %Y"
_AMOUNT = r"(-?[\d,]+\.\d{2})"
_STYLED_DATE = r"(\d{2} \w{3} \d{4})"
#: invoice_pdf's details strip: number, issued, due, terms, purchase order
_STYLED_STRIP = re.compile(rf"^(\S+) {_STYLED_DATE} {_STYLED_DATE} (.+) (\S+)$")
#: trade_pdf's purchase order strip: number, ordered, deliver by, terms, currency
_ORDER_STRIP = re.compile(rf"^(\S+) {_STYLED_DATE} {_STYLED_DATE} (.+) SGD$")
#: trade_pdf's bill of lading strip: number, shipped, voyage, invoice, freight
_BILL_STRIP = re.compile(rf"^(\S+) {_STYLED_DATE} (\S+) (\S+) (\S+)$")
_STYLED_ROW = re.compile(rf"^(\d+) (.+) (\d+) {_AMOUNT} {_AMOUNT}$")
_CARGO_ROW = re.compile(r"^(\d+) (.+) (\d+) (\d+) ([A-Z]+) ([\d,]+\.\d) ([\d,]+\.\d{2})$")


def _lines(pages: list[str]) -> list[str]:
    return [line for line in (pages[0] if pages else "").splitlines() if line.strip()]


def _styled_day(text: str) -> date:
    return datetime.strptime(text, _STYLED_DAY).date()


def _after(doc_id: str, lines: list[str], label: str, what: str) -> str:
    """The line under the first line starting with *label*: a value under its label, or a block's name."""
    found = next((n for n, line in enumerate(lines) if line.startswith(label)), None)
    if found is None or found + 1 == len(lines):
        raise ValueError(f"{doc_id}: no '{label}' on the {what}")
    return lines[found + 1]


def _strip(doc_id: str, lines: list[str], label: str, pattern: re.Pattern[str], what: str) -> tuple[str, ...]:
    """The values of the details strip that *label* starts."""
    found = pattern.match(_after(doc_id, lines, label, what))
    if found is None:
        raise ValueError(f"{doc_id}: the {what}'s details are not in the layout it is printed in")
    return found.groups()


def _styled_figure(doc_id: str, lines: list[str], label: str, what: str) -> tuple[Decimal, str]:
    """A figure of the totals box, and its line as a quote."""
    line = next((line for line in lines if re.fullmatch(rf"{re.escape(label)} {_AMOUNT}", line)), None)
    if line is None:
        raise ValueError(f"{doc_id}: no '{label}' figure on the {what}")
    return _parse_money(line.rsplit(" ", 1)[1]), line


def _styled_items(doc_id: str, lines: list[str], what: str) -> list[LineItem]:
    """The numbered rows of the item table invoice_pdf prints, up to the subtotal."""
    table = next((n for n, line in enumerate(lines) if line.startswith("NO. DESCRIPTION")), None)
    if table is None:
        raise ValueError(f"{doc_id}: no item table on the {what}")
    items: list[LineItem] = []
    for line in lines[table + 1:]:
        if line.startswith("Subtotal"):
            break
        row = _STYLED_ROW.match(line)
        if row is None:
            raise _row_error(doc_id, line)
        _, description, quantity, unit_price, amount = row.groups()
        items.append(LineItem(description=description, quantity=int(quantity),
                              unit_price=_parse_money(unit_price), amount=_parse_money(amount)))
    return items


def _parse_invoice(doc_id: str, pages: list[str]) -> Invoice:
    lines = _lines(pages)
    if any(line.startswith("BILL TO") for line in lines):
        return _parse_styled_invoice(doc_id, lines)
    return _parse_plain_invoice(doc_id, lines)


def _parse_styled_invoice(doc_id: str, lines: list[str]) -> Invoice:
    """An invoice as gldb/invoice_pdf.py prints it. Each text line holds one
    thing, so the seller is the letterhead's first line, the details are the
    line under their labels and the table is the numbered rows."""
    what = "invoice"
    number, issued, _due, _terms, po_number = _strip(doc_id, lines, "INVOICE NO", _STYLED_STRIP, what)
    uen = re.search(r"UEN: (\w+)", "\n".join(lines))
    if uen is None:
        raise ValueError(f"{doc_id}: the invoice's details are not in the layout it is printed in")
    items = _styled_items(doc_id, lines, what)
    (subtotal, subtotal_line), (gst, gst_line), (total, total_line) = (
        _styled_figure(doc_id, lines, label, what) for label in ("Subtotal", "GST (9%)", "TOTAL (SGD)"))
    return Invoice(
        doc_id=doc_id, invoice_number=number, invoice_date=_styled_day(issued),
        seller_name=lines[0].removesuffix(" TAX INVOICE"), seller_uen=uen.group(1),
        buyer_name=_after(doc_id, lines, "BILL TO", what),
        po_number=po_number, line_items=items, subtotal=subtotal, gst=gst, total=total, page=1,
        source_quotes={"Subtotal": subtotal_line, "GST (9%)": gst_line, "Total": total_line},
    )


def _parse_plain_invoice(doc_id: str, lines: list[str]) -> Invoice:
    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    seller = _get("Seller")
    seller_uen = _get("Seller UEN")
    buyer = _get("Buyer")
    number = _get("Invoice No")
    inv_date = _parse_date(_get("Invoice Date"))

    line_items = _parse_line_items(doc_id, lines, stop_prefix="Subtotal:")
    source_quotes: dict[str, str] = {}

    subtotal = _parse_money(_get("Subtotal"))
    gst = _parse_money(_get("GST (9%)"))
    total = _parse_money(_get("Total"))

    # Source quotes
    for label in ("Subtotal", "GST (9%)", "Total"):
        for l in lines:
            if l.startswith(f"{label}:"):
                source_quotes[label] = l.strip()

    return Invoice(
        doc_id=doc_id,
        invoice_number=number,
        invoice_date=inv_date,
        seller_name=seller,
        seller_uen=seller_uen,
        buyer_name=buyer,
        line_items=line_items,
        subtotal=subtotal,
        gst=gst,
        total=total,
        page=1,
        source_quotes=source_quotes,
    )


# ---------------------------------------------------------------------------
# Purchase order parser
# ---------------------------------------------------------------------------

def _parse_purchase_order(doc_id: str, pages: list[str]) -> PurchaseOrder:
    lines = _lines(pages)
    if "VENDOR" in lines:
        return _parse_styled_purchase_order(doc_id, lines)

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    total_label = "Total (incl. GST)"
    return PurchaseOrder(
        doc_id=doc_id,
        po_number=_get("PO No"),
        po_date=_parse_date(_get("PO Date")),
        buyer_name=_get("Buyer"),
        seller_name=_get("Seller"),
        seller_uen=_get("Seller UEN"),
        line_items=_parse_line_items(doc_id, lines, stop_prefix=f"{total_label}:"),
        total=_parse_money(_get(total_label)),
        page=1,
        source_quotes={
            "Total": l.strip() for l in lines if l.startswith(f"{total_label}:")
        },
    )


def _parse_styled_purchase_order(doc_id: str, lines: list[str]) -> PurchaseOrder:
    """A purchase order as gldb/trade_pdf.py prints it: the buyer's letterhead,
    the details strip, the vendor block (name, then its UEN) and the item table."""
    what = "purchase order"
    number, ordered, _deliver_by, _terms = _strip(doc_id, lines, "PO NUMBER", _ORDER_STRIP, what)
    vendor = lines.index("VENDOR")
    uen = re.fullmatch(r"UEN: (\w+)", lines[vendor + 2]) if vendor + 2 < len(lines) else None
    if uen is None:
        raise ValueError(f"{doc_id}: the vendor's UEN is not under its name")
    total, total_line = _styled_figure(doc_id, lines, "TOTAL (SGD)", what)
    return PurchaseOrder(
        doc_id=doc_id, po_number=number, po_date=_styled_day(ordered),
        buyer_name=lines[0].removesuffix(" PURCHASE ORDER"), seller_name=lines[vendor + 1], seller_uen=uen.group(1),
        line_items=_styled_items(doc_id, lines, what), total=total, page=1, source_quotes={"Total": total_line},
    )


# ---------------------------------------------------------------------------
# Bill of lading parser
# ---------------------------------------------------------------------------

def _parse_bill_of_lading(doc_id: str, pages: list[str]) -> BillOfLading:
    lines = _lines(pages)
    if "SHIPPER" in lines:
        return _parse_styled_bill_of_lading(doc_id, lines)

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    goods: list[ShippedGoods] = []
    in_table = False
    for l in lines:
        if not in_table:
            in_table = (_cells(l, 2) or []) == ["Description", "Quantity"]
            continue
        parts = _cells(l, 2)
        try:
            if parts is None:
                raise ValueError
            goods.append(ShippedGoods(description=parts[0], quantity=int(parts[1])))
        except ValueError:
            raise _row_error(doc_id, l) from None

    return BillOfLading(
        doc_id=doc_id,
        bl_number=_get("B/L No"),
        shipped_date=_parse_date(_get("Shipped on Board")),
        shipper_name=_get("Shipper"),
        consignee_name=_get("Consignee"),
        vessel=_get("Vessel"),
        port_of_loading=_get("Port of Loading"),
        port_of_discharge=_get("Port of Discharge"),
        invoice_reference=_get("Invoice Ref"),
        goods=goods,
        page=1,
        source_quotes={
            f"Quantity {g.description}": f"{g.description} | {g.quantity}" for g in goods
        },
    )


def _parse_styled_bill_of_lading(doc_id: str, lines: list[str]) -> BillOfLading:
    """A bill of lading as gldb/trade_pdf.py prints it: the carrier's
    letterhead, the details strip, the party blocks, the vessel and port rows
    and the cargo table up to its totals row."""
    what = "bill of lading"
    number, shipped, _voyage, invoice_ref, _freight = _strip(doc_id, lines, "B/L NO", _BILL_STRIP, what)

    def row(label: str) -> str:
        found = next((line for line in lines if line.startswith(f"{label} ")), None)
        if found is None:
            raise ValueError(f"{doc_id}: no '{label}' on the {what}")
        return found.removeprefix(f"{label} ")

    table = next((n for n, line in enumerate(lines) if line.startswith("NO. DESCRIPTION OF GOODS")), None)
    if table is None:
        raise ValueError(f"{doc_id}: no cargo table on the {what}")
    goods: list[ShippedGoods] = []
    quotes: dict[str, str] = {}
    for line in lines[table + 1:]:
        if line.startswith("TOTAL "):
            break
        cargo = _CARGO_ROW.match(line)
        if cargo is None:
            raise _row_error(doc_id, line)
        description, quantity = cargo.group(2), int(cargo.group(3))
        goods.append(ShippedGoods(description=description, quantity=quantity))
        quotes[f"Quantity {description}"] = line
    return BillOfLading(
        doc_id=doc_id, bl_number=number, shipped_date=_styled_day(shipped),
        shipper_name=_after(doc_id, lines, "SHIPPER", what), consignee_name=_after(doc_id, lines, "CONSIGNEE", what),
        vessel=row("VESSEL"), port_of_loading=row("PORT OF LOADING"), port_of_discharge=row("PORT OF DISCHARGE"),
        invoice_reference=invoice_ref, goods=goods, page=1, source_quotes=quotes,
    )


# ---------------------------------------------------------------------------
# Bank statement parser
# ---------------------------------------------------------------------------

def _parse_bank_statement(doc_id: str, pages: list[str]) -> BankStatement:
    text = pages[0] if pages else ""
    lines = [l for l in text.splitlines() if l.strip()]

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    # Period: "YYYY-MM-DD to YYYY-MM-DD"
    period_raw = _get("Period")
    period_parts = period_raw.split(" to ")
    period_start = _parse_date(period_parts[0])
    period_end = _parse_date(period_parts[1])

    bank = _get("Bank")
    holder = _get("Account Holder")
    opening = _parse_money(_get("Opening Balance"))
    closing = _parse_money(_get("Closing Balance"))

    transactions: list[Transaction] = []
    source_quotes: dict[str, str] = {
        "Opening Balance": next(line.strip() for line in lines if line.startswith("Opening Balance:")),
    }
    in_table = False
    for l in lines:
        if not in_table:
            in_table = (_cells(l, 5) or [])[:2] == ["Date", "Description"]
            continue
        if l.startswith("Closing Balance:"):
            source_quotes["Closing Balance"] = l.strip()
            break
        parts = _cells(l, 5)
        try:
            if parts is None:
                raise ValueError
            d_s, desc, debit_s, credit_s, bal_s = parts
            t = Transaction(
                date=_parse_date(d_s),
                description=desc,
                debit=_parse_money(debit_s) if debit_s != "-" else None,
                credit=_parse_money(credit_s) if credit_s != "-" else None,
                balance=_parse_money(bal_s),
            )
        except (ValueError, ArithmeticError):
            raise _row_error(doc_id, l) from None
        transactions.append(t)
        source_quotes[desc] = l.strip()

    return BankStatement(
        doc_id=doc_id,
        bank_name=bank,
        account_holder=holder,
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening,
        closing_balance=closing,
        transactions=transactions,
        page=1,
        source_quotes=source_quotes,
    )


# ---------------------------------------------------------------------------
# Bank statement AcroForm parser
# ---------------------------------------------------------------------------

_STATEMENT_ROW = re.compile(r"F02(\d{3})[a-e]")
#: A fillable statement has its header fields and a first row
_STATEMENT_FIELDS = {*BANK_STATEMENT_FIELD_MAP.values(),
                     BANK_STATEMENT_ROW_FIELD.format(n=1, column=BANK_STATEMENT_ROW_COLUMNS["date"])}


def _is_statement_form(fields: dict[str, Any]) -> bool:
    return _STATEMENT_FIELDS <= fields.keys()


def is_bank_statement_form(path: Path) -> bool:
    """Whether *path* is a fillable statement of account: its figures are
    form field values, not page text."""
    return _is_statement_form(pypdf.PdfReader(str(path)).get_fields() or {})


def _parse_dmy(s: str) -> date:
    return datetime.strptime(s.strip(), "%d/%m/%Y").date()


def parse_bank_statement_form(doc_id: str, path: Path) -> BankStatement:
    """Parse a fillable statement of account from its form fields with pypdf.

    Rows run to the first one with no date and no amount (the end-of-
    transactions marker). A row we cannot read, or any content after the
    marker, is refused rather than dropped. The form carries its bank only as
    a logo image, so bank_name stays empty.
    """
    fields = pypdf.PdfReader(str(path)).get_fields() or {}
    if not _is_statement_form(fields):
        raise ValueError(f"{doc_id}: not a bank statement form")
    raw = {name: value.strip() for name, value in _field_values(fields).items()}
    header = {name: raw.get(field, "") for name, field in BANK_STATEMENT_FIELD_MAP.items()}
    try:
        period_start, period_end = (_parse_dmy(d) for d in header["period"].split(" to "))
        opening, closing = _parse_money(header["opening_balance"]), _parse_money(header["closing_balance"])
    except (ValueError, ArithmeticError):
        raise ValueError(f"{doc_id}: cannot parse the statement header {header!r}") from None

    transactions: list[Transaction] = []
    ended = False
    for n in sorted({int(m[1]) for name in fields if (m := _STATEMENT_ROW.fullmatch(name))}):
        cells = {column: raw.get(BANK_STATEMENT_ROW_FIELD.format(n=n, column=letter), "")
                 for column, letter in BANK_STATEMENT_ROW_COLUMNS.items()}
        if ended:
            if any(cells.values()):
                raise _row_error(doc_id, f"row {n}")
            continue
        if not (cells["date"] or cells["credit"] or cells["debit"]):
            ended = True
            continue
        try:
            transactions.append(Transaction(
                date=_parse_dmy(cells["date"]),
                description=cells["description"],
                credit=_parse_money(cells["credit"]) if cells["credit"] else None,
                debit=_parse_money(cells["debit"]) if cells["debit"] else None,
                balance=_parse_money(cells["balance"]),
            ))
        except (ValueError, ArithmeticError):
            raise _row_error(doc_id, f"row {n}") from None

    return BankStatement(
        doc_id=doc_id, bank_name=None, account_holder=header["account_holder"], period_start=period_start,
        period_end=period_end, opening_balance=opening, closing_balance=closing, transactions=transactions,
        source_quotes={"Opening Balance": header["opening_balance"], "Closing Balance": header["closing_balance"]},
    )


# ---------------------------------------------------------------------------
# Financial statements parser
# ---------------------------------------------------------------------------

def _parse_fs_summary(doc_id: str, pages: list[str]) -> FinancialStatementSummary:
    text = pages[0] if pages else ""
    lines = [l for l in text.splitlines() if l.strip()]

    def _get(label: str) -> str:
        for l in lines:
            if l.startswith(f"{label}:"):
                return l.split(":", 1)[1].strip()
        return ""

    return FinancialStatementSummary(
        doc_id=doc_id,
        company_name=_get("Company"),
        uen=_get("UEN"),
        financial_year_end=_parse_date(_get("Financial Year End")),
        revenue=_parse_money(_get("Revenue")),
        total_assets=_parse_money(_get("Total Assets")),
        total_liabilities=_parse_money(_get("Total Liabilities")),
        total_equity=_parse_money(_get("Total Equity")),
        page=1,
    )


# ---------------------------------------------------------------------------
# Financial statements workbook parser
# ---------------------------------------------------------------------------

#: FinancialStatementSummary field -> (sheet, row label) of the line it is read from
_WORKBOOK_FIGURES = {
    "revenue": ("income statement", "Total Revenue"),
    "ebit": ("income statement", "Operating Income"),
    "profit_before_tax": ("income statement", "EBT Incl. Unusual Items"),
    "profit_after_tax": ("income statement", "Earnings from Cont. Ops."),
    "cash": ("balance sheet", "Cash And Equivalents"),
    "total_current_assets": ("balance sheet", "Total Current Assets"),
    "total_assets": ("balance sheet", "Total Assets"),
    "total_current_liabilities": ("balance sheet", "Total Current Liabilities"),
    "total_liabilities": ("balance sheet", "Total Liabilities"),
    "retained_earnings": ("balance sheet", "Retained Earnings"),
    "total_equity": ("balance sheet", "Total Equity"),
    "operating_cashflow": ("cashflow statement", "Cash from Ops."),
    "investing_cashflow": ("cashflow statement", "Cash from Investing"),
    "financing_cashflow": ("cashflow statement", "Cash from Financing"),
}
_WORKBOOK_REQUIRED = ("revenue", "total_assets", "total_liabilities", "total_equity")
#: The balance sheet lines that add to borrowings
_WORKBOOK_DEBT = ("Short-term Borrowings", "Curr. Port. of LT Debt", "Long-Term Debt")
_WORKBOOK_UNIT = re.compile(r"In SGD\s*(Millions|Thousands)?", re.I)
_WORKBOOK_UNITS = {"millions": 1_000_000, "thousands": 1_000, "": 1}
_WORKBOOK_PERIOD_END = re.compile(r"[A-Z][a-z]{2}-\d{2}-\d{4}")


def _label(text: str) -> str:
    return " ".join(text.split()).casefold()


def _sheet_lines(sheet: Sheet) -> dict[str, tuple[Decimal | str, str]]:
    """Row label -> (its figure, its text) for every row of a label and a figure; the first row of a label wins."""
    lines: dict[str, tuple[Decimal | str, str]] = {}
    for row in sheet.rows:
        if len(row) > 1 and isinstance(row[0], str) and isinstance(row[1], (Decimal, str)):
            lines.setdefault(_label(row[0]), (row[1], " ".join(cell_text(c) for c in row if c is not None)))
    return lines


def parse_financial_workbook(doc_id: str, path: Path) -> FinancialStatementSummary:
    """Parse the applicant's financial statements workbook (an income
    statement, balance sheet and cash flow statement, one sheet each).

    Figures are read in dollars: each sheet states its own unit ("In SGD
    Millions"). A dash is nil. The year end, revenue, total assets,
    liabilities and equity are required; every other figure is left empty
    where the statements lack it. The workbook states no company name or UEN.
    Each figure keeps the text of its row and the sheet it is on.
    """
    def refuse(reason: str) -> ValueError:
        return ValueError(f"{doc_id}: cannot read the financial statements: {reason}")

    sheets = {_label(sheet.name): (page, sheet) for page, sheet in enumerate(read_workbook(path), start=1)}
    units: dict[str, int] = {}
    lines: dict[str, dict[str, tuple[Decimal | str, str]]] = {}
    for key in {key for key, _ in _WORKBOOK_FIGURES.values()}:
        if key not in sheets:
            raise refuse(f"no {key} sheet")
        sheet = sheets[key][1]
        unit = next((m for cell in (sheet.rows[0] if sheet.rows else []) if isinstance(cell, str)
                     and (m := _WORKBOOK_UNIT.search(cell))), None)
        if unit is None:
            raise refuse(f"the {key} sheet states no SGD unit")
        units[key], lines[key] = _WORKBOOK_UNITS[(unit[1] or "").casefold()], _sheet_lines(sheets[key][1])

    period_end = next((m for cell in sheets["income statement"][1].rows[0] if isinstance(cell, str)
                       and (m := _WORKBOOK_PERIOD_END.search(cell))), None)
    if period_end is None:
        raise refuse("the income statement states no financial year end")

    def dollars(key: str, label: str) -> Decimal | None:
        found = lines[key].get(_label(label))
        if found is None or not (isinstance(found[0], Decimal) or found[0].strip() == "-"):
            return None
        return Decimal(0) if isinstance(found[0], str) else money(found[0] * units[key])

    figures: dict[str, Decimal | None] = {}
    quotes: dict[str, str] = {}
    pages: dict[str, int] = {}
    for field, (key, label) in _WORKBOOK_FIGURES.items():
        figures[field] = dollars(key, label)
        if field in _WORKBOOK_REQUIRED and figures[field] is None:
            raise refuse(f"no {label}")
        if figures[field] is not None:
            quotes[field], pages[field] = lines[key][_label(label)][1], sheets[key][0]
    debt = [d for label in _WORKBOOK_DEBT if (d := dollars("balance sheet", label)) is not None]
    return FinancialStatementSummary.model_validate({
        **figures, "doc_id": doc_id, "financial_year_end": datetime.strptime(period_end[0], "%b-%d-%Y").date(),
        "borrowings": sum(debt, Decimal(0)) if debt else None, "figure_pages": pages, "source_quotes": quotes,
    })


# ---------------------------------------------------------------------------
# Notice of Assessment parser
# ---------------------------------------------------------------------------

#: IRAS's own layout. The name is the first line of the address block, under
#: the request to quote the reference; the three figures are income,
#: deductions and chargeable income on one line.
_IRAS_REFERENCE = re.compile(r"Tax Reference Number\s*:\s*(\S+)")
_IRAS_NAME = re.compile(r"^Please quote the Tax Reference Number.*\n(.+)$", re.M)
_IRAS_YEAR = re.compile(r"YEAR OF ASSESSMENT\s+(\d{4})")
#: Income, deductions and chargeable income: on a line of their own under the "+ - =" signs, or after each sign
_IRAS_FIGURES = re.compile(r"^(?:\+ )?([\d,]+\.\d\d) (?:- )?([\d,]+\.\d\d) (?:= INCOME )?([\d,]+\.\d\d)$", re.M)
_IRAS_PAYABLE = re.compile(r"^Tax Payable\nby [^\n]+\n\$([\d,]+\.\d\d)$", re.M)
#: A Singapore postal code, looked for in the address lines under a name
_POSTAL_CODE = re.compile(r"\b\d{6}\b")
_ADDRESS_LINES = 5


def _parse_iras_notice(doc_id: str, text: str) -> NoticeOfAssessment:
    """A Notice of Assessment as IRAS prints it. The identity and the year are
    required; the tax computation is read where the notice shows it."""
    reference, name, year = (p.search(text) for p in (_IRAS_REFERENCE, _IRAS_NAME, _IRAS_YEAR))
    if not (reference and name and year):
        raise ValueError(f"{doc_id}: cannot read the Notice of Assessment")
    figures, payable = _IRAS_FIGURES.search(text), _IRAS_PAYABLE.search(text)
    postal = _POSTAL_CODE.search("\n".join(text[name.end():].splitlines()[1:_ADDRESS_LINES + 1]))
    income, deductions, chargeable = (_parse_money(f) for f in figures.groups()) if figures else (None,) * 3
    return NoticeOfAssessment(
        doc_id=doc_id, name=name[1].strip(), id_number=reference[1], year_of_assessment=int(year[1]),
        income=income, deductions=deductions, chargeable_income=chargeable,
        tax_payable=_parse_money(payable[1]) if payable else None, postal_code=postal[0] if postal else None,
    )


def _parse_notice_of_assessment(doc_id: str, pages: list[str]) -> NoticeOfAssessment:
    text = pages[0] if pages else ""
    if _IRAS_REFERENCE.search(text):
        return _parse_iras_notice(doc_id, text)
    lines = [line for line in text.splitlines() if line.strip()]

    def _get(label: str) -> str:
        return next((line.split(":", 1)[1].strip() for line in lines if line.startswith(f"{label}:")), "")

    return NoticeOfAssessment(
        doc_id=doc_id,
        name=_get("Name"),
        id_number=_get("Tax Reference No."),
        year_of_assessment=int(_get("Year of Assessment")),
    )


# ---------------------------------------------------------------------------
# ACRA profile parser
# ---------------------------------------------------------------------------

#: Header fields of the first page, by the label ACRA prints before the colon
_ACRA_LABELS = {
    "company_name": "Name of Company", "uen": "UEN", "incorporation_date": "Incorporation Date",
    "status": "Status of Company", "registered_address": "Registered Office Address", "issued_on": "Date",
}
_ACRA_DATE = r"\d{1,2} [A-Za-z]{3,9} \d{4}"
#: An officer's line: name, NRIC/FIN, nationality and position, date appointed
_ACRA_OFFICER = re.compile(
    rf"^(?P<name>.+?) (?P<id>[STFGM]\d{{7}}[A-Z]) (?P<rest>[A-Za-z/ ]+) (?P<appointed>{_ACRA_DATE})$", re.M,
)
#: A shareholder's line: name, identification, nationality, shares held, date the address changed
_ACRA_SHAREHOLDER = re.compile(
    rf"^(?P<name>.+?) (?=\S*\d)\S{{8,}} [A-Za-z ]+? (?P<shares>[\d,]{{4,}}) {_ACRA_DATE}$", re.M,
)
#: Positions an officer's line can end in, longest first; the nationality precedes them
_ACRA_POSITIONS = ("CHIEF EXECUTIVE OFFICER", "ALTERNATE DIRECTOR", "MANAGING DIRECTOR", "DIRECTOR", "CHAIRMAN",
                   "SECRETARY", "CEO", "AUDITOR")
#: A line the nationality and position columns wrap onto, under an officer's line (the address has digits)
_ACRA_WRAPPED = re.compile(r"^[A-Z][A-Z/ ]*$")
#: What the nationality column wraps ("SINGAPORE" / "CITIZEN"); the rest of a wrapped line is the position's
_ACRA_NATIONALITY_WRAP = "CITIZEN"


#: AcraFinancials field -> the label ACRA prints before the figures (the label
#: may wrap onto the next line after them; the first figure is the latest period)
_ACRA_FIGURES = {
    "total_assets": r"Total Assets",
    "total_current_assets": r"Total Current Assets",
    "total_liabilities": r"Total Liabilities",
    "total_current_liabilities": r"Total Current Liabilities",
    "retained_earnings": r"Retained Earnings \(Accumulated",
    "revenue": r"Revenue",
    "profit_before_tax": r"Profit\(Loss\) before tax from continuing",
    "profit_after_tax": r"Profit\(Loss\) after tax from continuing",
    "ebit": r"EBIT",
    "operating_cashflow": r"Net cashflow from \(used in\) operating",
    "investing_cashflow": r"Net cashflow from \(used in\) investing",
    "financing_cashflow": r"Net cashflow from \(used in\) financing",
}
_ACRA_PERIOD = re.compile(rf"^Financial Period\s+({_ACRA_DATE}) TO .*\n\s*({_ACRA_DATE})", re.M)
_ACRA_CURRENCY = re.compile(r"^Currency\s+([A-Z]{3})", re.M)


def _acra_figure(text: str, label: str) -> Decimal | None:
    """The first (latest period) figure after *label*: "(1.541)" is a loss,
    "*" is a figure ACRA does not have."""
    found = re.search(rf"^{label}\s+(\(?[\d,]*\.?\d+\)?|\*)(?=\s)", text, re.M)
    if found is None or found[1] == "*":
        return None
    figure = Decimal(found[1].strip("()").replace(",", ""))
    return -figure if found[1].startswith("(") else figure


def _parse_acra_financials(text: str) -> AcraFinancials | None:
    """The profile's latest financial period, or None where it has no financial information."""
    period, currency = _ACRA_PERIOD.search(text), _ACRA_CURRENCY.search(text)
    if "Financial Information" not in text:
        return None
    if period is None or currency is None:
        raise ValueError("cannot read the financial information")
    return AcraFinancials(
        period_start=_parse_acra_date(period[1]), period_end=_parse_acra_date(period[2]), currency=currency[1],
        **{field: _acra_figure(text, label) for field, label in _ACRA_FIGURES.items()},
    )


def _acra_position(text: str, rest: str, end: int) -> str:
    """An officer's position: the end of its line (*rest*, after the identity number), with the words the
    position column wraps onto the lines under it (which end at *end*), the nationality's wrap left out."""
    words = rest.split()
    for line in text[end:].splitlines()[1:_ADDRESS_LINES]:
        if not _ACRA_WRAPPED.match(line):
            break
        words += [word for word in line.split() if word != _ACRA_NATIONALITY_WRAP]
    written = " ".join(words)
    return next((role for role in _ACRA_POSITIONS if written.endswith(role)), rest)


def _acra_postal_code(text: str, end: int) -> str | None:
    """The postal code in the address lines under an officer's line (which ends at *end*), up to the next record."""
    for line in text[end:].splitlines()[1:_ADDRESS_LINES]:
        if _ACRA_OFFICER.match(line) or line.startswith(("Shareholder(s)", "Officer(s)")):
            return None
        if postal := _POSTAL_CODE.search(line):
            return postal[0]
    return None


def _parse_acra_date(text: str) -> date:
    """A date as ACRA prints it: "9 October 1965", "04 Oct 2026" or "11 OCT 2020"."""
    for fmt in ("%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(text)


def _parse_acra_profile(doc_id: str, pages: list[str]) -> AcraProfile:
    """The company's header fields are read from the first page and are all
    required. Officers and shareholders are read wherever a line of either
    kind appears."""
    first_page = pages[0] if pages else ""
    header = {
        name: (m[1].strip() if (m := re.search(rf"^{re.escape(label)}\s*:\s*(.+)$", first_page, re.M)) else "")
        for name, label in _ACRA_LABELS.items()
    }
    try:
        if not all(header.values()):
            raise ValueError
        incorporated, issued = _parse_acra_date(header["incorporation_date"]), _parse_acra_date(header["issued_on"])
    except ValueError:
        raise ValueError(f"{doc_id}: cannot read the ACRA profile") from None
    text = "\n".join(pages)
    try:
        financials = _parse_acra_financials(text)
    except ValueError as exc:
        raise ValueError(f"{doc_id}: {exc}") from None
    return AcraProfile(
        doc_id=doc_id, company_name=header["company_name"], uen=header["uen"], incorporation_date=incorporated,
        status=header["status"], registered_address=header["registered_address"], issued_on=issued,
        officers=[
            AcraOfficer(
                name=m["name"], id_number=m["id"], appointed=_parse_acra_date(m["appointed"]),
                postal_code=_acra_postal_code(text, m.end()),
                position=_acra_position(text, m["rest"], m.end()),
            )
            for m in _ACRA_OFFICER.finditer(text)
        ],
        shareholders=[
            AcraShareholder(name=m["name"], shares=int(m["shares"].replace(",", "")))
            for m in _ACRA_SHAREHOLDER.finditer(text)
        ],
        financials=financials,
    )


# ---------------------------------------------------------------------------
# AcroForm helpers
# ---------------------------------------------------------------------------

def _field_values(fields: dict[str, Any]) -> dict[str, str]:
    """Field name -> value, for the fields that have one."""
    return {name: str(field["/V"]) for name, field in fields.items() if field.get("/V") is not None}


def _template_id(kind: str, fields: dict[str, Any]) -> str | None:
    """The id (file stem) of the GLDB *kind* ("entity" or "individual") form
    template whose fields are exactly *fields*, whatever the form is called."""
    return next((
        path.stem for path in Path(TEMPLATES_DIR).glob(f"gldb_fatca_crs_{kind}_*.pdf")
        if set(pypdf.PdfReader(str(path)).get_fields() or {}) == set(fields)
    ), None)


def _field_pages(reader: pypdf.PdfReader) -> dict[str, int]:
    """Field name -> the 1-based page its first widget is on."""
    pages: dict[str, int] = {}
    for number, page in enumerate(reader.pages, start=1):
        for ref in page.get("/Annots") or []:
            widget = ref.get_object()
            name = widget.get("/T") or (widget["/Parent"].get_object().get("/T") if "/Parent" in widget else None)
            if name is not None:
                pages.setdefault(str(name), number)
    return pages


class _Form:
    """The values of one filled AcroForm, read by field name, and the page each field is on."""

    def __init__(self, reader: pypdf.PdfReader) -> None:
        self.raw = _field_values(reader.get_fields() or {})
        self.pages = _field_pages(reader)

    def text(self, key: str) -> str | None:
        value = self.raw.get(key, "").strip()
        return value or None

    def checked(self, key: str) -> bool:
        return self.raw.get(key, "") == "/Yes"

    def signed(self, key: str) -> bool:
        """Whether signature field *key* carries a signature."""
        return bool(self.raw.get(key))

    def address(self, fields: dict[str, Any]) -> Address:
        """An address from one address block of a field map; a part held by
        several fields takes the first that has a value."""
        return Address(**{
            part: next((v for key in ([keys] if isinstance(keys, str) else keys) if (v := self.text(key))), None)
            for part, keys in fields.items()
        })

    def tax_rows(self, rows: list[dict[str, str]]) -> list[TaxResidencyRow]:
        """The tax residency rows of a field map that have any content, numbered from 1."""
        found = []
        for number, row in enumerate(rows, start=1):
            country, tin, explanation = (self.text(row[part]) for part in ("country", "tin", "explanation"))
            reasons = [reason for reason in ("A", "B+", "C") if self.checked(row[reason])]
            if country or tin or explanation or reasons:
                found.append(TaxResidencyRow(
                    row=number, country=country, tin=tin, reasons_ticked=reasons, explanation=explanation,
                    page=self.pages.get(row["country"], 1),
                ))
        return found


# ---------------------------------------------------------------------------
# Entity self-cert AcroForm parser
# ---------------------------------------------------------------------------

def parse_entity_self_cert(doc_id: str, path: Path) -> EntitySelfCert:
    """Parse a GLDB FATCA/CRS Entity AcroForm using pypdf get_fields()."""
    reader = pypdf.PdfReader(str(path))
    fields = reader.get_fields() or {}
    form = _Form(reader)
    _text, _checked = form.text, form.checked

    # Part 1 — identification
    legal_name = _text("Full Legal Name of Entity Branch")
    coi = _text("Country of Incorporation")
    reg_addr = form.address(ENTITY_FIELD_MAP["registered_address"])
    mailing_different = _checked("Check Box1")
    mail_addr = form.address(ENTITY_FIELD_MAP["mailing_address"])
    mailing_explanation_key = (
        "If the country of the mailing address does not match the country of the "
        "registered address please explainRow1"
    )
    mailing_explanation = _text(mailing_explanation_key)

    # Part 2 — branch
    branch_ticked: list[str] = []
    if _checked("Check Box2"):
        branch_ticked.append("No")
    if _checked("Check Box3"):
        branch_ticked.append("Yes")
    hq_jurisdiction = _text("No YesIf yes in which jurisdiction is the headquarters located")

    # Part 3 — US person
    us_person_ticked: list[str] = []
    if _checked("Check Box4"):
        us_person_ticked.append("No")
    if _checked("Check Box5"):
        us_person_ticked.append("Yes")
    us_tin = _text("Text6")

    # Part 4 — tax residency rows
    tax_rows = form.tax_rows(ENTITY_FIELD_MAP["tax_rows"])

    # Part 5 — entity type
    entity_type_keys = {
        "A": "Check Box16", "B": "Check Box17", "C": "Check Box18",
        "D": "Check Box24", "E": "Check Box25", "F": "Check Box26",
        "G": "Check Box27", "H": "Check Box28", "I": "Check Box29",
        "J": "Check Box30", "K": "Check Box31", "L": "Check Box32",
        "M": "Check Box33",
    }
    entity_types_ticked = [letter for letter, key in entity_type_keys.items() if _checked(key)]
    giin_parts = [_text("Text21"), _text("Text22"), _text("Text23")]

    # Part 6 — controlling persons
    cp_slots = [
        ("Text47", {
            "legal_ownership": "Check Box34",
            "legal_senior_managing_official": "Check Box36",
            "legal_other_means": "Check Box37",
            "trust_settlor": "Check Box38",
            "trust_trustee": "Check Box39",
            "trust_protector": "Check Box40",
            "trust_beneficiary": "Check Box41",
            "trust_other": "Check Box42",
            "arrangement_settlor_eq": "Check Box35",
            "arrangement_trustee_eq": "Check Box43",
            "arrangement_protector_eq": "Check Box44",
            "arrangement_beneficiary_eq": "Check Box45",
            "arrangement_other_eq": "Check Box46",
        }, 1),
        ("Text48", {
            "legal_ownership": "Check Box49",
            "legal_senior_managing_official": "Check Box50",
            "legal_other_means": "Check Box51",
            "trust_settlor": "Check Box52",
            "trust_trustee": "Check Box53",
            "trust_protector": "Check Box54",
            "trust_beneficiary": "Check Box55",
            "trust_other": "Check Box56",
            "arrangement_settlor_eq": "Check Box57",
            "arrangement_trustee_eq": "Check Box58",
            "arrangement_protector_eq": "Check Box59",
            "arrangement_beneficiary_eq": "Check Box60",
            "arrangement_other_eq": "Check Box61",
        }, 2),
        ("Text75", {
            "legal_ownership": "Check Box62",
            "legal_senior_managing_official": "Check Box63",
            "legal_other_means": "Check Box64",
            "trust_settlor": "Check Box65",
            "trust_trustee": "Check Box66",
            "trust_protector": "Check Box67",
            "trust_beneficiary": "Check Box68",
            "trust_other": "Check Box69",
            "arrangement_settlor_eq": "Check Box70",
            "arrangement_trustee_eq": "Check Box71",
            "arrangement_protector_eq": "Check Box72",
            "arrangement_beneficiary_eq": "Check Box73",
            "arrangement_other_eq": "Check Box74",
        }, 3),
    ]
    controlling_persons: list[ControllingPerson] = []
    for name_key, type_map, slot_num in cp_slots:
        name = _text(name_key)
        types = [label for label, key in type_map.items() if _checked(key)]
        if name or types:
            controlling_persons.append(ControllingPerson(
                slot=slot_num,
                name=name,
                types_ticked=types,
                page=form.pages.get(name_key, 1),
            ))

    # Part 7 — signatories
    sig_slots = [
        ("Name", "Designation", "Date DDMMYY", "Signature76", 1),
        ("Name_2", "Designation_2", "Date DDMMYY_2", "Signature77", 2),
    ]
    signatories: list[Signatory] = []
    for name_key, desig_key, date_key, sig_key, slot_num in sig_slots:
        name = _text(name_key)
        desig = _text(desig_key)
        date_raw = _text(date_key)
        sig_signed = _checked(sig_key)
        parsed_date: date | None = None
        if date_raw:
            parsed_date = _parse_ddmmyy(date_raw)
        if name or desig or date_raw:
            signatories.append(Signatory(
                slot=slot_num,
                name=name,
                designation=desig,
                date_raw=date_raw,
                date=parsed_date,
                signature_field_signed=sig_signed,
                page=form.pages.get(name_key, 1),
            ))

    return EntitySelfCert(
        doc_id=doc_id,
        template_id=_template_id("entity", fields),
        legal_name=legal_name,
        country_of_incorporation=coi,
        registered_address=reg_addr,
        mailing_different=mailing_different,
        mailing_address=mail_addr,
        mailing_explanation=mailing_explanation,
        branch_ticked=branch_ticked,
        hq_jurisdiction=hq_jurisdiction,
        us_person_ticked=us_person_ticked,
        us_tin=us_tin,
        tax_rows=tax_rows,
        entity_types_ticked=entity_types_ticked,
        giin_parts=giin_parts,
        controlling_persons=controlling_persons,
        signatories=signatories,
        raw_fields=form.raw,
    )


# ---------------------------------------------------------------------------
# Individual self-cert AcroForm parser
# ---------------------------------------------------------------------------

def parse_individual_self_cert(doc_id: str, path: Path) -> IndividualSelfCert:
    """Parse a GLDB FATCA/CRS Individual AcroForm using pypdf get_fields()."""
    reader = pypdf.PdfReader(str(path))
    fields = reader.get_fields() or {}
    form = _Form(reader)
    declaration = INDIVIDUAL_FIELD_MAP["declaration"]
    born, declared = (form.text(INDIVIDUAL_FIELD_MAP["date_of_birth"]), form.text(declaration["date"]))
    return IndividualSelfCert(
        doc_id=doc_id,
        template_id=_template_id("individual", fields),
        full_name=form.text(INDIVIDUAL_FIELD_MAP["full_name"]),
        date_of_birth=_parse_ddmmyy(born) if born else None,
        place_of_birth=form.text(INDIVIDUAL_FIELD_MAP["place_of_birth"]),
        residential_address=form.address(INDIVIDUAL_FIELD_MAP["residential_address"]),
        mailing_different=form.checked(INDIVIDUAL_FIELD_MAP["mailing_different"]),
        mailing_address=form.address(INDIVIDUAL_FIELD_MAP["mailing_address"]),
        mailing_explanation=form.text(INDIVIDUAL_FIELD_MAP["mailing_explanation"]),
        us_status_ticked=[status for status, key in INDIVIDUAL_FIELD_MAP["us_status"].items() if form.checked(key)],
        us_tin_parts=[form.text(key) for key in INDIVIDUAL_FIELD_MAP["us_tin_parts"]],
        tax_rows=form.tax_rows(INDIVIDUAL_FIELD_MAP["tax_rows"]),
        declaration_name=form.text(declaration["name"]),
        declaration_id_number=form.text(declaration["id_number"]),
        declaration_date=_parse_ddmmyy(declared) if declared else None,
        signature_field_signed=form.signed(declaration["signature"]),
        raw_fields=form.raw,
    )


def _parse_ddmmyy(s: str) -> date | None:
    """Parse DD/MM/YY or DD/MM/YYYY date strings. Returns None if unparseable."""
    s = s.strip()
    for fmt in ("%d/%m/%y", "%d/%m/%Y", "%d-%m-%y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Pack parser — public entry point
# ---------------------------------------------------------------------------

def parse_pack(
    case_id: str,
    docs: list[DocMeta],
    pdf_paths: list[Path],
    manifest: ApplicationManifest | None,
    ocr: dict[str, list[str]] | None = None,
) -> "ExtractedPack":
    """Parse all documents in a pack and return a populated ExtractedPack.

    *ocr* (doc_id -> page text from the OCR stage) fills pages that have no
    text layer and is kept on the pack for the Forensics rule group.
    """
    from gldb.schemas import ExtractedPack

    ocr = ocr or {}
    path_map: dict[str, Path] = {p.stem: p for p in pdf_paths}

    invoices = []
    bank_statements = []
    financial_statements = []
    purchase_orders = []
    bills_of_lading = []
    entity_self_certs = []
    individual_self_certs = []
    notices_of_assessment = []
    acra_profiles = []

    for doc in docs:
        p = path_map.get(doc.doc_id)
        if p is None:
            continue
        pages = read_text(p, ocr=ocr.get(doc.doc_id))

        if doc.doc_type == "invoice":
            invoices.append(_parse_invoice(doc.doc_id, pages))
        elif doc.doc_type == "bank_statement":
            bank_statements.append(
                parse_bank_statement_form(doc.doc_id, p) if is_bank_statement_form(p)
                else _parse_bank_statement(doc.doc_id, pages)
            )
        elif doc.doc_type == "financial_statements":
            financial_statements.append(
                parse_financial_workbook(doc.doc_id, p) if is_workbook(p) else _parse_fs_summary(doc.doc_id, pages)
            )
        elif doc.doc_type == "purchase_order":
            purchase_orders.append(_parse_purchase_order(doc.doc_id, pages))
        elif doc.doc_type == "bill_of_lading":
            bills_of_lading.append(_parse_bill_of_lading(doc.doc_id, pages))
        elif doc.doc_type == "fatca_crs_entity":
            entity_self_certs.append(parse_entity_self_cert(doc.doc_id, p))
        elif doc.doc_type == "fatca_crs_individual":
            individual_self_certs.append(parse_individual_self_cert(doc.doc_id, p))
        elif doc.doc_type == "notice_of_assessment":
            notices_of_assessment.append(_parse_notice_of_assessment(doc.doc_id, pages))
        elif doc.doc_type == "acra_profile":
            acra_profiles.append(_parse_acra_profile(doc.doc_id, pages))

    return ExtractedPack(
        case_id=case_id,
        docs=docs,
        invoices=invoices,
        bank_statements=bank_statements,
        financial_statements=financial_statements,
        purchase_orders=purchase_orders,
        bills_of_lading=bills_of_lading,
        entity_self_certs=entity_self_certs,
        individual_self_certs=individual_self_certs,
        notices_of_assessment=notices_of_assessment,
        acra_profiles=acra_profiles,
        manifest=manifest,
        extraction_mode="stub",
        paths=path_map,
        ocr=ocr,
    )
