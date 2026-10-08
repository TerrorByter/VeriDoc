"""
gldb/routing.py
---------------
Decision layer: turns a scored case into a review path.

The risk score measures; this module applies policy (gldb/config.py, routing
section). Keeping them apart means policy can change without touching how risk
is measured, and every Routing records the POLICY_VERSION that produced it.

Rules:
- Pure, deterministic functions. No LLM involvement.
- Paths follow the risk band: Low -> fast_track, Medium -> standard_review,
  High -> enhanced_review (with enhanced due diligence).
- Signals only ever raise the path, never lower it ("most cautious wins"):
  a hard flag forces enhanced_review; low extraction confidence or an
  incomplete application forces at least standard_review.
- A reproducible QA_SAMPLE_PERCENT of fast-track cases also go to an analyst.
- The system routes; it never approves, declines or reports a case.
"""

from __future__ import annotations

import hashlib

from gldb.config import (
    HARD_FLAG_CHECK_IDS,
    OCR_MIN_CONFIDENCE,
    POLICY_VERSION,
    QA_SAMPLE_PERCENT,
)
from gldb.schemas import (
    Evidence,
    ExtractedPack,
    ReadinessResult,
    ReviewPath,
    RiskResult,
    Routing,
)

#: Review paths from least to most cautious
_PATHS: tuple[ReviewPath, ...] = ("fast_track", "standard_review", "enhanced_review")
_BAND_PATH: dict[str, ReviewPath] = {
    "Low": "fast_track", "Medium": "standard_review", "High": "enhanced_review",
}


def qa_sampled(case_id: str) -> bool:
    """Whether *case_id* falls in the QA sample. Derived from a hash of the
    case ID, so the draw is reproducible and an auditor can verify it."""
    bucket = int(hashlib.sha256(case_id.encode()).hexdigest(), 16) % 100
    return bucket < QA_SAMPLE_PERCENT


def hard_flags(evidence: list[Evidence]) -> list[str]:
    return [
        f"{ev.evidence_id} {ev.check_id}"
        for ev in evidence
        if ev.check_id in HARD_FLAG_CHECK_IDS and ev.severity != "info"
    ]


def low_confidence(evidence: list[Evidence], pack: ExtractedPack) -> list[str]:
    """Reasons the extracted data may be unreliable, so a score built on it
    cannot be trusted to fast-track the case."""
    reasons = [
        f"{doc.doc_id}: document type not recognised"
        for doc in pack.docs
        if doc.doc_type == "unknown"
    ]
    reasons += [
        f"{ev.evidence_id}: a check failed to run"
        for ev in evidence
        if ev.check_id == "SYSTEM_CHECK_ERROR"
    ]
    reasons += [
        f"{doc_id} page {page}: OCR confidence {conf:.0f}% is below {OCR_MIN_CONFIDENCE:.0f}%"
        for doc_id, pages in pack.ocr_confidence.items()
        for page, conf in enumerate(pages, start=1)
        if conf < OCR_MIN_CONFIDENCE
    ]
    return reasons


def route(
    case_id: str,
    evidence: list[Evidence],
    risk: RiskResult,
    readiness: ReadinessResult | None,
    pack: ExtractedPack,
) -> Routing:
    """Choose the review path for a scored case."""
    path: ReviewPath = _BAND_PATH[risk.band]
    reasons = [f"Risk band {risk.band} (score {risk.score}) -> {path}"]

    def at_least(minimum: ReviewPath, why: str) -> None:
        nonlocal path
        if _PATHS.index(minimum) > _PATHS.index(path):
            path = minimum
        reasons.append(f"{why} -> at least {minimum}")

    flags = hard_flags(evidence)
    if flags:
        at_least("enhanced_review", f"Hard flag(s): {', '.join(flags)}")
    unreliable = low_confidence(evidence, pack)
    if unreliable:
        at_least("standard_review", "Low extraction confidence")
    if readiness is not None and readiness.status == "Incomplete":
        at_least("standard_review", "Application incomplete: request information first")

    sampled = path == "fast_track" and qa_sampled(case_id)
    if sampled:
        reasons.append(f"Drawn for the {QA_SAMPLE_PERCENT}% QA sample -> analyst review")

    return Routing(
        path=path,
        analyst_review=path != "fast_track" or sampled,
        qa_sampled=sampled,
        edd_required=path == "enhanced_review",
        hard_flags=flags,
        low_confidence=unreliable,
        reasons=reasons,
        policy_version=POLICY_VERSION,
    )
