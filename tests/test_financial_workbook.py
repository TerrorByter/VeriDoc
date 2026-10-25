"""
tests/test_financial_workbook.py
--------------------------------
The applicant's financial statements as a workbook (income statement,
balance sheet, cash flow): read by code in either extraction mode, never by an
LLM, into the same summary a PDF gives plus the figures a workbook carries.
Workbooks are built on the fly. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from gldb import extract_llm, extract_stub
from gldb.checks import check_fs_balance
from gldb.config import money
from gldb.extract_stub import parse_financial_workbook
from gldb.intake import contains_quote, intake, read_text
from gldb.schemas import ExtractedPack
from tests.workbooks import BALANCE, CASHFLOW, INCOME, STATEMENTS, workbook


def _read(tmp_path: Path, sheets: dict | None = None):
    return parse_financial_workbook("fs", workbook(tmp_path, sheets or STATEMENTS))


def test_the_summary_is_read_in_dollars_not_millions(tmp_path: Path) -> None:
    fs = _read(tmp_path)
    assert fs.financial_year_end == date(2025, 12, 31)
    assert (fs.revenue, fs.total_assets, fs.total_liabilities, fs.total_equity) == (
        Decimal("7935000"), Decimal("6749000"), Decimal("5657000"), Decimal("1092000"),
    )


def test_the_workbook_states_no_identity(tmp_path: Path) -> None:
    fs = _read(tmp_path)
    assert (fs.company_name, fs.uen) == (None, None)


def test_the_further_figures_are_read(tmp_path: Path) -> None:
    fs = _read(tmp_path)
    assert (fs.cash, fs.total_current_assets, fs.total_current_liabilities) == (
        Decimal("1613000"), Decimal("6166000"), Decimal("5416000"),
    )
    assert (fs.ebit, fs.profit_before_tax, fs.profit_after_tax, fs.retained_earnings) == (
        Decimal("-1499000"), Decimal("-1541000"), Decimal("-1548000"), Decimal("-194497000"),
    )
    assert (fs.operating_cashflow, fs.investing_cashflow, fs.financing_cashflow) == (
        Decimal("371000"), Decimal("-122000"), Decimal("-137000"),
    )


def test_borrowings_add_the_debt_lines_and_a_dash_is_nil(tmp_path: Path) -> None:
    assert _read(tmp_path).borrowings == Decimal("571000")
    balance = [*BALANCE[:9], ["Curr. Port. of LT Debt", Decimal("0.1")], *BALANCE[10:11],
               ["Long-Term Debt", Decimal("2")], *BALANCE[12:]]
    sheets = {**STATEMENTS, "Balance sheet": balance}
    assert _read(tmp_path, sheets).borrowings == Decimal("2671000")


def test_a_statement_without_debt_lines_has_no_borrowings(tmp_path: Path) -> None:
    balance = [row for row in BALANCE if not row or row[0] not in {"Short-term Borrowings", "Curr. Port. of LT Debt", "Long-Term Debt"}]
    assert _read(tmp_path, {**STATEMENTS, "Balance sheet": balance}).borrowings is None


def test_a_figure_a_statement_lacks_is_empty(tmp_path: Path) -> None:
    cashflow = [row for row in CASHFLOW if not row or row[0] != "Cash from Investing"]
    fs = _read(tmp_path, {**STATEMENTS, "Cashflow Statement": cashflow})
    assert (fs.investing_cashflow, fs.operating_cashflow) == (None, Decimal("371000"))


def test_each_figure_is_quoted_from_its_row_on_its_sheet(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    fs = parse_financial_workbook("fs", path)
    assert fs.source_quotes["total_assets"] == "Total Assets 6.749"
    assert (fs.figure_pages["revenue"], fs.figure_pages["total_assets"], fs.figure_pages["operating_cashflow"]) == (1, 2, 3)
    assert all(contains_quote(read_text(path), quote) for quote in fs.source_quotes.values())


@pytest.mark.parametrize(("unit", "factor"), [("In SGD Millions", 1_000_000), ("In SGD Thousands", 1000), ("In SGD", 1)])
def test_the_sheets_own_unit_is_applied(tmp_path: Path, unit: str, factor: int) -> None:
    sheets = {name: [[row[0], row[1], unit] if i == 0 else row for i, row in enumerate(rows)] for name, rows in STATEMENTS.items()}
    assert _read(tmp_path, sheets).total_assets == money(Decimal("6.749") * factor)


def test_a_sheet_in_another_currency_or_unit_is_refused(tmp_path: Path) -> None:
    sheets = {**STATEMENTS, "Income Statement": [[INCOME[0][0], INCOME[0][1], "In USD Millions"], *INCOME[1:]]}
    with pytest.raises(ValueError, match="fs.*cannot read the financial statements.*unit"):
        _read(tmp_path, sheets)


def test_a_missing_year_end_or_required_figure_is_refused(tmp_path: Path) -> None:
    no_year = {**STATEMENTS, "Income Statement": [[INCOME[0][0], "LTM", INCOME[0][2]], *INCOME[1:]]}
    with pytest.raises(ValueError, match="cannot read the financial statements.*year end"):
        _read(tmp_path, no_year)
    no_assets = {**STATEMENTS, "Balance sheet": [row for row in BALANCE if not row or row[0] != "Total Assets"]}
    with pytest.raises(ValueError, match="cannot read the financial statements.*Total Assets"):
        _read(tmp_path, no_assets)


def test_a_workbook_that_balances_passes_and_an_edited_one_does_not(tmp_path: Path) -> None:
    def findings(sheets: dict) -> list[str]:
        fs = _read(tmp_path, sheets)
        pack = ExtractedPack(case_id="t", docs=[], invoices=[], bank_statements=[], financial_statements=[fs],
                             extraction_mode="stub")
        return [e.check_id for e in check_fs_balance(pack)]

    assert findings(STATEMENTS) == []
    edited = [row if not row or row[0] != "Total Equity" else ["Total Equity", Decimal("2.092")] for row in BALANCE]
    assert findings({**STATEMENTS, "Balance sheet": edited}) == ["FS_BALANCE"]


@pytest.mark.parametrize("module", [extract_stub, extract_llm])
def test_both_extractors_read_the_workbook_without_an_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module
) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a workbook's contents never go to an LLM")

    monkeypatch.setattr(extract_llm, "converse", refuse)
    path = workbook(tmp_path, STATEMENTS)
    docs = intake("t", [path])
    (fs,) = module.parse_pack("t", docs, [path], None).financial_statements
    assert (fs.doc_id, fs.total_assets) == ("Submitted financials", Decimal("6749000"))
