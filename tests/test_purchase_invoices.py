"""
tests/test_purchase_invoices.py
-------------------------------
A company's trade runs both ways: it sells (invoices it issues, paid by a
credit) and it buys (suppliers' invoices addressed to it, paid by a debit). The
checks that assumed the applicant is always the seller read the direction from
who the invoice is addressed to, and an invoice names the purchase order it is
for. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from gldb.checks import (
    check_temp_trade_sequence,
    check_xdoc_identity,
    check_xdoc_invoice_bank,
    check_xdoc_po_invoice,
    is_purchase,
    matching_po,
)
from gldb.reconciliation import build_chains
from gldb.schemas import BankStatement, Invoice, LineItem, PurchaseOrder, Transaction
from tests.acma_records import acra, pack

D = Decimal
SUPPLIER, SUPPLIER_UEN = "SteelMen Pte Ltd.", "201012345K"


def _line(total: str = "109.00") -> list[LineItem]:
    subtotal = D(total) / D("1.09")
    return [LineItem(description="Steel sheets", quantity=1, unit_price=subtotal, amount=subtotal)]


def _invoice(number: str = "SM-0001", total: str = "109.00", **kw: Any) -> Invoice:
    fields: dict[str, Any] = {
        "doc_id": f"invoice_{number}", "invoice_number": number, "invoice_date": date(2026, 2, 1),
        "seller_name": SUPPLIER, "seller_uen": SUPPLIER_UEN, "buyer_name": "ACMA LTD.", "po_number": "PO-4001",
        "line_items": _line(total), "subtotal": D(total) / D("1.09"), "gst": D(total) - D(total) / D("1.09"),
        "total": D(total), "source_quotes": {"Total": f"TOTAL (SGD) {total}"},
    }
    return Invoice(**{**fields, **kw})


def _sale(number: str = "INV-1001", total: str = "109.00", **kw: Any) -> Invoice:
    sale = {"seller_name": "ACMA LTD.", "seller_uen": "196500233E", "buyer_name": "EFG Company", "po_number": "PO-2001"}
    return _invoice(number, total, **{**sale, **kw})


def _order(number: str = "PO-4001", total: str = "109.00", **kw: Any) -> PurchaseOrder:
    fields: dict[str, Any] = {
        "doc_id": f"purchase_order_{number}", "po_number": number, "po_date": date(2026, 1, 15), "buyer_name": "ACMA LTD.",
        "seller_name": SUPPLIER, "seller_uen": SUPPLIER_UEN, "line_items": _line(total), "total": D(total),
    }
    return PurchaseOrder(**{**fields, **kw})


def _statement(*rows: tuple[date, str | None, str | None]) -> BankStatement:
    """Rows of (date, credit, debit)."""
    return BankStatement(
        doc_id="bank_statement", account_holder="ACMA LTD.", period_start=date(2026, 1, 1), period_end=date(2026, 6, 30),
        opening_balance=D(0), closing_balance=D(0), page=1,
        transactions=[Transaction(date=on, description="x", credit=D(c) if c else None, debit=D(d) if d else None, balance=D(0))
                      for on, c, d in rows],
    )


def _case(*, invoices: list[Invoice], statement: BankStatement | None = None, orders: list[PurchaseOrder] | None = None) -> Any:
    return pack(acra_profiles=[acra()], invoices=invoices, purchase_orders=orders or [],
                bank_statements=[statement] if statement else [])


# ---------------------------------------------------------------------------
# Which way a trade runs
# ---------------------------------------------------------------------------

def test_an_invoice_to_the_applicant_is_a_purchase_and_any_other_is_a_sale() -> None:
    case = _case(invoices=[_invoice(), _sale()])
    assert is_purchase("ACMA LTD.", case) and is_purchase("Acma Ltd", case)
    assert not is_purchase("EFG Company", case)


def test_with_no_applicant_on_record_every_invoice_is_a_sale() -> None:
    assert not is_purchase("ACMA LTD.", pack(invoices=[_invoice()]))


# ---------------------------------------------------------------------------
# Identity: the applicant is the seller of its sales, not of its purchases
# ---------------------------------------------------------------------------

def test_a_suppliers_invoice_to_the_applicant_is_not_held_to_the_applicants_identity() -> None:
    assert check_xdoc_identity(_case(invoices=[_invoice()], orders=[_order()])) == []


def test_a_sale_by_a_company_that_is_not_the_applicant_is_still_flagged() -> None:
    found = check_xdoc_identity(_case(invoices=[_sale(seller_uen="999999999Z", seller_name="OTHER LTD.")]))
    assert {(e.severity, e.document) for e in found} == {("high", "invoice_INV-1001"), ("medium", "invoice_INV-1001")}


def test_an_invoice_to_a_stranger_from_a_stranger_is_flagged() -> None:
    found = check_xdoc_identity(_case(invoices=[_invoice(buyer_name="SOMEONE ELSE PTE. LTD.")]))
    assert [e.severity for e in found] == ["high", "medium"]


def test_the_applicants_own_order_to_a_supplier_is_not_held_to_the_applicants_identity() -> None:
    assert check_xdoc_identity(_case(invoices=[], orders=[_order()])) == []


def test_a_customers_order_placed_with_the_wrong_seller_is_still_flagged() -> None:
    order = _order(buyer_name="EFG Company", seller_name="ACMA LTD.", seller_uen="999999999Z")
    assert [e.severity for e in check_xdoc_identity(_case(invoices=[], orders=[order]))] == ["high"]


# ---------------------------------------------------------------------------
# Payment: a sale is paid by a credit, a purchase by a debit
# ---------------------------------------------------------------------------

def test_a_purchase_is_paid_by_a_debit_of_its_total() -> None:
    case = _case(invoices=[_invoice()], statement=_statement((date(2026, 2, 12), None, "109.00")))
    assert check_xdoc_invoice_bank(case) == []


def test_a_credit_of_the_same_amount_does_not_pay_a_purchase() -> None:
    case = _case(invoices=[_invoice()], statement=_statement((date(2026, 2, 12), "109.00", None)))
    (found,) = check_xdoc_invoice_bank(case)
    assert found.severity == "high" and "No bank debit matching invoice SM-0001" in found.explanation


def test_a_debit_of_the_same_amount_does_not_pay_a_sale() -> None:
    case = _case(invoices=[_sale()], statement=_statement((date(2026, 2, 12), None, "109.00")))
    (found,) = check_xdoc_invoice_bank(case)
    assert "No bank credit matching invoice INV-1001" in found.explanation


def test_a_sale_is_paid_by_a_credit_of_its_total() -> None:
    case = _case(invoices=[_sale()], statement=_statement((date(2026, 2, 12), "109.00", None)))
    assert check_xdoc_invoice_bank(case) == []


def test_a_purchase_a_cent_out_is_noted_not_flagged() -> None:
    case = _case(invoices=[_invoice()], statement=_statement((date(2026, 2, 12), None, "109.01")))
    (found,) = check_xdoc_invoice_bank(case)
    assert found.severity == "info" and "matching bank debit" in found.explanation


def test_one_debit_pays_one_invoice() -> None:
    case = _case(invoices=[_invoice("SM-0001"), _invoice("SM-0002")],
                 statement=_statement((date(2026, 2, 12), None, "109.00")))
    assert [e.document for e in check_xdoc_invoice_bank(case)] == ["invoice_SM-0002"]


def test_a_payment_before_the_invoice_or_long_after_it_does_not_pay_it() -> None:
    for paid in (date(2026, 1, 20), date(2026, 3, 10)):
        case = _case(invoices=[_invoice()], statement=_statement((paid, None, "109.00")))
        assert len(check_xdoc_invoice_bank(case)) == 1


def test_sales_and_purchases_of_one_amount_are_paid_separately() -> None:
    case = _case(invoices=[_sale(), _invoice()],
                 statement=_statement((date(2026, 2, 10), "109.00", None), (date(2026, 2, 12), None, "109.00")))
    assert check_xdoc_invoice_bank(case) == []


# ---------------------------------------------------------------------------
# Purchase orders: the one the invoice names
# ---------------------------------------------------------------------------

def test_an_invoice_is_matched_to_the_order_it_names_not_the_closest_total() -> None:
    near, named = _order("PO-4002", "109.00"), _order("PO-4001", "500.00")
    assert matching_po(_invoice(total="109.00"), _case(invoices=[], orders=[near, named])) == named


def test_an_invoice_naming_an_order_the_pack_lacks_has_none() -> None:
    case = _case(invoices=[], orders=[_order("PO-4002")])
    assert matching_po(_invoice(po_number="PO-9999"), case) is None


def test_an_invoice_naming_no_order_is_matched_by_buyer_and_nearest_total() -> None:
    near, far = _order("PO-4002", "109.00"), _order("PO-4003", "900.00")
    case = _case(invoices=[], orders=[far, near])
    assert matching_po(_invoice(po_number=None), case) == near
    assert matching_po(_invoice(po_number=None, buyer_name="NOBODY"), case) is None


def test_an_invoice_naming_an_order_the_pack_lacks_is_flagged_by_name() -> None:
    case = _case(invoices=[_invoice(po_number="PO-9999")], orders=[_order("PO-4002")])
    (found,) = check_xdoc_po_invoice(case)
    assert found.severity == "medium" and found.observed == "none"
    assert "PO-9999" in found.explanation and "not in the pack" in found.explanation


def test_a_purchase_billed_above_its_order_is_flagged() -> None:
    case = _case(invoices=[_invoice(total="150.00")], orders=[_order(total="109.00")])
    (found,) = check_xdoc_po_invoice(case)
    assert found.severity == "high" and "exceeds purchase order PO-4001" in found.explanation


def test_a_purchase_matching_its_order_is_not_flagged() -> None:
    assert check_xdoc_po_invoice(_case(invoices=[_invoice()], orders=[_order()])) == []


def test_the_trade_sequence_uses_the_order_the_invoice_names() -> None:
    late = _order("PO-4001", po_date=date(2026, 2, 20))  # raised after the invoice
    other = _order("PO-4002", po_date=date(2026, 1, 1))
    (found,) = check_temp_trade_sequence(_case(invoices=[_invoice()], orders=[other, late]))
    assert "PO-4001" in found.explanation


# ---------------------------------------------------------------------------
# The chain: a purchase's payment is a debit
# ---------------------------------------------------------------------------

def _payment(case: Any) -> Any:
    (chain,) = build_chains(case, check_xdoc_invoice_bank(case))
    return chain.steps[-1]


def test_a_purchases_chain_ends_at_the_debit_that_paid_it() -> None:
    case = _case(invoices=[_invoice()], statement=_statement((date(2026, 2, 12), None, "109.00")))
    step = _payment(case)
    assert step.status == "matched" and step.counterpart is not None
    assert step.counterpart.label == "Bank debit on 2026-02-12" and step.counterpart.value == "SGD 109.00"


def test_a_purchase_nothing_paid_breaks_its_chain() -> None:
    case = _case(invoices=[_invoice()], statement=_statement((date(2026, 2, 12), "109.00", None)))
    assert _payment(case).status == "missing"


def test_a_sales_chain_still_ends_at_the_credit() -> None:
    case = _case(invoices=[_sale()], statement=_statement((date(2026, 2, 12), "109.00", None)))
    step = _payment(case)
    assert step.counterpart is not None and step.counterpart.label == "Bank credit on 2026-02-12"
