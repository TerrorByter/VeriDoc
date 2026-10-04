"""
gldb/ai_team/verify.py
----------------------
The deterministic gate on AI output. A lead is kept only if each of its tests
cites evidence its own agent received (results of its own successful tool
calls, never an error or a lead or question receipt, so hub-and-spoke is
enforced here too), it cites known findings, stays inside its specialist's
patterns, keeps to the lead contract (a supported lead shows a test whose
observation differs from what was expected; an unresolved one says what is
missing), neither accuses nor decides, and states only numbers and
identifiers found in what it rests on. An identifier (INV-1001, BL-3002,
202205551D) is a name, not a quantity: it may also come from the case index.
Its R, E and L ids are references. A memo point faces the same grounding
rules. The strength of a kept lead is assigned here too. Anything that fails
is withheld with a reason the analyst can read; nothing here asks a model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gldb.schemas import Evidence, Lead, ReviewPoint, SpecialistName, Strength

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: Result, finding and lead ids (R05, E04, L01): references, not numbers
_ID = re.compile(r"\b[REL]\d+\b")
#: Letters and digits joined by - _ /; one holding both is an identifier
_TOKEN = re.compile(r"[A-Za-z0-9]+(?:[-_/][A-Za-z0-9]+)*")
_LETTER = re.compile(r"[A-Za-z]")
_DIGIT = re.compile(r"\d")
_NOT_WORD = re.compile(r"[\W_]+")
#: Words that accuse or decide: a reviewer describes, people judge
VERDICT_WORDS = re.compile(
    r"\b(fraud\w*|crim\w*|launder\w*|guilt\w*|approv\w*|reject\w*|declin\w*|recommend\w*)\b",
    re.IGNORECASE,
)
#: The fraud patterns each specialist may raise
PATTERNS: dict[SpecialistName, frozenset[str]] = {
    "transaction": frozenset({"third_party_payment", "round_trip", "split_payment"}),
    "kyc": frozenset({"related_party", "business_mismatch"}),
    "forensics": frozenset({"document_inconsistency"}),
    "timeline": frozenset({"sequence_anomaly"}),
}


def _normalise(number: str) -> str:
    number = number.replace(",", "")
    return number.rstrip("0").rstrip(".") if "." in number else number


def _numbers(text: str) -> list[str]:
    """The numbers in the text, in order, normalised."""
    return [_normalise(n) for n in _NUMBER.findall(text)]


def numbers(text: str) -> set[str]:
    """The numbers in the text, normalised so "18,530.00" and "18530" match."""
    return set(_numbers(text))


def _is_identifier(token: str) -> bool:
    return _LETTER.search(token) is not None and _DIGIT.search(token) is not None


def _claims(text: str) -> tuple[list[str], list[str]]:
    """The identifiers and the numbers an AI's text states, in order, its R,
    E and L ids left out. A number inside an identifier is part of its name."""
    identifiers: list[str] = []

    def take(match: re.Match[str]) -> str:
        if not _is_identifier(token := match.group(0)):
            return token
        identifiers.append(token)
        return " "

    rest = _TOKEN.sub(take, _ID.sub(" ", text))
    return identifiers, _numbers(rest)


@dataclass(frozen=True)
class Known:
    """What a text may state: the numbers and (casefolded) identifiers found
    in what it rests on."""

    numbers: frozenset[str]
    identifiers: frozenset[str]

    def __or__(self, other: Known) -> Known:
        return Known(self.numbers | other.numbers, self.identifiers | other.identifiers)


def known(*texts: str) -> Known:
    """The numbers and identifiers in *texts*."""
    text = " ".join(texts)
    return Known(frozenset(numbers(text)),
                 frozenset(t.casefold() for t in _TOKEN.findall(text) if _is_identifier(t)))


def _identifiers_only(grounding: Known) -> Known:
    """The identifiers of *grounding*: a case's names, never its quantities."""
    return Known(frozenset(), grounding.identifiers)


def _cite_reason(cites: list[str], issued: set[str], citable: set[str]) -> str | None:
    """Why *cites* are not grounded: none, one the agent did not receive
    (*issued*), or one that is not evidence (outside *citable*)."""
    if not cites:
        return "no citations"
    if (unreceived := next((c for c in cites if c not in issued), None)) is not None:
        return f"cites a tool result it did not receive: {unreceived}"
    if (receipt := next((c for c in cites if c not in citable), None)) is not None:
        return f"cites a result that is not evidence: {receipt}"
    return None


