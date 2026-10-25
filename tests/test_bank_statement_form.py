"""
tests/test_bank_statement_form.py
---------------------------------
A bank statement can arrive as a fillable PDF form: its figures are form
field values, not page text. These tests build such forms on the fly with
reportlab and check that they are read deterministically (never by an LLM)
and checked like any other statement. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from gldb import extract_llm, extract_stub
from gldb.checks import check_bank_running_bal
from gldb.extract_stub import is_bank_statement_form, parse_bank_statement_form
from gldb.intake import intake
from gldb.schemas import ExtractedPack
from tests.statement_forms import BLANK, END, ROWS, Row, statement_form


def test_header_and_rows_are_read_from_the_fields(tmp_path: Path) -> None:
    bank = parse_bank_statement_form("stmt", statement_form(tmp_path, [*ROWS, END, BLANK]))
    assert bank.account_holder == "ACMA Ltd."
    assert (bank.period_start, bank.period_end) == (date(2026, 1, 1), date(2026, 1, 31))
    assert (bank.opening_balance, bank.closing_balance) == (Decimal("1000.00"), Decimal("1500.50"))
    assert [(t.date, t.description, t.credit, t.debit, t.balance) for t in bank.transactions] == [
        (date(2026, 1, 2), "Payment - Rent", None, Decimal("500.00"), Decimal("500.00")),
        (date(2026, 1, 15), "Sales Revenue", Decimal("1000.50"), None, Decimal("1500.50")),
    ]


def test_the_opening_and_closing_balances_are_quoted_as_the_form_holds_them(tmp_path: Path) -> None:
    bank = parse_bank_statement_form("stmt", statement_form(tmp_path, [*ROWS, END]))
    assert bank.source_quotes == {"Opening Balance": "1000", "Closing Balance": "1500.50"}


def test_the_bank_name_is_not_in_the_form(tmp_path: Path) -> None:
    """The form carries its bank only as a logo image."""
    assert parse_bank_statement_form("stmt", statement_form(tmp_path, [*ROWS, END])).bank_name is None


def test_spreadsheet_float_noise_is_rounded_to_cents(tmp_path: Path) -> None:
    rows: list[Row] = [("15/01/2026", "Sales Revenue", "1000.5", "", "1500.499999998"), END]
    bank = parse_bank_statement_form("stmt", statement_form(tmp_path, rows))
    assert bank.transactions[0].balance == Decimal("1500.50")


def test_a_row_with_an_amount_but_no_date_is_refused(tmp_path: Path) -> None:
    rows: list[Row] = [ROWS[0], ("", "Sales Revenue", "1000.50", "", "1500.50"), END]
    with pytest.raises(ValueError, match="stmt.*cannot parse"):
        parse_bank_statement_form("stmt", statement_form(tmp_path, rows))


def test_a_row_after_the_end_of_transactions_is_refused(tmp_path: Path) -> None:
    """A silently dropped row would let the balance checks pass on missing data."""
    with pytest.raises(ValueError, match="stmt.*cannot parse"):
        parse_bank_statement_form("stmt", statement_form(tmp_path, [ROWS[0], END, ROWS[1]]))


def test_an_unreadable_date_is_refused(tmp_path: Path) -> None:
    rows: list[Row] = [("2nd Jan", "Payment - Rent", "", "500", "500"), END]
    with pytest.raises(ValueError, match="stmt.*cannot parse"):
        parse_bank_statement_form("stmt", statement_form(tmp_path, rows))


def test_a_form_without_the_statement_fields_is_refused(tmp_path: Path) -> None:
    path = statement_form(tmp_path, [*ROWS, END], header={"F01008": "ACMA Ltd."})
    with pytest.raises(ValueError, match="not a bank statement form"):
        parse_bank_statement_form("stmt", path)


def test_only_a_statement_form_is_recognised(tmp_path: Path) -> None:
    plain = tmp_path / "plain.pdf"
    canvas = Canvas(str(plain), pagesize=A4)
    canvas.drawString(72, 800, "STATEMENT OF ACCOUNT")
    canvas.save()
    assert is_bank_statement_form(statement_form(tmp_path, [*ROWS, END]))
    assert not is_bank_statement_form(plain)
    assert not is_bank_statement_form(statement_form(tmp_path, [], header={"F01008": "ACMA Ltd."}, name="other.pdf"))


def test_a_balance_edited_in_the_form_is_caught(tmp_path: Path) -> None:
    rows: list[Row] = [ROWS[0], ("15/01/2026", "Sales Revenue", "1000.50", "", "9500.50"), END]
    bank = parse_bank_statement_form("stmt", statement_form(tmp_path, rows))
    pack = ExtractedPack(case_id="t", docs=[], invoices=[], bank_statements=[bank], financial_statements=[],
                         extraction_mode="stub")
    assert {e.check_id for e in check_bank_running_bal(pack)} == {"BANK_RUNNING_BAL"}


@pytest.mark.parametrize("module", [extract_stub, extract_llm])
def test_both_extractors_read_the_form_without_an_llm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module
) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("a form's contents never go to an LLM")

    monkeypatch.setattr(extract_llm, "converse", refuse)
    path = statement_form(tmp_path, [*ROWS, END])
    docs = intake("t", [path])
    assert [d.doc_type for d in docs] == ["bank_statement"]
    pack = module.parse_pack("t", docs, [path], None)
    (bank,) = pack.bank_statements
    assert bank.doc_id == "ACMA BANK STATEMENT"
    assert [t.description for t in bank.transactions] == ["Payment - Rent", "Sales Revenue"]
