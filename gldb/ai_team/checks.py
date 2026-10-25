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

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from gldb.checks import incorporation, matching_po
from gldb.config import (
    GENERIC_PAYERS,
    MATCH_WINDOW_DAYS,
    PAYER_DIFFERENT_THRESHOLD,
    PAYER_MIN_LETTERS,
    PAYER_SAME_THRESHOLD,
    PAYMENT_CHANNELS,
    SCREENING_MATCH_THRESHOLD,
    TOLERANCE_SGD,
)
from gldb.ai_team.verify import PATTERN_SUBJECTS
from gldb.ordering import TradeDates, undetermined, violations
from gldb import official
from gldb.schemas import BankStatement, EvidenceStatus, Evidence, ExtractedPack, Invoice, Lead, Pattern, Transaction
from gldb.screening import screening_key, similarity


def bank_rows(pack: ExtractedPack) -> list[tuple[str, BankStatement, Transaction]]:
    """Every bank row with its id: T01, T02... over the statements in pack
    order and the rows in statement order, so an id is stable for a case."""
    rows = [(statement, txn) for statement in pack.bank_statements for txn in statement.transactions]
    return [(f"T{i:02d}", statement, txn) for i, (statement, txn) in enumerate(rows, 1)]


def _party_documents(pack: ExtractedPack) -> list[tuple[str, str]]:
    """(document, party name) for each name on the trade documents, in document order."""
    return [
        *((i.doc_id, n) for i in pack.invoices for n in (i.seller_name, i.buyer_name)),
        *((o.doc_id, n) for o in pack.purchase_orders for n in (o.buyer_name, o.seller_name)),
        *((b.doc_id, n) for b in pack.bills_of_lading for n in (b.shipper_name, b.consignee_name)),
    ]


def counterparties(pack: ExtractedPack) -> dict[str, str]:
    """Each counterparty's id (P01...) by its name's screening key: one id per
    company however often it appears, the applicant itself left out."""
    applicant = pack.manifest.company_name if pack.manifest else None
    keys: dict[str, str] = {}
    for _, name in _party_documents(pack):
        key = screening_key(name)
        if not key or key in keys or (applicant and similarity(name, applicant) >= SCREENING_MATCH_THRESHOLD):
            continue
        keys[key] = f"P{len(keys) + 1:02d}"
    return keys


def counterparty_id(pack: ExtractedPack, name: str) -> str | None:
    """The id of the counterparty *name*, or None for the applicant."""
    return counterparties(pack).get(screening_key(name))


def subject_locations(pack: ExtractedPack) -> dict[str, tuple[str, int]]:
    """Where each subject a lead can name is, as (document, page): bank rows
    T01... on their statement, invoice numbers on their invoice, counterparties
    P01... on the first document that names them."""
    where = {row_id: (statement.doc_id, statement.page) for row_id, statement, _ in bank_rows(pack)}
    where.update({i.invoice_number: (i.doc_id, i.page) for i in pack.invoices})
    for doc_id, name in _party_documents(pack):
        if (party := counterparty_id(pack, name)) is not None:
            where.setdefault(party, (doc_id, 1))
    return where


def counterparty_name(pack: ExtractedPack, party_id: str) -> str | None:
    """The name counterparty *party_id* first appears under, or None."""
    return next((n for _, n in _party_documents(pack) if counterparty_id(pack, n) == party_id), None)


def names(number: str) -> re.Pattern[str]:
    """A pattern matching *number* as a whole token in a description."""
    return re.compile(rf"(?<![\w-]){re.escape(number)}(?![\w-])")


def trade_dates(invoice: Invoice, pack: ExtractedPack, incorporated: date | None) -> TradeDates:
    """*invoice*'s trade: its matching purchase order, its earliest shipment
    (the rules check every bill of lading that references it, and a breach by
    any is one by the earliest) and the earliest credit whose description
    names it."""
    po = matching_po(invoice, pack)
    shipped = min((b.shipped_date for b in pack.bills_of_lading if b.invoice_reference == invoice.invoice_number),
                  default=None)
    named = names(invoice.invoice_number)
    credits = [txn.date for _, _, txn in bank_rows(pack) if txn.credit is not None and named.search(txn.description)]
    return TradeDates(purchase_order=po.po_date if po else None, shipped=shipped,
                      invoice=invoice.invoice_date, payment=min(credits, default=None), incorporated=incorporated)


# ---------------------------------------------------------------------------
# Pattern checks: (lead, pack, findings) -> Outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    """What code established about a lead's pattern, the facts it read and why."""

    status: EvidenceStatus
    reason: str
    facts: dict[str, str] = field(default_factory=dict)


