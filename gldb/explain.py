"""
gldb/explain.py
---------------
Optional plain-English case summary (AWS Bedrock via gldb.llm_client).

The LLM only narrates findings that deterministic code already produced; it
never sees the raw documents and never produces or changes the score. Its
output is accepted only if every claim cites a real evidence ID and every
number appears in the facts it was given. Otherwise a deterministic template
summary is returned instead.
"""

from __future__ import annotations

import json
import re

from gldb.llm_client import converse, prompt_version
from gldb.schemas import Evidence, RiskResult

_SYSTEM = (
    "You write a short plain-English summary of automated loan-document "
    "checks for a loan officer. Use only the facts in <facts>; add nothing. "
    "End every sentence with the evidence IDs it relies on in square "
    "brackets, e.g. [E01][E03]. Copy numbers exactly. Never approve, reject "
    "or recommend a decision about the loan, and never change the score. "
    "The summary is for a prototype: do not overstate certainty."
)

#: Content hash of the system prompt (see llm_client.prompt_version)
PROMPT_VERSION = prompt_version(_SYSTEM)

_CITATION = re.compile(r"\[(E\d+)\]")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _facts(evidence: list[Evidence], risk: RiskResult) -> str:
    """Findings as JSON. Document quotes are left out: they are untrusted."""
    findings = [
        {
            "id": ev.evidence_id,
            "category": ev.category,
            "severity": ev.severity,
            "document": ev.document,
            "expected": ev.expected,
            "observed": ev.observed,
            "explanation": ev.explanation,
        }
        for ev in evidence
        if ev.severity != "info"
    ]
    return json.dumps(
        {"score": risk.score, "band": risk.band, "findings": findings}, indent=1
    )


def _template(evidence: list[Evidence], risk: RiskResult) -> str:
    lines = [f"Risk score {risk.score} ({risk.band})."]
    lines += [
        f"{ev.explanation} [{ev.evidence_id}]"
        for ev in evidence
        if ev.severity != "info"
    ]
    return "\n".join(lines)


def _is_grounded(summary: str, facts: str, evidence: list[Evidence]) -> bool:
    """Every cited ID exists, every number is in the facts, and it cites something."""
    ids = {ev.evidence_id for ev in evidence}
    cited = _CITATION.findall(summary)
    if any(c not in ids for c in cited):
        return False
    if any(ev.severity != "info" for ev in evidence) and not cited:
        return False
    known = {n.rstrip(".").replace(",", "") for n in _NUMBER.findall(facts)}
    prose = _CITATION.sub("", summary)
    return all(
        n.rstrip(".").replace(",", "") in known for n in _NUMBER.findall(prose)
    )


def explain(evidence: list[Evidence], risk: RiskResult) -> str:
    """Return a cited plain-English summary, or the template if the LLM's fails the check."""
    facts = _facts(evidence, risk)
    result = converse(
        _SYSTEM, f"<facts>\n{facts}\n</facts>", purpose="explain_case"
    )
    summary = result.text.strip()
    if summary and _is_grounded(summary, facts, evidence):
        return summary
    return _template(evidence, risk)
