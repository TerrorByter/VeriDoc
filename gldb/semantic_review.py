"""
gldb/semantic_review.py
-----------------------
The Semantic Review Agent: given the verified findings, the transaction
chain breaks and the date anomalies, does the applicant's story hold
together, and what should the analyst ask?

LLM reasoning where it adds value, synthesis, inside hard limits:
- It never sees the documents: only structured facts that deterministic code
  produced. Document quotes are left out (untrusted text), so instructions
  hidden in a document cannot reach it.
- Every observation and question must cite findings, use only numbers that
  appear in the facts, and neither accuse nor decide. A point that fails is
  dropped and counted; if none survive, a deterministic template is used.
- It never scores, routes or decides. AI investigates, rules quantify,
  humans decide.
"""

from __future__ import annotations

import json
import re
from typing import Any

from gldb.llm_client import converse, prompt_version
from gldb.schemas import (
    Evidence,
    ReviewPoint,
    SemanticReview,
    TimelineEvent,
    TransactionChain,
)

_SYSTEM = (
    "You review a loan application's documents for a bank analyst. You get "
    "only facts already verified by deterministic checks, in <facts>: findings, "
    "breaks in invoice -> order -> shipment -> payment chains, and date "
    "anomalies. Say where the documents tell one consistent story and where "
    "they contradict each other, connecting facts across documents (for "
    "example an invoice whose shipment and payment do not match it). Then list "
    "questions the analyst could put to the applicant or a counterparty. Rules: "
    "use only the facts given; cite the finding IDs each point relies on; copy "
    "numbers exactly and do not calculate new ones; describe inconsistencies, "
    "never intent: do not call anything fraud or crime, and never approve, "
    "decline or recommend a decision. Be brief: at most 5 observations and 5 "
    "questions."
)

_POINTS = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["text", "evidence_ids"],
    },
}
_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"observations": _POINTS, "questions": _POINTS},
    "required": ["observations", "questions"],
}

#: Content hash of the system prompt and output schema
PROMPT_VERSION = prompt_version(_SYSTEM, json.dumps(_SCHEMA, sort_keys=True))

_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
#: Words that accuse or decide: a reviewer describes, people judge
_FORBIDDEN = re.compile(
    r"\b(fraud\w*|crim\w*|launder\w*|guilt\w*|approv\w*|reject\w*|declin\w*|recommend\w*)\b",
    re.IGNORECASE,
)


def _findings(evidence: list[Evidence]) -> list[Evidence]:
    return [e for e in evidence if e.severity != "info"]


def facts(evidence: list[Evidence], chains: list[TransactionChain], timeline: list[TimelineEvent]) -> str:
    """The verified facts the model may use, as JSON. No document quotes."""
    return json.dumps({
        "findings": [
            {"id": e.evidence_id, "check": e.check_id, "severity": e.severity, "document": e.document,
             "expected": e.expected, "observed": e.observed, "explanation": e.explanation}
            for e in _findings(evidence)
        ],
        "chain_breaks": [
            {"invoice": c.invoice_number, "step": s.step, "status": s.status,
             "invoice_says": f"{s.invoice_claim.label}: {s.invoice_claim.value}",
             "counterpart_says": f"{s.counterpart.label}: {s.counterpart.value}" if s.counterpart else None,
             "finding_ids": s.evidence_ids}
            for c in chains for s in c.steps if s.status in ("contradicts", "missing")
        ],
        "date_anomalies": [
            {"date": str(t.on), "kind": t.kind, "fact": t.label, "trade": t.ref, "finding_ids": t.evidence_ids}
            for t in timeline if t.evidence_ids
        ],
    }, indent=1)


def _grounded(point: Any, known_ids: set[str], known_numbers: set[str]) -> ReviewPoint | None:
    """The point, if it cites only known findings, uses only known numbers
    and neither accuses nor decides; else None."""
    try:
        p = ReviewPoint.model_validate(point)
    except ValueError:
        return None
    if not p.evidence_ids or not set(p.evidence_ids) <= known_ids or _FORBIDDEN.search(p.text):
        return None
    if any(n.rstrip(".").replace(",", "") not in known_numbers for n in _NUMBER.findall(p.text)):
        return None
    return p


def _template(chains: list[TransactionChain], timeline: list[TimelineEvent],
              evidence: list[Evidence]) -> list[ReviewPoint]:
    """Deterministic observations: each chain break and date anomaly, or
    failing those, each serious finding."""
    points = [
        ReviewPoint(text=f"Invoice {c.invoice_number}: the {s.step.replace('_', ' ')} "
                         f"{'contradicts it' if s.status == 'contradicts' else 'is missing'}.",
                    evidence_ids=s.evidence_ids)
        for c in chains for s in c.steps if s.status in ("contradicts", "missing") and s.evidence_ids
    ]
    points += [
        ReviewPoint(text=f"{t.label} ({t.on}) is out of sequence with related documents.",
                    evidence_ids=t.evidence_ids)
        for t in timeline if t.evidence_ids
    ]
    return points or [
        ReviewPoint(text=e.explanation, evidence_ids=[e.evidence_id])
        for e in _findings(evidence) if e.severity in ("high", "critical")
    ]


def review(
    case_id: str, evidence: list[Evidence], chains: list[TransactionChain],
    timeline: list[TimelineEvent],
) -> SemanticReview | None:
    """Review the case's story. None when there is nothing to review (no
    findings), in which case the model is not called."""
    if not _findings(evidence):
        return None
    given = facts(evidence, chains, timeline)
    result = converse(_SYSTEM, f"<facts>\n{given}\n</facts>", purpose="semantic_review",
                      case_id=case_id, schema=_SCHEMA)
    data = result.data or {}
    known_ids = {e.evidence_id for e in _findings(evidence)}
    known_numbers = {n.rstrip(".").replace(",", "") for n in _NUMBER.findall(given)}
    raw = {key: data.get(key) or [] for key in ("observations", "questions")}
    kept = {
        key: [p for point in points if (p := _grounded(point, known_ids, known_numbers))]
        for key, points in raw.items()
    }
    rejected = sum(len(points) for points in raw.values()) - sum(len(p) for p in kept.values())
    if not kept["observations"]:
        return SemanticReview(observations=_template(chains, timeline, evidence),
                              questions=kept["questions"], source="template", rejected=rejected)
    return SemanticReview(observations=kept["observations"], questions=kept["questions"],
                          source="llm", rejected=rejected)
