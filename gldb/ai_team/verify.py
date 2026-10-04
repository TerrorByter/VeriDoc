"""
gldb/ai_team/verify.py
----------------------
The deterministic gate on AI output. A lead is kept only if it cites tool
results its own agent received (so hub-and-spoke is enforced here too), cites
known findings, stays inside its specialist's patterns, neither accuses nor
decides, and uses only numbers found in what it cited. A memo point faces the
same grounding rules. Anything that fails is withheld with a reason the
analyst can read; nothing here asks a model.
"""

from __future__ import annotations

import re

from gldb.schemas import AgentName, Evidence, Lead, ReviewPoint, SpecialistName

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
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


def _cite_reason(cites: list[str], issued: set[str]) -> str | None:
    if not cites:
        return "no citations"
    if (unreceived := next((c for c in cites if c not in issued), None)) is not None:
        return f"cites a tool result it did not receive: {unreceived}"
    return None


def _wording_reason(text: str, cited: list[str]) -> str | None:
    """Why *text* is withheld for verdict words or for a number found in none
    of the *cited* texts, or None."""
    if word := VERDICT_WORDS.search(text):
        return f"uses verdict language: {word.group(1).lower()}"
    known = numbers(" ".join(cited))
    if stray := next((n for n in _numbers(text) if n not in known), None):
        return f"uses a number not in its cited results: {stray}"
    return None


def check_lead(lead: Lead, issued: set[str], results: dict[str, str],
               findings: dict[str, Evidence]) -> str | None:
    """Why the lead is withheld, or None to keep it. `issued` are the tool
    results its agent received, `results` their text, `findings` the case's."""
    if reason := _cite_reason(lead.cites, issued):
        return reason
    if (unknown := next((f for f in lead.finding_ids if f not in findings), None)) is not None:
        return f"cites an unknown finding: {unknown}"
    if lead.kind == "triage" and not lead.finding_ids and lead.verdict is None:
        return "triage lead without a finding or verdict"
    if (lead.kind == "pattern" or lead.pattern is not None) and lead.pattern not in PATTERNS[lead.agent]:
        return f"pattern not in this specialist's list: {lead.pattern}"
    cited = [results.get(c, "") for c in lead.cites]
    for finding_id in lead.finding_ids:
        finding = findings[finding_id]
        cited += [s for s in (finding.explanation, finding.expected, finding.observed) if s]
    return _wording_reason(f"{lead.title} {lead.detail}", cited)


def check_question(agent: AgentName, text: str, cites: list[str], issued: set[str],
                   results: dict[str, str]) -> str | None:
    """Why *agent*'s drafted question is withheld, or None to keep it: the
    same citation, wording and number rules as a lead."""
    return _cite_reason(cites, issued) or _wording_reason(text, [results.get(c, "") for c in cites])


def check_point(point: ReviewPoint, known_ids: set[str], known_numbers: set[str]) -> bool:
    """Whether the point cites only known findings or leads, uses only known
    numbers and neither accuses nor decides."""
    return (bool(point.evidence_ids) and set(point.evidence_ids) <= known_ids
            and not VERDICT_WORDS.search(point.text) and numbers(point.text) <= known_numbers)
