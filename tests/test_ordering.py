"""
tests/test_ordering.py
----------------------
The one ordering policy of a trade, read by the deterministic rules and the
AI team's sequence check alike: what is valid, what is an anomaly (and which
rule reports it), and what has no policy. Offline.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from gldb.ai_team.checks import trade_dates
from gldb.intake import load_manifest
from gldb.ordering import TradeDates, Violation, undetermined, violations
from gldb.pipeline import assess

PACKS = Path(__file__).parent.parent / "data" / "packs"
PO, SHIP, INV, PAY = date(2026, 6, 1), date(2026, 6, 7), date(2026, 6, 10), date(2026, 6, 18)


def _dates(**fields: date | None) -> TradeDates:
    return replace(TradeDates(purchase_order=PO, shipped=SHIP, invoice=INV, payment=PAY,
                              incorporated=date(2023, 1, 16)), **fields)


def test_valid_orderings() -> None:
    assert violations(_dates()) == []  # PO, shipment, invoice, payment
    assert violations(_dates(shipped=date(2026, 6, 12))) == []  # PO, invoice, shipment
    assert violations(_dates(invoice=PO, shipped=PO)) == []  # on the PO date
    assert undetermined(_dates()) == []


def test_invoice_or_shipment_before_po_is_temp_trade_sequence() -> None:
    assert violations(_dates(invoice=date(2026, 5, 30))) == [
        Violation("TEMP_TRADE_SEQUENCE", "purchase_order", "invoice")]
    assert violations(_dates(shipped=date(2026, 5, 30))) == [
        Violation("TEMP_TRADE_SEQUENCE", "purchase_order", "shipped")]


def test_anything_before_incorporation_is_temp_before_incorporation() -> None:
    early = date(2022, 12, 1)
    assert violations(_dates(purchase_order=early, shipped=early, invoice=early)) == [
        Violation("TEMP_BEFORE_INCORPORATION", "incorporated", "purchase_order"),
        Violation("TEMP_BEFORE_INCORPORATION", "incorporated", "shipped"),
        Violation("TEMP_BEFORE_INCORPORATION", "incorporated", "invoice"),
    ]


def test_payment_before_invoice_is_undetermined_not_a_violation() -> None:
    advance = _dates(payment=date(2026, 6, 2))
    assert violations(advance) == [] and undetermined(advance) == ["payment before invoice"]


def test_missing_dates_are_never_a_violation() -> None:
    assert violations(_dates(purchase_order=None, shipped=None, payment=None, incorporated=None)) == []


def test_trade_dates_come_from_the_pack() -> None:
    folder = PACKS / "third_party_payment"
    pack = assess("ordering", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None).pack
    invoice = next(i for i in pack.invoices if i.invoice_number == "INV-1002")
    dates = trade_dates(invoice, pack, date(2023, 1, 16))
    assert dates == TradeDates(purchase_order=date(2026, 6, 21), shipped=date(2026, 7, 2), invoice=date(2026, 7, 5),
                               payment=date(2026, 7, 12), incorporated=date(2023, 1, 16))
    assert violations(dates) == []  # run 5's L08: invoice after shipment is a valid ordering
