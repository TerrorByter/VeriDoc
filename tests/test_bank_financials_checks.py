"""
tests/test_bank_financials_checks.py
------------------------------------
Checks that hold the bank statements to the financial statements: the cash a
statement opens with, the debt its payments service, and the revenue its
receipts amount to. Packs are built directly. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from gldb.checks import check_xdoc_bank_cash, check_xdoc_bank_debt, check_xdoc_bank_revenue
from gldb.schemas import BankStatement, ExtractedPack, FinancialStatementSummary, Transaction

D = Decimal


def _fs(**kw) -> FinancialStatementSummary:
    fields = {
        "doc_id": "Submitted financials", "financial_year_end": date(2025, 12, 31), "revenue": D("7935000"),
        "total_assets": D("6749000"), "total_liabilities": D("5657000"), "total_equity": D("1092000"),
        "cash": D("1613000"), "borrowings": D("571000"),
        "figure_pages": {"revenue": 1}, "source_quotes": {"revenue": "Total Revenue 7.935"},
    }
    return FinancialStatementSummary(**{**fields, **kw})


def _txn(day: str, description: str, credit: str | None = None, debit: str | None = None) -> Transaction:
    return Transaction(date=date.fromisoformat(day), description=description, balance=D(0),
                       credit=D(credit) if credit else None, debit=D(debit) if debit else None)


def _stmt(*txns: Transaction, start: str = "2026-01-01", end: str = "2026-06-30", opening: str = "1213365.00",
          doc_id: str = "bank", **kw) -> BankStatement:
    return BankStatement(
        doc_id=doc_id, account_holder="ACMA Ltd.", period_start=date.fromisoformat(start), period_end=date.fromisoformat(end),
        opening_balance=D(opening), closing_balance=D(0), transactions=list(txns),
        source_quotes={"Opening Balance": opening}, **kw,
    )


def _pack(statements: list[BankStatement], fs: FinancialStatementSummary | None = None) -> ExtractedPack:
    return ExtractedPack(case_id="t", docs=[], invoices=[], bank_statements=statements,
                         financial_statements=[fs or _fs()], extraction_mode="stub")


# ---- opening cash ------------------------------------------------------------------------------

def test_a_statement_opening_within_the_declared_cash_passes() -> None:
    assert check_xdoc_bank_cash(_pack([_stmt()])) == []


def test_a_statement_opening_above_the_declared_cash_is_a_serious_mismatch() -> None:
    (finding,) = check_xdoc_bank_cash(_pack([_stmt(opening="1700000.00")]))
    assert (finding.check_id, finding.category, finding.severity, finding.document) == (
        "XDOC_BANK_CASH", "fraud_signal", "high", "bank",
    )
    assert (finding.expected, finding.observed, finding.quote) == (
        "at most SGD 1,613,000.00", "SGD 1,700,000.00", "1700000.00",
    )


def test_the_tolerance_is_a_dollar() -> None:
    assert check_xdoc_bank_cash(_pack([_stmt(opening="1613001.00")])) == []
    assert len(check_xdoc_bank_cash(_pack([_stmt(opening="1613001.01")]))) == 1


def test_only_the_statement_that_opens_the_year_is_compared() -> None:
    later = _stmt(start="2026-02-01", opening="9000000.00")
    assert check_xdoc_bank_cash(_pack([later])) == []
    first, second = _stmt(opening="1000000.00"), _stmt(start="2026-02-01", opening="9000000.00", doc_id="feb")
    assert check_xdoc_bank_cash(_pack([second, first])) == []


def test_without_declared_cash_nothing_is_compared() -> None:
    assert check_xdoc_bank_cash(_pack([_stmt(opening="9000000.00")], _fs(cash=None))) == []


# ---- debt service -------------------------------------------------------------------------------

BOND_YEAR = (
    _txn("2026-02-13", "Payment - Bond interest", debit="300000.00"),
    _txn("2026-02-13", "Bond issuance", credit="3000000.00"),
    _txn("2026-04-25", "Payment - Bond Principal", debit="3000000.00"),
    _txn("2026-05-27", "Payment - Bank Loan Interest", debit="30000.00"),
    _txn("2026-06-21", "Interest Income", credit="20000.00"),
)


def test_debt_borrowed_in_the_statements_supports_its_repayment() -> None:
    assert check_xdoc_bank_debt(_pack([_stmt(*BOND_YEAR)])) == []


def test_debt_borrowed_and_serviced_on_the_same_day_is_taken_in_that_order() -> None:
    """The statement may list the interest before the issuance; the issuance came first."""
    assert check_xdoc_bank_debt(_pack([_stmt(*BOND_YEAR[:3])], _fs(borrowings=D(0)))) == []


def test_principal_repaid_with_no_debt_to_repay_is_a_serious_mismatch() -> None:
    (finding,) = check_xdoc_bank_debt(_pack([_stmt(_txn("2026-04-25", "Payment - Bond Principal", debit="3000000.00"))]))
    assert (finding.check_id, finding.severity, finding.quote) == ("XDOC_BANK_DEBT", "high", "Payment - Bond Principal")
    assert (finding.expected, finding.observed) == ("at most SGD 571,000.00", "SGD 3,000,000.00")


def test_debt_repaid_once_cannot_be_repaid_again() -> None:
    twice = (*BOND_YEAR[:3], _txn("2026-05-01", "Payment - Bond Principal", debit="3000000.00"))
    (finding,) = check_xdoc_bank_debt(_pack([_stmt(*twice)]))
    assert finding.expected == "at most SGD 571,000.00"


def test_interest_on_a_bond_with_no_debt_outstanding_is_reported() -> None:
    (finding,) = check_xdoc_bank_debt(_pack([_stmt(_txn("2026-02-13", "Payment - Bond interest", debit="300000.00"))],
                                           _fs(borrowings=D(0))))
    assert (finding.severity, finding.observed) == ("medium", "SGD 300,000.00 interest")


def test_interest_received_is_not_debt_service() -> None:
    assert check_xdoc_bank_debt(_pack([_stmt(_txn("2026-06-21", "Interest Income", credit="20000.00"))], _fs(borrowings=D(0)))) == []


def test_debt_carries_from_one_statement_to_the_next() -> None:
    jan = _stmt(_txn("2026-01-10", "Loan drawdown", credit="500000.00"), end="2026-01-31", doc_id="jan")
    feb = _stmt(_txn("2026-02-10", "Loan repayment", debit="1000000.00"), start="2026-02-01", end="2026-02-28",
                doc_id="feb", opening="0")
    assert check_xdoc_bank_debt(_pack([feb, jan])) == []
    (finding,) = check_xdoc_bank_debt(_pack([feb, jan], _fs(borrowings=D(0))))
    assert finding.document == "feb" and finding.expected == "at most SGD 500,000.00"


def test_without_borrowings_or_a_statement_opening_the_year_debt_is_not_compared() -> None:
    repay = _txn("2026-04-25", "Payment - Bond Principal", debit="3000000.00")
    assert check_xdoc_bank_debt(_pack([_stmt(repay)], _fs(borrowings=None))) == []
    assert check_xdoc_bank_debt(_pack([_stmt(repay, start="2026-02-01")])) == []


# ---- revenue ------------------------------------------------------------------------------------

def _sales(amount: str, desc: str = "Sales Revenue - EFG Company") -> Transaction:
    return _txn("2026-03-01", desc, credit=amount)


def test_receipts_in_line_with_revenue_pass() -> None:
    assert check_xdoc_bank_revenue(_pack([_stmt(_sales("1670370.23"), _sales("1497280.63", "Sales Revenue - Metallica Inc."))])) == []


def test_receipts_far_below_revenue_are_reported_at_the_revenue_figure() -> None:
    (finding,) = check_xdoc_bank_revenue(_pack([_stmt(_sales("500000.00"))]))
    assert (finding.check_id, finding.category, finding.severity) == ("XDOC_BANK_REVENUE", "fraud_signal", "medium")
    assert (finding.document, finding.page, finding.quote) == ("Submitted financials", 1, "Total Revenue 7.935")
    assert finding.observed == "0.13 times revenue" and "181 days" in finding.explanation


def test_receipts_far_above_revenue_are_reported() -> None:
    (finding,) = check_xdoc_bank_revenue(_pack([_stmt(_sales("9000000.00"))]))
    assert finding.observed == "2.29 times revenue"


def test_money_that_is_not_sales_is_not_counted() -> None:
    other = (
        _txn("2026-02-13", "Bond issuance", credit="30000000.00"), _txn("2026-03-21", "Dividend Income", credit="5000000.00"),
        _txn("2026-01-16", "Investment Income - 123 Plastics Ltd.", credit="5000000.00"),
        _txn("2026-06-21", "Interest Income", credit="5000000.00"), _txn("2026-03-02", "Loan drawdown", credit="5000000.00"),
    )
    assert check_xdoc_bank_revenue(_pack([_stmt(_sales("3167650.86"), *other)])) == []


def test_statements_that_do_not_follow_the_balance_sheet_date_are_not_compared() -> None:
    assert check_xdoc_bank_revenue(_pack([_stmt(_sales("500000.00"), start="2026-02-01")])) == []


def test_too_few_days_of_statements_cannot_be_annualised() -> None:
    assert check_xdoc_bank_revenue(_pack([_stmt(_sales("100.00"), end="2026-02-15")])) == []


def test_a_company_with_no_revenue_is_not_compared() -> None:
    assert check_xdoc_bank_revenue(_pack([_stmt(_sales("100.00"))], _fs(revenue=D(0)))) == []
