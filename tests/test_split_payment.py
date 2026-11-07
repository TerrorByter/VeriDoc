"""
tests/test_split_payment.py
---------------------------
The rule XDOC_INVOICE_BANK (gldb.checks) on a sale its buyer paid in parts:
two or more credits naming the invoice, each read as from its buyer, within
the match window and together making its total, are information, not a flag.
Anything less (a third party, a payer that cannot be read or may not be the
buyer, a sum short or over, a part out of time, one credit) stays a high
finding.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from gldb.checks import bank_rows, check_xdoc_invoice_bank
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import ExtractedPack

PACKS = Path(__file__).parent / "fixtures" / "packs"


@cache
def _pack() -> ExtractedPack:
    folder = PACKS / "innocent_split"
    return assess("split-innocent", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None).pack


def _part(pack: ExtractedPack, n: int) -> str:
    return next(row for row, _, t in bank_rows(pack) if t.description.endswith(f"INV-1002 PART {n}"))


def _split(**changes: dict[str, Any]) -> ExtractedPack:
    """innocent_split with its two INV-1002 part-payments (part_1, part_2) changed as given."""
    pack = _pack()
    by_row = {_part(pack, int(name[-1])): fields for name, fields in changes.items()}
    statements = []
    for statement in pack.bank_statements:
        txns = [t.model_copy(update=by_row.get(rid, {})) for rid, s, t in bank_rows(pack) if s is statement]
        statements.append(statement.model_copy(update={"transactions": txns}))
    return pack.model_copy(update={"bank_statements": statements})


def _inv_1002(pack: ExtractedPack) -> list[tuple[str, str]]:
    doc = next(i.doc_id for i in pack.invoices if i.invoice_number == "INV-1002")
    return [(e.severity, e.explanation) for e in check_xdoc_invoice_bank(pack) if e.document == doc]


def test_two_buyer_credits_that_make_the_total_are_information() -> None:
    pack = _pack()
    ((severity, explanation),) = _inv_1002(pack)
    assert severity == "info"
    assert explanation == (
        "Invoice INV-1002 (SGD 18530.00) was paid in 2 parts by its buyer Bayview Marine Supplies Pte. Ltd.: "
        f"{_part(pack, 1)} SGD 10000.00 on 2026-07-12, {_part(pack, 2)} SGD 8530.00 on 2026-07-20; "
        "together SGD 18530.00.")


def test_a_sum_within_the_rule_tolerance_is_information() -> None:
    assert [s for s, _ in _inv_1002(_split(part_2={"credit": Decimal("8529.50")}))] == ["info"]


@pytest.mark.parametrize(("changes", "why"), [
    # Not the buyer's money at all: a third-party payment is not explained by splitting it
    ({"part_1": {"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 1"},
      "part_2": {"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 2"}}, "third party"),
    ({"part_2": {"description": "GIRO CR HARBOURLINE LOGISTICS INV-1002 PART 2"}}, "mixed payers"),
    ({"part_2": {"credit": Decimal("8000.00")}}, "underpaid"),
    ({"part_2": {"credit": Decimal("9000.00")}}, "overpaid"),
    ({"part_2": {"date": date(2026, 9, 1)}}, "a part outside the match window"),
    ({"part_2": {"description": "CASH DEPOSIT INV-1002 PART 2"}}, "a payer that cannot be read"),
    ({"part_2": {"description": "GIRO CR BAYVIEW MARINE INV-1002 PART 2"}}, "a payer that may not be the buyer"),
    ({"part_2": {"description": "INTEREST"}}, "one credit is not a split"),
])
def test_anything_but_the_buyer_paying_the_whole_total_in_time_stays_a_flag(
        changes: dict[str, dict[str, Any]], why: str) -> None:
    assert [s for s, _ in _inv_1002(_split(**changes))] == ["high"], why


def test_the_pipeline_fast_tracks_a_sale_paid_in_parts() -> None:
    folder = PACKS / "innocent_split"
    result = assess("split-route", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None)
    assert [e.check_id for e in result.evidence if e.severity != "info"] == []
    assert (result.risk.band, result.routing.path) == ("Low", "fast_track")
