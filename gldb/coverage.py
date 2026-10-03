"""
gldb/coverage.py
----------------
Evidence coverage: the share of risk findings that can be traced to their
source, so a reviewer can check every claim against the document itself.

    coverage = traceable risk findings / all risk findings

A risk finding (non-info fraud_signal) is traceable when it names:
- a document in the pack and a page that document has, plus a quote or an
  observed value to check there; a quote must actually be on that page;
- or the application data (PACK_DOC), when the case has it.

The system must never raise a finding it cannot point to; anything below 1.0
is a defect to fix, not a statistic to report.
"""

from __future__ import annotations

from gldb.checks import PACK_DOC
from gldb.intake import contains_quote, read_text
from gldb.schemas import Evidence, ExtractedPack


def _traceable(ev: Evidence, pack: ExtractedPack) -> bool:
    if not (ev.quote or ev.observed):
        return False
    if ev.document == PACK_DOC:
        return pack.manifest is not None
    doc = next((d for d in pack.docs if d.doc_id == ev.document), None)
    if doc is None or not 1 <= ev.page <= doc.page_count:
        return False
    if ev.quote is None:
        return True
    path = pack.paths.get(ev.document)
    if path is None:
        return False  # the file is not at hand, so the quote cannot be checked
    page = read_text(path, ocr=pack.ocr.get(ev.document))[ev.page - 1]
    return contains_quote([page], ev.quote)


def _risk_findings(evidence: list[Evidence]) -> list[Evidence]:
    return [e for e in evidence if e.category == "fraud_signal" and e.severity != "info"]


def untraceable(evidence: list[Evidence], pack: ExtractedPack) -> list[str]:
    """Evidence IDs of risk findings that cannot be traced to their source."""
    return [e.evidence_id for e in _risk_findings(evidence) if not _traceable(e, pack)]


def coverage(evidence: list[Evidence], pack: ExtractedPack) -> float:
    findings = _risk_findings(evidence)
    if not findings:
        return 1.0
    return 1 - len(untraceable(findings, pack)) / len(findings)
