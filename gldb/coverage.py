"""
gldb/coverage.py
----------------
Evidence coverage: the share of risk findings that can be traced to their
source, so a reviewer can check every claim against the document itself.

    coverage = traceable risk findings / all risk findings

A risk finding (non-info fraud_signal) is traceable when it names:
- a document in the pack and a page that document has, plus a quote or an
  observed value to check there; a quote must actually be on that page, in
  its text or where the evidence viewer can box it (a form field's value, a
  workbook row); a finding with rows is traceable when every row is too;
- or the application data or the official records (PACK_DOC), when the case
  has them.

The system must never raise a finding it cannot point to; anything below 1.0
is a defect to fix, not a statistic to report.
"""

from __future__ import annotations

from gldb.checks import PACK_DOC
from gldb.intake import contains_quote, read_text
from gldb.schemas import Evidence, ExtractedPack
from gldb.viewer import locate


def _on_page(pack: ExtractedPack, document: str, page: int, quote: str | None, observed: str | None) -> bool:
    if not (quote or observed):
        return False
    if document == PACK_DOC:
        return pack.manifest is not None or pack.official is not None
    doc = next((d for d in pack.docs if d.doc_id == document), None)
    if doc is None or not 1 <= page <= doc.page_count:
        return False
    if quote is None:
        return True
    path = pack.paths.get(document)
    if path is None:
        return False  # the file is not at hand, so the quote cannot be checked
    text = read_text(path, ocr=pack.ocr.get(document))[page - 1]
    return contains_quote([text], quote) or bool(locate(path, page, quote))


def _traceable(ev: Evidence, pack: ExtractedPack) -> bool:
    """The finding, and every row of it, can be checked where it points."""
    return all(_on_page(pack, ev.document, page, quote, observed)
               for page, quote, observed in [(ev.page, ev.quote, ev.observed),
                                             *((r.page, r.quote, r.observed) for r in ev.rows)])


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
