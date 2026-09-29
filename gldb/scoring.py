"""
gldb/scoring.py
---------------
Rule-based risk scoring and application readiness.

Rules:
- score() uses ONLY Evidence with category "fraud_signal".
- readiness() uses ONLY Evidence with category "compliance" or "completeness".
- No LLM involvement. Pure deterministic functions.
- The risk score is NEVER produced or changed by an LLM.
"""

from __future__ import annotations

from gldb.config import (
    BAND_LOW_MAX,
    BAND_MEDIUM_MAX,
    CHECKLIST,
    SEVERITY_WEIGHTS,
)
from gldb.schemas import Evidence, ExtractedPack, ReadinessResult, RiskResult

# ---------------------------------------------------------------------------
# Hard-rule triggers
# ---------------------------------------------------------------------------

#: check_id prefixes that force band "High" at severity high/critical
_HARD_RULE_PREFIXES: tuple[str, ...] = ("META_",)

#: Exact check_ids that force band "High" at severity high/critical
_HARD_RULE_IDS: frozenset[str] = frozenset({"XDOC_IDENTITY"})

#: Severities considered serious enough to trigger hard rules / manual review
_SERIOUS: frozenset[str] = frozenset({"high", "critical"})


def _is_hard_rule_trigger(ev: Evidence) -> bool:
    """Return True if *ev* would trigger a hard band-High rule."""
    if ev.severity not in _SERIOUS:
        return False
    if any(ev.check_id.startswith(p) for p in _HARD_RULE_PREFIXES):
        return True
    if ev.check_id in _HARD_RULE_IDS:
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score(evidence: list[Evidence]) -> RiskResult:
    """Compute a rule-based risk score from *evidence*.

    Only Evidence with category ``"fraud_signal"`` contributes to the score.
    Compliance and completeness Evidence are ignored here.

    Algorithm:
      1. raw  = sum of SEVERITY_WEIGHTS[ev.severity] for each fraud_signal.
      2. score = min(100, raw).
      3. band  = "Low" if score <= BAND_LOW_MAX,
                 "Medium" if score <= BAND_MEDIUM_MAX,
                 else "High".
      4. Hard rule: any fraud_signal with check_id starting with "META_" or
         equal to "XDOC_IDENTITY" at severity high or critical forces band "High".
      5. manual_review_required = band == "High" OR any fraud_signal at
         severity high or critical.
      6. reasons: one line per non-info fraud_signal, plus a line for each
         hard rule that fired.

    Args:
        evidence: All Evidence objects from the pipeline (mixed categories OK).

    Returns:
        A RiskResult with score, band, manual_review_required, and reasons.
    """
    fraud_signals = [ev for ev in evidence if ev.category == "fraud_signal"]

    # 1 & 2 — weighted sum, capped at 100
    raw = sum(SEVERITY_WEIGHTS.get(ev.severity, 0) for ev in fraud_signals)
    final_score = min(100, raw)

    # 3 — band from score
    if final_score <= BAND_LOW_MAX:
        band: str = "Low"
    elif final_score <= BAND_MEDIUM_MAX:
        band = "Medium"
    else:
        band = "High"

    # 4 — hard rules: certain check_ids at serious severity always force High
    hard_rule_fired: list[str] = []
    for ev in fraud_signals:
        if _is_hard_rule_trigger(ev):
            if band != "High":
                band = "High"
            hard_rule_fired.append(
                f"Hard rule: {ev.check_id} ({ev.severity}) forces band High"
                f" — {ev.evidence_id}"
            )

    # 5 — manual review
    any_serious = any(ev.severity in _SERIOUS for ev in fraud_signals)
    manual_review_required: bool = band == "High" or any_serious

    # 6 — reasons
    reasons: list[str] = []
    for ev in fraud_signals:
        if ev.severity == "info":
            continue
        reasons.append(
            f"{ev.evidence_id} {ev.check_id} ({ev.severity}): {ev.explanation}"
        )
    reasons.extend(hard_rule_fired)

    return RiskResult(
        score=final_score,
        band=band,  # type: ignore[arg-type]
        manual_review_required=manual_review_required,
        reasons=reasons,
    )


def readiness(evidence: list[Evidence], pack: ExtractedPack) -> ReadinessResult:
    """Compute application readiness from *evidence* and the extracted *pack*.

    Only Evidence with category ``"compliance"`` or ``"completeness"`` feeds
    this result.  Fraud signals are ignored here.

    Checklist logic (see CHECKLIST in config.py):
      - FATCA_CRS_ENTITY: "Provided" if pack has at least one entity self-cert
        document; else "Missing".
      - BANK_6M: "Missing" if no bank statements; "Incomplete" if a
        BANK_COVERAGE evidence fired; else "Provided".
      - Items with implemented=False: "Not checked in prototype".

    Args:
        evidence: All Evidence objects from the pipeline.
        pack: The extracted document pack.

    Returns:
        A ReadinessResult with status, open_items, and checklist.
    """
    # Filter to compliance/completeness only
    readiness_evidence = [
        ev for ev in evidence
        if ev.category in ("compliance", "completeness")
    ]

    # Open items: non-info findings
    open_items: list[str] = [
        f"{ev.evidence_id} {ev.check_id} ({ev.severity}): {ev.explanation}"
        for ev in readiness_evidence
        if ev.severity != "info"
    ]

    status = "Incomplete" if open_items else "Complete"

    # Build per-item checklist
    # Precompute facts needed for checklist decisions
    has_entity_cert = bool(pack.entity_self_certs) or any(
        d.doc_type in ("fatca_crs_entity",) for d in pack.docs
    )
    has_bank_statements = bool(pack.bank_statements)
    bank_coverage_fired = any(
        ev.check_id == "BANK_COVERAGE" for ev in evidence
    )

    checklist: list[dict] = []
    for item in CHECKLIST:
        item_id: str = item["id"]
        label: str = item["label"]
        implemented: bool = item.get("implemented", False)

        if not implemented:
            item_status = "Not checked in prototype"
        elif item_id == "FATCA_CRS_ENTITY":
            item_status = "Provided" if has_entity_cert else "Missing"
        elif item_id == "BANK_6M":
            if not has_bank_statements:
                item_status = "Missing"
            elif bank_coverage_fired:
                item_status = "Incomplete"
            else:
                item_status = "Provided"
        else:
            # Fallback for any future implemented items
            item_status = "Provided"

        checklist.append({"id": item_id, "label": label, "status": item_status})

    return ReadinessResult(
        status=status,  # type: ignore[arg-type]
        open_items=open_items,
        checklist=checklist,
    )