def _unverified(reason: str, **facts: str) -> Outcome:
    return Outcome("unverified", reason, facts)


def _invoice(lead: Lead, pack: ExtractedPack) -> Invoice | None:
    return next((i for i in pack.invoices if i.invoice_number == lead.invoice_number), None)


_WORDS = re.compile(r"[A-Z0-9]+")


def _payer_reader(invoice_number: str) -> re.Pattern[str]:
    """Reads the payer from "<CHANNEL> CR <PAYER> <INVOICE NUMBER>[ <suffix>]"."""
    channels = "|".join(map(re.escape, PAYMENT_CHANNELS))
    return re.compile(rf"^(?:{channels}) CR (?P<payer>.+?) {re.escape(invoice_number)}(?: .*)?$", re.IGNORECASE)


_GENERIC_WORDS = frozenset(w for phrase in GENERIC_PAYERS for w in _WORDS.findall(phrase.upper()))


def _payer(description: str, reader: re.Pattern[str]) -> tuple[str | None, str]:
    """The payer a bank description names, or None with why it cannot be
    relied on: not in the known format, a generic payer (any word of one), or
    something with a digit in it (an amount, an account, another invoice)."""
    read = reader.match(description.strip())
    if read is None:
        return None, "the payer cannot be read from its description"
    payer = read["payer"].strip()
    words = _WORDS.findall(payer.upper())
    if any(w in _GENERIC_WORDS for w in words):
        return None, f"generic payer {payer}"
    if any(ch.isdigit() for ch in payer):
        return None, f"payer {payer} holds a number, not only a name"
    if len(re.findall(r"[A-Za-z]", payer)) < PAYER_MIN_LETTERS:
        return None, f"payer {payer} is too short to be a name"
    return payer, ""


@dataclass(frozen=True)
class Payment:
    """A credit that names an invoice, with who paid it. *relation* says
    whether the payer is the invoice's buyer, from the one similarity
    threshold pair; "unreadable" when the payer cannot be relied on (*why*)."""

    row_id: str
    transaction: Transaction
    payer: str | None
    relation: Literal["buyer", "other", "unclear", "unreadable"]
    why: str = ""


def payments(invoice: Invoice, pack: ExtractedPack) -> list[Payment]:
    """Every credit naming *invoice*, in bank order, with its payer read from
    the description and compared with the invoice's buyer."""
    named, reader = names(invoice.invoice_number), _payer_reader(invoice.invoice_number)
    found = []
    for row_id, _, txn in bank_rows(pack):
        if txn.credit is None or txn.credit <= 0 or not named.search(txn.description):
            continue
        payer, why = _payer(txn.description, reader)
        if payer is None:
            found.append(Payment(row_id, txn, None, "unreadable", why))
            continue
        score = similarity(payer, invoice.buyer_name)
        relation = ("buyer" if score >= PAYER_SAME_THRESHOLD
                    else "unclear" if score > PAYER_DIFFERENT_THRESHOLD else "other")
        found.append(Payment(row_id, txn, payer, relation))
    return found


_RELATION_TEXT = {"buyer": "the buyer", "other": "not the buyer", "unclear": "may or may not be the buyer"}


def payment_context(pack: ExtractedPack) -> str:
    """What code read of who paid each invoice, as lines for an agent's task:
    case data to investigate the meaning of, never a lead and never evidence
    an agent can cite (a result must be searched for to be cited)."""
    lines = []
    for invoice in pack.invoices:
        head = f"- {invoice.invoice_number}, buyer {invoice.buyer_name or 'not named'}: "
        credits = [f"{p.row_id} {p.transaction.date} credit {p.transaction.credit} "
                   + (f"from {p.payer} ({_RELATION_TEXT[p.relation]})" if p.payer
                      else f"(payer not relied on: {p.why})")
                   for p in payments(invoice, pack)]
        lines.append(head + ("; ".join(credits) or "no credit names it"))
    return "\n".join(lines) or "none"


