"""
tests/workbooks.py
------------------
Shared helper: a real .xlsx built on the fly (zip of the same XML parts Excel
writes), so reader and parser tests do not depend on the team's sample files.
"""

from __future__ import annotations

import zipfile
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

Value = str | int | float | Decimal | None
CORE = (
    '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
    'xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
    '<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
    '<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified></cp:coreProperties>'
)
APP = (
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<Application>{application}</Application></Properties>"
)
MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
RELS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _column(index: int) -> str:
    name = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        name = chr(65 + rest) + name
    return name


def workbook(
    tmp_path: Path,
    sheets: dict[str, list[list[Value]]],
    name: str = "Submitted financials.xlsx",
    created: str = "2026-10-08T04:50:08Z",
    modified: str = "2026-10-14T08:48:23Z",
    application: str = "Microsoft Excel",
) -> Path:
    """An .xlsx with one sheet per entry of *sheets* (rows of cells, text as
    shared strings, numbers written exactly as given, None left empty)."""
    shared: list[str] = []
    parts: dict[str, str] = {}
    for number, rows in enumerate(sheets.values(), start=1):
        body = []
        for row_number, row in enumerate(rows, start=1):
            cells = []
            for column, value in enumerate(row):
                ref = f"{_column(column)}{row_number}"
                if value is None:
                    continue
                if isinstance(value, str):
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            body.append(f'<row r="{row_number}">{"".join(cells)}</row>')
        parts[f"xl/worksheets/sheet{number}.xml"] = (
            f'<worksheet xmlns="{MAIN}"><sheetData>{"".join(body)}</sheetData></worksheet>'
        )
    names = "".join(
        f'<sheet name="{escape(title)}" sheetId="{n}" r:id="rId{n}"/>' for n, title in enumerate(sheets, start=1)
    )
    parts["xl/workbook.xml"] = f'<workbook xmlns="{MAIN}" xmlns:r="{RELS}"><sheets>{names}</sheets></workbook>'
    parts["xl/_rels/workbook.xml.rels"] = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        + "".join(
            f'<Relationship Id="rId{n}" Type="{RELS}/worksheet" Target="worksheets/sheet{n}.xml"/>'
            for n in range(1, len(sheets) + 1)
        )
        + "</Relationships>"
    )
    parts["xl/sharedStrings.xml"] = (
        f'<sst xmlns="{MAIN}">' + "".join(f"<si><t>{escape(text)}</t></si>" for text in shared) + "</sst>"
    )
    parts["docProps/core.xml"] = CORE.format(created=created, modified=modified)
    parts["docProps/app.xml"] = APP.format(application=application)
    path = tmp_path / name
    with zipfile.ZipFile(path, "w") as archive:
        for part, xml in parts.items():
            archive.writestr(part, xml)
    return path


#: The submitted statements of the team's sample, trimmed to the lines the
#: pipeline reads (figures in SGD millions, "-" for nil)
INCOME: list[list[Value]] = [
    ["Income Statement ", "LTM of Dec-31-2025", "In SGD Millions"], [],
    ["Revenue", Decimal("7.8990000000000000")],
    ["Total Revenue", Decimal("7.9349999999999996")], [],
    ["Operating Income", Decimal("-1.4990000000000001")], [],
    ["EBT Incl. Unusual Items", Decimal("-1.5409999999999999")], [],
    ["Earnings from Cont. Ops.", Decimal("-1.548")],
]
BALANCE: list[list[Value]] = [
    ["Balance sheet", "Dec-31-20255", "In SGD Millions"], [],
    ["ASSETS"],
    ["Cash And Equivalents", Decimal("1.613")],
    ["Total Current Assets", Decimal("6.1660000000000004")],
    ["Total Assets", Decimal("6.7489999999999997")], [],
    ["LIABILITIES"],
    ["Short-term Borrowings", Decimal("0.57099999999999995")],
    ["Curr. Port. of LT Debt", "-"],
    ["Total Current Liabilities", Decimal("5.4160000000000004")],
    ["Long-Term Debt", "-"],
    ["Total Liabilities", Decimal("5.657")], [],
    ["Retained Earnings", Decimal("-194.49700000000001")],
    ["Total Equity", Decimal("1.0920000000000001")],
]
CASHFLOW: list[list[Value]] = [
    ["Cashflow Statement", "LTM of Dec-31-2025", "In SGD Millions"], [],
    ["Cash from Ops.", Decimal("0.371")],
    ["Cash from Investing", Decimal("-0.122")],
    ["Cash from Financing", Decimal("-0.13700000000000001")],
]
STATEMENTS = {"Income Statement": INCOME, "Balance sheet": BALANCE, "Cashflow Statement": CASHFLOW}
