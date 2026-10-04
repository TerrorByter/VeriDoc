"""
gldb/ai_team/checks.py
----------------------
Code checks of the AI team's leads against the case data. A lead names its
subject in ids the case defines (an invoice number, bank rows T01..., a
counterparty P01...); the checks read the extracted pack, the register and
the findings through those ids, never the lead's prose or a tool result's
text, and never call a model.
"""

from __future__ import annotations

from gldb.config import SCREENING_MATCH_THRESHOLD
from gldb.schemas import BankStatement, ExtractedPack, Transaction
from gldb.screening import screening_key, similarity


def bank_rows(pack: ExtractedPack) -> list[tuple[str, BankStatement, Transaction]]:
    """Every bank row with its id: T01, T02... over the statements in pack
    order and the rows in statement order, so an id is stable for a case."""
    rows = [(statement, txn) for statement in pack.bank_statements for txn in statement.transactions]
    return [(f"T{i:02d}", statement, txn) for i, (statement, txn) in enumerate(rows, 1)]


def _trade_party_names(pack: ExtractedPack) -> list[str]:
    """The names on the trade documents, in document order."""
    return [
        *(n for i in pack.invoices for n in (i.seller_name, i.buyer_name)),
        *(n for o in pack.purchase_orders for n in (o.buyer_name, o.seller_name)),
        *(n for b in pack.bills_of_lading for n in (b.shipper_name, b.consignee_name)),
    ]


def counterparties(pack: ExtractedPack) -> dict[str, str]:
    """Each counterparty's id (P01...) by its name's screening key: one id per
    company however often it appears, the applicant itself left out."""
    applicant = pack.manifest.company_name if pack.manifest else None
    keys: dict[str, str] = {}
    for name in _trade_party_names(pack):
        key = screening_key(name)
        if not key or key in keys or (applicant and similarity(name, applicant) >= SCREENING_MATCH_THRESHOLD):
            continue
        keys[key] = f"P{len(keys) + 1:02d}"
    return keys


def counterparty_id(pack: ExtractedPack, name: str) -> str | None:
    """The id of the counterparty *name*, or None for the applicant."""
    return counterparties(pack).get(screening_key(name))
