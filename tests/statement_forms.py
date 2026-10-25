"""
tests/statement_forms.py
------------------------
Fillable bank statement forms for tests, built on the fly with reportlab: a
one-page statement of account whose figures are form field values, not page
text.
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

#: (date, description, credit, debit, balance) as typed into the form
Row = tuple[str, str, str, str, str]

HEADER = {
    "F01005": "111-234-567-890", "F01007": "01/01/2026 to 31/01/2026", "F01008": "ACMA Ltd.",
    "F01012": "1000", "F01015": "1500.50",
}
ROWS: list[Row] = [
    ("02/01/2026", "Payment - Rent", "", "500", "500"),
    ("15/01/2026", "Sales Revenue", "1000.50", "", "1500.50"),
]
END: Row = ("", "--- End of Transactions --", "", "", "1500.50")
#: Unused rows the form still carries
BLANK: Row = ("", "", "", "", "")


def statement_form(tmp_path: Path, rows: list[Row], header: dict[str, str] | None = None,
                   name: str = "ACMA BANK STATEMENT.pdf") -> Path:
    """A fillable statement: every row has its five fields, filled or empty."""
    fields = dict(HEADER if header is None else header)
    for n, row in enumerate(rows, start=1):
        fields.update({f"F02{n:03d}{column}": value for column, value in zip("abcde", row)})
    path = tmp_path / name
    canvas = Canvas(str(path), pagesize=A4)
    canvas.drawString(72, 800, "STATEMENT OF ACCOUNT")
    for i, (field, value) in enumerate(fields.items()):
        canvas.acroForm.textfield(name=field, value=value, x=20 + (i % 6) * 90, y=780 - (i // 6) * 12,
                                  width=80, height=10, borderWidth=0, forceBorder=False)
    canvas.save()
    return path