def resolved_findings(pack: ExtractedPack, evidence: list[Evidence]) -> dict[str, str]:
    """Rule findings code can explain, by finding id and the reason: an
    invoice no single credit matched (XDOC_INVOICE_BANK) whose credits, all
    naming it, are all read as paid by its buyer within the rule's window and
    together make its total, within the rule's tolerance. Anything else (a
    payer who is not the buyer or cannot be read, a part outside the window,
    a sum short or over, a single credit) resolves nothing. The finding and
    the score stand; only what is asked about it changes. An agent's view
    never resolves one."""
    resolved = {}
    for finding in evidence:
        if finding.check_id != "XDOC_INVOICE_BANK" or finding.severity == "info":
            continue
        invoice = next((i for i in pack.invoices if i.doc_id == finding.document), None)
        if invoice is None:
            continue
        found = payments(invoice, pack)
        window_end = invoice.invoice_date + timedelta(days=MATCH_WINDOW_DAYS)
        paid = sum((p.transaction.credit or Decimal(0) for p in found), Decimal(0))
        if (len(found) >= 2 and all(p.relation == "buyer" for p in found)
                and all(invoice.invoice_date <= p.transaction.date <= window_end for p in found)
                and abs(paid - invoice.total) <= TOLERANCE_SGD):
            resolved[finding.evidence_id] = (
                f"{invoice.invoice_number} was paid in parts by its buyer {invoice.buyer_name}: "
                f"{', '.join(p.row_id for p in found)}, together {paid} of {invoice.total}")
    return resolved


