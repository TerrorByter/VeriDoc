"""
tests/test_workbook.py
----------------------
The .xlsx reader (gldb/workbook.py): cells, text and the file's own
properties, with the standard library only. Workbooks are built on the fly.
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from gldb.config import WORKBOOK_MAX_COLUMNS, WORKBOOK_MAX_ROWS
from gldb.workbook import WorkbookError, cell_text, is_workbook, properties, read_workbook, workbook_text
from tests.workbooks import workbook

SHEETS = {
    "Income Statement": [["Income Statement ", "LTM of Dec-31-2025", "In SGD Millions"], [], ["Revenue", Decimal("7.899")]],
    "Balance sheet": [["Total Assets", Decimal("6.7489999999999997"), None, "x"]],
}


def test_sheets_are_read_in_order_with_their_cells(tmp_path: Path) -> None:
    income, balance = read_workbook(workbook(tmp_path, SHEETS))
    assert (income.name, balance.name) == ("Income Statement", "Balance sheet")
    assert income.rows[0] == ["Income Statement ", "LTM of Dec-31-2025", "In SGD Millions"]
    assert income.rows[1] == []
    assert income.rows[2] == ["Revenue", Decimal("7.899")]


def test_stored_float_noise_is_not_read_as_a_figure(tmp_path: Path) -> None:
    balance = read_workbook(workbook(tmp_path, SHEETS))[1]
    assert balance.rows[0][:2] == ["Total Assets", Decimal("6.749")]


def test_empty_cells_between_values_stay_empty(tmp_path: Path) -> None:
    assert read_workbook(workbook(tmp_path, SHEETS))[1].rows[0] == ["Total Assets", Decimal("6.749"), None, "x"]


def test_text_is_one_line_per_row_and_empty_rows_are_dropped(tmp_path: Path) -> None:
    assert workbook_text(workbook(tmp_path, SHEETS)) == [
        "Income Statement \tLTM of Dec-31-2025\tIn SGD Millions\nRevenue\t7.899",
        "Total Assets\t6.749\tx",
    ]


def test_the_files_own_properties_are_read(tmp_path: Path) -> None:
    props = properties(workbook(tmp_path, SHEETS))
    assert props.created == datetime(2026, 10, 8, 4, 50, 8, tzinfo=timezone.utc)
    assert props.modified == datetime(2026, 10, 14, 8, 48, 23, tzinfo=timezone.utc)
    assert props.application == "Microsoft Excel"


def test_properties_a_file_lacks_are_empty(tmp_path: Path) -> None:
    path = workbook(tmp_path, SHEETS)
    stripped = tmp_path / "stripped.xlsx"
    with zipfile.ZipFile(path) as source, zipfile.ZipFile(stripped, "w") as target:
        for item in source.infolist():
            if not item.filename.startswith("docProps/"):
                target.writestr(item, source.read(item))
    props = properties(stripped)
    assert (props.created, props.modified, props.application) == (None, None, None)


@pytest.mark.parametrize(("value", "shown"), [
    (Decimal("7.935"), "7.935"), (Decimal("-1.4990000"), "-1.499"), (Decimal("100"), "100"),
    (Decimal("1E+2"), "100"), (Decimal("0.0000004"), "0"), (Decimal("12345678.9"), "12345678.9"),
    ("-", "-"), (None, ""),
])
def test_a_cell_is_shown_without_noise(value: str | Decimal | None, shown: str) -> None:
    assert cell_text(value) == shown


def test_only_xlsx_files_are_workbooks(tmp_path: Path) -> None:
    assert is_workbook(Path("a.XLSX")) and not is_workbook(Path("a.pdf")) and not is_workbook(Path("a.xls"))


def test_a_file_that_is_not_a_workbook_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "fake.xlsx"
    path.write_bytes(b"not a zip")
    with pytest.raises(WorkbookError, match="fake.xlsx"):
        read_workbook(path)


def test_a_zip_without_a_workbook_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "other.xlsx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("hello.txt", "hi")
    with pytest.raises(WorkbookError, match="other.xlsx"):
        read_workbook(path)


def test_a_sheet_larger_than_the_limit_is_refused(tmp_path: Path) -> None:
    tall = {"Big": [[1]] * (WORKBOOK_MAX_ROWS + 1)}
    wide = {"Wide": [[1] * (WORKBOOK_MAX_COLUMNS + 1)]}
    for name, sheets in (("tall.xlsx", tall), ("wide.xlsx", wide)):
        with pytest.raises(WorkbookError, match=name):
            read_workbook(workbook(tmp_path, sheets, name=name))
