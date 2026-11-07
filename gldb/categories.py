"""
gldb/categories.py
------------------
What kind of problem a case shows, set by code from what was found: each
rule finding and each lead code verified names its category. A case can carry
several; none means no issues were found. No model chooses a category.

A check missing from CHECK_CATEGORY (one added after this map) still lands
somewhere: in "Other" under its signal family, so nothing found goes
unlabelled. A fraud no check or verified lead detects has no category; that
gap is the analyst's and the AI team's memo's to raise, not this module's.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from gldb.ai_team.team import flagged
from gldb.schemas import CaseResult, Evidence, Pattern, SignalFamily
from gldb.signal_library import SIGNALS

CategoryId = Literal[
    "tampered_document", "prompt_attack", "trade_mismatch", "financial_misstatement",
    "identity_registry", "sanctions_aml", "timeline", "third_party_payment", "round_trip",
    "related_party", "business_mismatch", "incomplete", "checks_not_run", "other",
]

#: How each category reads in the UI, in display order (most serious first)
LABEL: dict[CategoryId, str] = {
    "sanctions_aml": "Sanctions / AML",
    "tampered_document": "Tampered document",
    "prompt_attack": "Prompt attack",
    "financial_misstatement": "Financial misstatement",
    "trade_mismatch": "Trade document mismatch",
    "identity_registry": "Identity / registry",
    "third_party_payment": "Third-party payment",
    "round_trip": "Round trip",
    "related_party": "Undisclosed related party",
    "business_mismatch": "Business activity mismatch",
    "timeline": "Timeline anomaly",
    "other": "Other",
    "incomplete": "Incomplete application",
    "checks_not_run": "Checks not run",
}

#: Every check in the signal library, by the category its finding shows
CHECK_CATEGORY: dict[str, CategoryId] = {
    # Documents altered after issue
    "FORENSIC_HIDDEN_TEXT": "tampered_document",
    "FORENSIC_MIXED_FONT": "tampered_document",
    "FORENSIC_INCREMENTAL_UPDATE": "tampered_document",
    "FORENSIC_OCR_MISMATCH": "tampered_document",
    "META_MOD_AFTER_CREATE": "tampered_document",
    "META_PRODUCER_MISMATCH": "tampered_document",
    "BANK_RUNNING_BAL": "tampered_document",
    "BANK_CONTINUITY": "tampered_document",
    "XDOC_OFFICIAL_RECORD": "tampered_document",
    "PROMPT_INJECTION": "prompt_attack",
    # Trade paperwork that does not agree with itself or the bank
    "INV_ARITH": "trade_mismatch",
    "XDOC_INVOICE_BANK": "trade_mismatch",
    "XDOC_PO_INVOICE": "trade_mismatch",
    "XDOC_BL_INVOICE": "trade_mismatch",
    # Who paid for a sale
    "XDOC_THIRD_PARTY_PAYER": "third_party_payment",
    # Financial statements that do not hold up
    "FS_BALANCE": "financial_misstatement",
    "FS_ACRA_MISMATCH": "financial_misstatement",
    "XDOC_BANK_CASH": "financial_misstatement",
    "XDOC_BANK_DEBT": "financial_misstatement",
    "XDOC_BANK_REVENUE": "financial_misstatement",
    # Who the applicant is
    "XDOC_IDENTITY": "identity_registry",
    "REGISTRY_ENTITY_NOT_FOUND": "identity_registry",
    "REGISTRY_NAME_MISMATCH": "identity_registry",
    "REGISTRY_ENTITY_INACTIVE": "identity_registry",
    "REGISTRY_SIGNATORY_NOT_OFFICER": "identity_registry",
    "XDOC_ACRA_ENTITY": "identity_registry",
    "XDOC_ACRA_SIGNATORY": "identity_registry",
    "XDOC_ACRA_PERSON": "identity_registry",
    # Screening and money movement
    "SANCTIONS_MATCH": "sanctions_aml",
    "HIGH_RISK_COUNTRY": "sanctions_aml",
    "BANK_PASS_THROUGH": "sanctions_aml",
    # Dates
    "TEMP_BEFORE_INCORPORATION": "timeline",
    "TEMP_TRADE_SEQUENCE": "timeline",
    # What the application is missing or gets wrong on the forms
    "BANK_COVERAGE": "incomplete",
    "CHECKLIST_MISSING_DOC": "incomplete",
    "MANIFEST_MISSING": "incomplete",
    "FORM_ENT_MANDATORY": "incomplete",
    "FORM_ENT_MAILING": "incomplete",
    "FORM_ENT_BRANCH": "incomplete",
    "FORM_ENT_US_STATUS": "incomplete",
    "FORM_ENT_TIN_OR_REASON": "incomplete",
    "FORM_ENT_TYPE_SELECTION": "incomplete",
    "FORM_ENT_DECLARATION": "incomplete",
    "FORM_TEMPLATE_MATCH": "incomplete",
    "FORM_ENT_NAME_MATCH": "incomplete",
    "FORM_ENT_ADDRESS_MATCH": "incomplete",
    "FORM_ENT_INCORPORATION": "incomplete",
    "FORM_ENT_SG_TIN": "incomplete",
    "FORM_ENT_START_UP": "incomplete",
    "FORM_ENT_SIGNATORY": "incomplete",
    # The system could not run a check
    "FORENSIC_OCR_SKIPPED": "checks_not_run",
    "PROMPT_SCREEN_SKIPPED": "checks_not_run",
    "OFFICIAL_RECORDS_UNAVAILABLE": "checks_not_run",
    "SYSTEM_CHECK_ERROR": "checks_not_run",
}

#: Each pattern a verified lead can carry. split_payment is an ordinary way to
#: pay, and a document_inconsistency or sequence_anomaly lead restates what
#: the forensics and timeline checks report, so those take the rule category.
PATTERN_CATEGORY: dict[Pattern, CategoryId] = {
    "third_party_payment": "third_party_payment",
    "round_trip": "round_trip",
    "related_party": "related_party",
    "business_mismatch": "business_mismatch",
    "document_inconsistency": "tampered_document",
    "sequence_anomaly": "timeline",
    "split_payment": "other",
}

#: A check missing from CHECK_CATEGORY is shown under its family, as "Other: ..."
FAMILY_LABEL: dict[SignalFamily, str] = {
    "DOC": "document integrity", "FIN": "financial", "TXN": "transactions", "REG": "registry",
    "ENT": "relationships", "TEMP": "timeline", "COMP": "compliance", "SYS": "system",
}
_FAMILY = {s.check_id: s.family for s in SIGNALS}


class CaseCategory(BaseModel):
    """One kind of problem the case shows, and what shows it."""

    category: CategoryId
    #: As shown; "Other: <family>" for a check not yet in CHECK_CATEGORY
    label: str
    #: rules: rule findings; ai_team: leads the AI team raised and code verified
    source: Literal["rules", "ai_team"]
    #: The findings (E…) or leads (L…) behind it
    ids: list[str]


def _of_finding(e: Evidence) -> tuple[CategoryId, str]:
    if (category := CHECK_CATEGORY.get(e.check_id)) is not None:
        return category, LABEL[category]
    family = _FAMILY.get(e.check_id)
    return "other", f"Other: {FAMILY_LABEL[family]}" if family else LABEL["other"]


def categorise(result: CaseResult) -> list[CaseCategory]:
    """The categories *result* shows, most serious first: one per kind of
    problem from its findings (info findings say nothing is wrong, so they
    are left out), then one per kind of verified AI-team lead. Empty when
    nothing was found."""
    found: dict[tuple[str, str], CaseCategory] = {}

    def add(category: CategoryId, label: str, source: Literal["rules", "ai_team"], item_id: str) -> None:
        entry = found.setdefault((label, source), CaseCategory(category=category, label=label, source=source, ids=[]))
        entry.ids.append(item_id)

    for e in result.evidence:
        if e.severity != "info":
            add(*_of_finding(e), "rules", e.evidence_id)
    for lead in result.ai_team.leads if result.ai_team else []:
        if lead.pattern is not None and flagged(lead):
            category = PATTERN_CATEGORY[lead.pattern]
            add(category, LABEL[category], "ai_team", lead.lead_id)

    order = list(LABEL)
    return sorted(found.values(), key=lambda c: (order.index(c.category), c.source, c.label))