def _wording_reason(text: str, grounding: Known) -> str | None:
    """Why *text* is withheld for verdict words, or for an identifier or a
    number outside *grounding*, or None."""
    if word := VERDICT_WORDS.search(text):
        return f"uses verdict language: {word.group(1).lower()}"
    identifiers, stated = _claims(text)
    if stray := next((i for i in identifiers if i.casefold() not in grounding.identifiers), None):
        return f"uses an identifier not in its cited results: {stray}"
    if stray := next((n for n in stated if n not in grounding.numbers), None):
        return f"uses a number not in its cited results: {stray}"
    return None


def _same(expected: str, observed: str) -> bool:
    """Whether two values say the same, ignoring case, spacing and punctuation."""
    return _NOT_WORD.sub(" ", expected).casefold().strip() == _NOT_WORD.sub(" ", observed).casefold().strip()


def _contract_reason(lead: Lead, findings: dict[str, Evidence], issued: set[str], citable: set[str]) -> str | None:
    """Why *lead* breaks the lead contract (tests, citations, findings,
    pattern, status), or None."""
    if not lead.tests:
        return "no tests"
    if any(not test.cites for test in lead.tests):
        return "a test without citations"
    if reason := _cite_reason(lead.cites, issued, citable):
        return reason
    if (unknown := next((f for f in lead.finding_ids if f not in findings), None)) is not None:
        return f"cites an unknown finding: {unknown}"
    if lead.kind == "triage" and not lead.finding_ids:
        return "triage lead without a finding"
    if lead.kind == "pattern" and lead.pattern is None:
        return "a pattern lead needs its pattern"
    if (lead.kind == "pattern" or lead.pattern is not None) and lead.pattern not in PATTERNS[lead.agent]:
        return f"pattern not in this specialist's list: {lead.pattern}"
    if lead.status == "supported" and all(_same(t.expected, t.observed) for t in lead.tests):
        return "supported without a test whose observed differs from its expected"
    if lead.status == "unresolved" and not lead.missing_evidence:
        return "unresolved without missing evidence"
    return None


def check_lead(lead: Lead, issued: set[str], citable: set[str], results: dict[str, str],
               findings: dict[str, Evidence], index: str) -> str | None:
    """Why the lead is withheld, or None to keep it. `issued` are the tool
    results its agent received, `citable` those that are evidence (see
    Ledger.citable), `results` their text, `findings` the case's and `index`
    the case index its agent was given (its identifiers may be named)."""
    if reason := _contract_reason(lead, findings, issued, citable):
        return reason
    judged = known(*(s for f in lead.finding_ids
                     for s in (findings[f].explanation, findings[f].expected, findings[f].observed) if s))
    base = judged | _identifiers_only(known(index))
    everything = base | known(*(results.get(c, "") for c in lead.cites))
    # Each test states only what its own citations show; the rest may draw on all of them
    texts = [(lead.hypothesis, everything),
             *((f"{t.expected} {t.observed}", base | known(*(results.get(c, "") for c in t.cites)))
               for t in lead.tests),
             *((m, everything) for m in lead.missing_evidence)]
    return next((r for text, grounding in texts if (r := _wording_reason(text, grounding))), None)


def strength(lead: Lead) -> Strength | None:
    """How strong a kept, supported lead's evidence is, by rule: a triage lead
    backs a rule finding with AI evidence (corroborated); a pattern lead has
    no code check of its pattern yet (unverified). Refuted, unresolved and
    withheld leads have none."""
    if lead.outcome != "kept" or lead.status != "supported":
        return None
    return "corroborated" if lead.kind == "triage" else "unverified"


def check_question(text: str, cites: list[str], issued: set[str], citable: set[str],
                   results: dict[str, str], index: str) -> str | None:
    """Why a drafted question is withheld, or None to keep it: the same
    citation, wording, identifier and number rules as a lead."""
    grounding = known(*(results.get(c, "") for c in cites)) | _identifiers_only(known(index))
    return _cite_reason(cites, issued, citable) or _wording_reason(text, grounding)


def check_point(point: ReviewPoint, known_ids: set[str], grounding: Known) -> bool:
    """Whether the point cites only known findings or leads, states only the
    numbers and identifiers of *grounding* and neither accuses nor decides."""
    return (bool(point.evidence_ids) and set(point.evidence_ids) <= known_ids
            and _wording_reason(point.text, grounding) is None)