def _third_party_payment(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """An invoice paid by someone other than its buyer. Every row of the lead
    is a credit naming the invoice, and every credit naming it has its payer
    read reliably: the case data decides, not the agent's choice of rows.
    Verified if any such credit is paid by someone else; contradicted only if
    every one is paid by the buyer; unverified otherwise."""
    invoice = _invoice(lead, pack)
    if invoice is None:
        return _unverified(f"invoice {lead.invoice_number} is not in the pack")
    if not invoice.buyer_name.strip():
        return _unverified(f"{invoice.invoice_number} names no buyer")
    rows = {row_id: txn for row_id, _, txn in bank_rows(pack)}
    named = names(invoice.invoice_number)
    facts = {"invoice": invoice.invoice_number, "buyer": invoice.buyer_name}
    for row_id in lead.transaction_ids:
        txn = rows.get(row_id)
        if txn is None:
            return _unverified(f"bank row {row_id} is not in the pack", **facts)
        if txn.credit is None or txn.credit <= 0:
            return _unverified(f"{row_id} is not a credit", **facts)
        if not named.search(txn.description):
            return _unverified(f"{row_id} does not name {invoice.invoice_number}", **facts)
    found = payments(invoice, pack)
    for payment in found:
        if payment.payer is None:
            return _unverified(f"{payment.row_id}: {payment.why}", **facts)
        if payment.relation == "unclear":
            return _unverified(f"{payment.row_id}: cannot tell whether payer {payment.payer} is the buyer",
                               **facts, payer=payment.payer)
    other = next((p for p in found if p.relation == "other"), None)
    if other is None:
        if not found:
            return _unverified("no credit names the invoice", **facts)
        return Outcome("contradicted", f"paid by the buyer {invoice.buyer_name}", _paid(facts, found[0]))
    return Outcome("verified", f"{other.row_id}: payer {other.payer} is not the buyer {invoice.buyer_name}",
                   _paid(facts, other))


def _paid(facts: dict[str, str], payment: Payment) -> dict[str, str]:
    """*facts* with the payment a check rests on: its payer, row, amount and date."""
    return {**facts, "payer": str(payment.payer), "transaction": payment.row_id,
            "amount": str(payment.transaction.credit), "date": str(payment.transaction.date)}


def _sequence_anomaly(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """The invoice's trade against the one ordering policy (gldb.ordering):
    a breach verifies (and names the rule finding that reports it), valid
    orderings contradict, an ordering with no policy stays unverified."""
    invoice = _invoice(lead, pack)
    if invoice is None:
        return _unverified(f"invoice {lead.invoice_number} is not in the pack")
    incorporated = known[0] if (known := incorporation(pack)) else None
    dates = trade_dates(invoice, pack, incorporated)
    facts = {k: str(v) for k, v in vars(dates).items() if v is not None}
    if found := violations(dates):
        breach = found[0]
        rule = next((e.evidence_id for e in evidence if e.check_id == breach.rule and e.severity != "info"
                     and e.document in _trade_documents(invoice, pack)), None)
        reason = f"{breach.later} dated before {breach.earlier}"
        return Outcome("verified", reason, {**facts, **({"rule_finding": rule} if rule else {})})
    if gaps := undetermined(dates):
        return _unverified(f"no policy for {gaps[0]}", **facts)
    if dates.purchase_order is None:
        return _unverified(f"no purchase order for {invoice.invoice_number}", **facts)
    return Outcome("contradicted", "every dated step is in a valid order", facts)


def _trade_documents(invoice: Invoice, pack: ExtractedPack) -> set[str]:
    """The documents of *invoice*'s trade: it, its purchase order, its bill of lading."""
    po = matching_po(invoice, pack)
    bls = {b.doc_id for b in pack.bills_of_lading if b.invoice_reference == invoice.invoice_number}
    return {invoice.doc_id, *bls, *((po.doc_id,) if po else ())}


def _applicant_people(pack: ExtractedPack) -> dict[str, str]:
    """The applicant's people by name: its ACRA officers, guarantors and signatories."""
    entity = pack.official.applicant() if pack.official else None
    people = {o.name: o.position.lower() for o in (entity.officers if entity else [])}
    if pack.manifest is not None:
        for name in pack.manifest.authorised_signatories:
            people.setdefault(name, "signatory")
        for guarantor in pack.manifest.guarantors:
            people.setdefault(guarantor.name, "guarantor")
    return people


def _related_party(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """A counterparty sharing an officer with the applicant: the counterparty
    resolved to exactly one ACRA record, its officers compared with the
    applicant's people."""
    name = counterparty_name(pack, lead.counterparty or "")
    if name is None:
        return _unverified(f"counterparty {lead.counterparty} is not in the pack")
    people = _applicant_people(pack)
    if not people:
        return _unverified("no application on file", counterparty=name)
    try:
        candidates = official.search(name)
    except official.OfficialRecordsError as exc:
        return _unverified(f"ACRA could not be searched: {exc}", counterparty=name)
    matches = [c for c in candidates if similarity(name, c.company_name) >= SCREENING_MATCH_THRESHOLD]
    if not matches:
        return _unverified(f"{name} is not in the company register", counterparty=name)
    if len(matches) > 1:
        return _unverified(f"{name} matches more than one register entry", counterparty=name)
    entity = matches[0]
    facts = {"counterparty": name, "register_uen": entity.uen}
    shared = next(((o.name, people[p]) for o in entity.officers for p in people
                   if similarity(o.name, p) >= SCREENING_MATCH_THRESHOLD), None)
    if shared is None:
        # "None in common" needs both lists: an applicant missing from the register, or an entity with no officers, is not that
        if not entity.officers or pack.official is None or pack.official.applicant() is None:
            return _unverified("the register does not list the officers of both sides", **facts)
        return Outcome("contradicted", f"no officer of {entity.company_name} is among the applicant's people", facts)
    officer, role = shared
    return Outcome("verified", f"{officer} is an officer of {entity.company_name} and the applicant's {role}",
                   {**facts, "shared_officer": officer, "applicant_role": role})


_CHECKS: dict[Pattern, Callable[[Lead, ExtractedPack, list[Evidence]], Outcome]] = {
    "third_party_payment": _third_party_payment,
    "sequence_anomaly": _sequence_anomaly,
    "related_party": _related_party,
}
#: Check id prefixes that read the same data as a pattern's check, so cannot corroborate it
_SAME_SOURCE: dict[Pattern, tuple[str, ...]] = {
    "third_party_payment": ("XDOC_INVOICE_BANK", "BANK_"),
    "sequence_anomaly": ("TEMP_",),
    "related_party": ("REGISTRY_", "SANCTIONS_"),
}


def check_pattern(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """What code establishes about *lead*'s pattern from the case data,
    whatever the agent concluded; unverified when there is no check for it."""
    check = _CHECKS.get(lead.pattern) if lead.kind == "pattern" and lead.pattern else None
    if check is None:
        return _unverified(f"no code check for {lead.pattern or 'triage'}")
    return check(lead, pack, evidence)


def _subject_documents(lead: Lead, pack: ExtractedPack) -> set[str]:
    """The documents a lead's subject is on, by the subject fields its pattern
    uses: others the agent may have set widen nothing."""
    subjects = PATTERN_SUBJECTS.get(lead.pattern, ()) if lead.pattern else ()
    invoice = _invoice(lead, pack) if "invoice_number" in subjects else None
    docs = _trade_documents(invoice, pack) if invoice else set()
    if "counterparty" in subjects and lead.counterparty:
        docs |= {d for d, n in _party_documents(pack) if counterparty_id(pack, n) == lead.counterparty}
    return docs


def corroborate(lead: Lead, outcome: Outcome, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """*outcome* raised to corroborated when it is verified and a risky rule
    finding from another data source is on the subject's documents. Only rule
    findings count, never another lead: two agents agreeing is one source. A
    rule-confirmed sequence is the rule's own finding, not corroboration."""
    if outcome.status != "verified" or lead.pattern is None or "rule_finding" in outcome.facts:
        return outcome
    docs, same = _subject_documents(lead, pack), _SAME_SOURCE.get(lead.pattern, ())
    other = next((e for e in evidence if e.severity != "info" and e.document in docs
                  and not e.check_id.startswith(same)), None)
    if other is None:
        return outcome
    return Outcome("corroborated", f"{outcome.reason}; and {other.check_id} ({other.evidence_id})",
                   {**outcome.facts, "corroborated_by": other.evidence_id})
