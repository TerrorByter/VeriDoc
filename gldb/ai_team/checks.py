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
from datetime import date

from gldb.checks import incorporation, matching_po
from gldb.config import (
    GENERIC_PAYERS,
    PAYER_DIFFERENT_THRESHOLD,
    PAYER_MIN_LETTERS,
    PAYER_SAME_THRESHOLD,
    PAYMENT_CHANNELS,
    SCREENING_MATCH_THRESHOLD,
)
from gldb.ai_team.verify import PATTERN_SUBJECTS
from gldb.ordering import TradeDates, undetermined, violations
from gldb.reference import registry
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


def _third_party_payment(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """An invoice paid by someone other than its buyer. Every row of the lead
    is a credit naming the invoice with its payer read reliably, and so are the
    other credits that name it: the case data decides, not the agent's choice
    of rows. Verified if any such credit is paid by someone else; contradicted
    only if every one is paid by the buyer; unverified otherwise."""
    invoice = _invoice(lead, pack)
    if invoice is None:
        return _unverified(f"invoice {lead.invoice_number} is not in the pack")
    if not invoice.buyer_name.strip():
        return _unverified(f"{invoice.invoice_number} names no buyer")
    rows = {row_id: txn for row_id, _, txn in bank_rows(pack)}
    named, reader = names(invoice.invoice_number), _payer_reader(invoice.invoice_number)
    facts = {"invoice": invoice.invoice_number, "buyer": invoice.buyer_name}
    for row_id in lead.transaction_ids:
        txn = rows.get(row_id)
        if txn is None:
            return _unverified(f"bank row {row_id} is not in the pack", **facts)
        if txn.credit is None or txn.credit <= 0:
            return _unverified(f"{row_id} is not a credit", **facts)
        if not named.search(txn.description):
            return _unverified(f"{row_id} does not name {invoice.invoice_number}", **facts)
    # The lead's rows, then every other credit that names the invoice
    credits = [(row_id, txn) for row_id, txn in rows.items()
               if row_id in lead.transaction_ids
               or (txn.credit is not None and txn.credit > 0 and named.search(txn.description))]
    different: tuple[str, str, Transaction] | None = None
    same: tuple[str, str, Transaction] | None = None
    for row_id, txn in credits:
        payer, why = _payer(txn.description, reader)
        if payer is None:
            return _unverified(f"{row_id}: {why}", **facts)
        score = similarity(payer, invoice.buyer_name)
        if PAYER_DIFFERENT_THRESHOLD < score < PAYER_SAME_THRESHOLD:
            return _unverified(f"{row_id}: cannot tell whether payer {payer} is the buyer", **facts, payer=payer)
        if score >= PAYER_SAME_THRESHOLD:
            same = same or (row_id, payer, txn)
        else:
            different = different or (row_id, payer, txn)
    if different is None:
        if same is None:
            return _unverified("no credit names the invoice", **facts)
        row_id, payer, txn = same
        return Outcome("contradicted", f"paid by the buyer {invoice.buyer_name}",
                       {**facts, "payer": payer, "transaction": row_id, "amount": str(txn.credit), "date": str(txn.date)})
    row_id, payer, txn = different
    return Outcome("verified", f"{row_id}: payer {payer} is not the buyer {invoice.buyer_name}",
                   {**facts, "payer": payer, "transaction": row_id, "amount": str(txn.credit),
                    "date": str(txn.date)})


def _sequence_anomaly(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """The invoice's trade against the one ordering policy (gldb.ordering):
    a breach verifies (and names the rule finding that reports it), valid
    orderings contradict, an ordering with no policy stays unverified."""
    invoice = _invoice(lead, pack)
    if invoice is None:
        return _unverified(f"invoice {lead.invoice_number} is not in the pack")
    incorporated = incorporation(pack.manifest)[0] if pack.manifest else None
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
    """The applicant's people by name: its register officers, guarantors and signatories."""
    manifest = pack.manifest
    if manifest is None:
        return {}
    entity = registry().entities.get(manifest.uen)
    people = {o.name: o.role.lower() for o in (entity.officers if entity else [])}
    for name in manifest.authorised_signatories:
        people.setdefault(name, "signatory")
    for guarantor in manifest.guarantors:
        people.setdefault(guarantor.name, "guarantor")
    return people


def _related_party(lead: Lead, pack: ExtractedPack, evidence: list[Evidence]) -> Outcome:
    """A counterparty sharing an officer with the applicant: the counterparty
    resolved to exactly one register entry, its officers compared with the
    applicant's people."""
    name = counterparty_name(pack, lead.counterparty or "")
    if name is None:
        return _unverified(f"counterparty {lead.counterparty} is not in the pack")
    people = _applicant_people(pack)
    if not people:
        return _unverified("no application on file", counterparty=name)
    matches = [(uen, e) for uen, e in registry().entities.items() if similarity(name, e.name) >= SCREENING_MATCH_THRESHOLD]
    if not matches:
        return _unverified(f"{name} is not in the company register", counterparty=name)
    if len(matches) > 1:
        return _unverified(f"{name} matches more than one register entry", counterparty=name)
    uen, entity = matches[0]
    facts = {"counterparty": name, "register_uen": uen}
    shared = next(((o.name, people[p]) for o in entity.officers for p in people
                   if similarity(o.name, p) >= SCREENING_MATCH_THRESHOLD), None)
    if shared is None:
        # "None in common" needs both lists: an applicant missing from the register, or an entity with no officers, is not that
        if not entity.officers or registry().entities.get(pack.manifest.uen if pack.manifest else "") is None:
            return _unverified("the register does not list the officers of both sides", **facts)
        return Outcome("contradicted", f"no officer of {entity.name} is among the applicant's people", facts)
    officer, role = shared
    return Outcome("verified", f"{officer} is an officer of {entity.name} and the applicant's {role}",
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
