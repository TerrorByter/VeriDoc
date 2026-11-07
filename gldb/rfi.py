"""
gldb/rfi.py
-----------
A request for information (RFI) drafted from a case, for the analyst to edit
and send through the bank's own channel. Nothing here sends anything.

Two rules hold the draft to what a bank may put in a letter to a borrower:

- Facts come from code, never from AI prose: each item is the applicant-facing
  request line of a rule finding's check, quoting only that finding's own
  expected and observed values. Verified AI questions (which passed the lead
  verifier) may join, if they too are applicant-safe.
- No tipping-off: a borrower is never told it was screened or is suspected.
  Sanctions and high-risk-country hits, forensic and metadata signals, prompt
  attacks, money-laundering typologies and altered official records have no
  request line, so they never reach a draft (they are counted as withheld),
  and nothing a draft holds may name screening, suspicion or a score.
"""

from __future__ import annotations

import re
from string import Formatter

from pydantic import BaseModel

from gldb.checks import PACK_DOC
from gldb.schemas import CaseResult, ReviewPoint

#: What the bank asks the applicant for, by check: (request, detail). The
#: detail is a sentence of fact that may quote the finding's {expected} and
#: {observed}; it is left out when the finding lacks a value it quotes. A
#: check missing here is never asked about.
REQUESTS: dict[str, tuple[str, str | None]] = {
    "INV_ARITH": ("Please confirm the line amounts and the total of this invoice, or provide a corrected copy "
                  "issued by the seller.", "Its amounts do not add up as printed."),
    "FS_BALANCE": ("Please provide the balance sheet as signed by your directors or as audited.",
                   "Its total assets do not equal its total liabilities and equity."),
    "BANK_RUNNING_BAL": ("Please arrange for this statement to be sent to us directly by your bank, or provide a "
                         "bank-certified copy.",
                         "A running balance does not reconcile: we calculate {expected}, the statement shows "
                         "{observed}."),
    "BANK_CONTINUITY": ("Please provide bank-certified statements for the whole period.",
                        "The opening balance of one statement does not match the closing balance of the one "
                        "before it."),
    "FS_ACRA_MISMATCH": ("Please provide the financial statements as filed with ACRA, and explain any difference "
                         "from those submitted.", None),
    "XDOC_BANK_CASH": ("Please reconcile the opening balance of this account with the cash shown on your balance "
                       "sheet.", None),
    "XDOC_BANK_DEBT": ("Please list the loans and borrowings these repayments relate to, with their facility "
                       "letters.", None),
    "XDOC_BANK_REVENUE": ("Please explain how your reported revenue compares with the receipts in your bank "
                          "statements, with a breakdown of receipts by customer.", None),
    "XDOC_INVOICE_BANK": ("Please provide the bank's credit advice or the customer's remittance advice for its "
                          "payment.", "We could not match it to a bank credit (we looked for a {expected})."),
    "XDOC_THIRD_PARTY_PAYER": ("Please tell us who made the payment for this invoice and why it paid on the "
                               "buyer's behalf, with any agreement under which it did.",
                               "We see {observed}, where we expected {expected}."),
    "XDOC_PO_INVOICE": ("Please provide the purchase order for this invoice.", None),
    "XDOC_BL_INVOICE": ("Please provide the shipping documents for the full quantity invoiced, or a corrected "
                        "invoice.", None),
    "XDOC_IDENTITY": ("Please confirm your company's legal name, UEN and registered address, as they differ "
                      "between documents.", None),
    "REGISTRY_ENTITY_NOT_FOUND": ("Please provide the company registration details of the other party to this "
                                  "document.", None),
    "REGISTRY_NAME_MISMATCH": ("Please confirm the registered name of the other party to this document, as it "
                               "differs from the name used.", None),
    "REGISTRY_ENTITY_INACTIVE": ("Please explain your dealings with the other party to this document, which is "
                                 "not live in the company register.", None),
    "REGISTRY_SIGNATORY_NOT_OFFICER": ("Please provide the board resolution or mandate authorising your "
                                       "signatories.", None),
    "XDOC_ACRA_ENTITY": ("Please check the company details on this form against your ACRA business profile, "
                         "and submit a corrected form if needed.", None),
    "XDOC_ACRA_SIGNATORY": ("Please provide the board resolution or mandate authorising the signatory of this "
                            "form.", None),
    "XDOC_ACRA_PERSON": ("Please confirm this person's name and address, with identification if either has "
                         "changed.", None),
    "TEMP_BEFORE_INCORPORATION": ("Please explain the date of this document, which is before your company was "
                                  "incorporated.", None),
    "TEMP_TRADE_SEQUENCE": ("Please provide the purchase order for this trade as originally issued, and explain "
                            "the order of its documents.", None),
    "BANK_COVERAGE": ("Please provide bank statements covering the full required period.", "{observed}."),
    "CHECKLIST_MISSING_DOC": ("Please provide this document.", "Missing: {expected}."),
    **{check: ("Please complete or correct the FATCA/CRS self-certification form and sign it again.", None)
       for check in ("FORM_ENT_MANDATORY", "FORM_ENT_MAILING", "FORM_ENT_BRANCH", "FORM_ENT_US_STATUS",
                     "FORM_ENT_TIN_OR_REASON", "FORM_ENT_TYPE_SELECTION", "FORM_ENT_DECLARATION",
                     "FORM_ENT_NAME_MATCH", "FORM_ENT_ADDRESS_MATCH", "FORM_ENT_INCORPORATION", "FORM_ENT_SG_TIN",
                     "FORM_ENT_START_UP", "FORM_ENT_SIGNATORY")},
    "FORM_TEMPLATE_MATCH": ("Please complete the bank's current FATCA/CRS self-certification form.", None),
}

