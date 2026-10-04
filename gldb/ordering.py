"""
gldb/ordering.py
----------------
The ordering policy of a trade, in one place: the deterministic rules
(TEMP_TRADE_SEQUENCE, TEMP_BEFORE_INCORPORATION) and the AI team's sequence
check both read it, so a rule and an AI check can never disagree on what a
normal trade looks like.

Valid: purchase order, then shipment and invoice in either order (an invoice
may follow shipment by days), then payment; anything on the order's date.
An anomaly: an invoice or a shipment dated before its purchase order, or any
of them dated before incorporation. No policy: a payment before its invoice
(advance payment is normal on some terms, and a pack has no terms).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: (earlier, later, the rule that reports a breach): the pairs a trade must keep
TRADE_ORDER: tuple[tuple[str, str, str], ...] = (
    ("purchase_order", "invoice", "TEMP_TRADE_SEQUENCE"),
    ("purchase_order", "shipped", "TEMP_TRADE_SEQUENCE"),
)
#: The rule that reports a trade document dated before incorporation
BEFORE_INCORPORATION = "TEMP_BEFORE_INCORPORATION"
#: The trade documents incorporation must precede
_TRADE_DOCUMENTS = ("purchase_order", "shipped", "invoice")


@dataclass(frozen=True)
class TradeDates:
    """One trade's dates; None where the pack does not have it."""

    purchase_order: date | None
    shipped: date | None
    invoice: date
    payment: date | None
    incorporated: date | None


@dataclass(frozen=True)
class Violation:
    #: The check id of the deterministic rule that reports it
    rule: str
    earlier: str
    later: str


def breaks(earlier: date, later: date) -> bool:
    """Whether *later* is dated before *earlier*: a same-day pair is in order."""
    return later < earlier


def violations(dates: TradeDates) -> list[Violation]:
    """The policy's anomalies in *dates*; a missing date is never one."""
    found = [Violation(rule, earlier, later) for earlier, later, rule in TRADE_ORDER
             if (a := getattr(dates, earlier)) is not None and (b := getattr(dates, later)) is not None
             and breaks(a, b)]
    if dates.incorporated is not None:
        found += [Violation(BEFORE_INCORPORATION, "incorporated", doc) for doc in _TRADE_DOCUMENTS
                  if (on := getattr(dates, doc)) is not None and breaks(dates.incorporated, on)]
    return found


def undetermined(dates: TradeDates) -> list[str]:
    """Orderings in *dates* the policy has no rule for."""
    return ["payment before invoice"] if dates.payment is not None and dates.payment < dates.invoice else []