#: Words a letter to a borrower must never hold: screening, suspicion, scoring, internal terms
UNSAFE = re.compile(
    r"\b(sanction\w*|watch ?list\w*|fatf|high[- ]risk|risk\w*|launder\w*|fraud\w*|tamper\w*|forg\w*|suspic\w*|"
    r"screen\w*|score\w*|mlro|str|aml|cft|flag\w*|alert\w*|injection|pass[- ]through|hidden text)\b",
    re.IGNORECASE,
)
#: Internal ids (findings E01, leads L01, results R01, bank rows T01, parties P01): never in a letter
_INTERNAL_ID = re.compile(r"\b[ELRTP]\d{2,}\b")


class RequestItem(BaseModel):
    """One thing the applicant is asked for, on one document."""

    #: The findings behind it: for the analyst, never in the letter
    evidence_ids: list[str]
    #: The document as the letter names it, with its page
    document: str
    text: str


class RequestDraft(BaseModel):
    items: list[RequestItem]
    #: Verified AI questions that are applicant-safe
    questions: list[str]
    #: Open findings kept out of the draft: never asked about (screening, forensics, AML)
    withheld: int
    #: The letter, ready to edit; empty when there is nothing to ask
    text: str


def _detail(template: str | None, expected: str | None, observed: str | None) -> str | None:
    """*template* with the finding's values, or None when it quotes a value the finding lacks."""
    if template is None:
        return None
    values = {"expected": expected, "observed": observed}
    used = {field for _, field, _, _ in Formatter().parse(template) if field}
    if any(values.get(field) is None for field in used):
        return None
    return template.format(**values)


def _items(result: CaseResult) -> tuple[list[RequestItem], int]:
    """One item per request on each document, in finding order, quoting its
    first finding (repeats of a check, or form errors on one form, ask once,
    as they score once), and how many open findings have no request.
    Information-only findings are not asked about."""
    names = {d.doc_id: d.filename for d in result.pack.docs}
    items: dict[tuple[str, str], RequestItem] = {}
    withheld = 0
    for ev in result.evidence:
        if ev.severity == "info":
            continue
        if (entry := REQUESTS.get(ev.check_id)) is None:
            withheld += 1
            continue
        request, template = entry
        if (item := items.get((ev.document, request))) is not None:
            item.evidence_ids.append(ev.evidence_id)
            continue
        detail = _detail(template, ev.expected, ev.observed)
        where = "Your application" if ev.document == PACK_DOC else f"{names.get(ev.document, ev.document)}, page {ev.page}"
        items[(ev.document, request)] = RequestItem(evidence_ids=[ev.evidence_id], document=where,
                                                    text=f"{detail} {request}" if detail else request)
    for item in items.values():
        if (more := len(item.evidence_ids) - 1) > 0:
            item.text = f"{item.text} The same applies to {more} more {'entry' if more == 1 else 'entries'} on this document."
    return list(items.values()), withheld


def asked_findings(result: CaseResult) -> set[str]:
    """The findings the request already asks the applicant about."""
    return {evidence_id for item in _items(result)[0] for evidence_id in item.evidence_ids}


def adds_to_request(question: ReviewPoint, asked: set[str]) -> bool:
    """Whether an AI question asks something new: it rests on more than
    findings the request already asks about."""
    return not question.evidence_ids or not set(question.evidence_ids) <= asked


def draft_request(result: CaseResult) -> RequestDraft:
    """The RFI for *result*: the request items (_items), then the verified AI
    questions that are applicant-safe and ask something the items do not."""
    items, withheld = _items(result)
    asked = {evidence_id for item in items for evidence_id in item.evidence_ids}
    memo = result.ai_team.memo if result.ai_team else None
    questions = [q.text for q in (memo.questions if memo else [])
                 if adds_to_request(q, asked) and not UNSAFE.search(q.text) and not _INTERNAL_ID.search(q.text)]
    return RequestDraft(items=items, questions=questions, withheld=withheld, text=_letter(result, items, questions))


def _letter(result: CaseResult, items: list[RequestItem], questions: list[str]) -> str:
    """The draft letter, plain text so it pastes cleanly into an email."""
    if not items and not questions:
        return ""
    company = result.pack.manifest.company_name if result.pack.manifest else None
    lines = [f"Dear {company or 'Sir or Madam'},", "",
             "Thank you for your application. To continue our review, please provide the following:", ""]
    lines += [f"{n}. {item.document}: {item.text}" for n, item in enumerate(items, 1)]
    if questions:
        lines += ["", "Please also answer the following:", *(f"- {q}" for q in questions)]
    lines += ["", "We will continue our review once we receive these.", "", "Yours sincerely,"]
    return "\n".join(lines)
