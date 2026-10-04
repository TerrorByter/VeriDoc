"""
tests/test_ai_team_verify.py
----------------------------
The deterministic gate on AI output: a lead is kept only if it cites tool
results its agent received, cites known findings, stays inside its
specialist's patterns, avoids verdict words and uses only numbers found in
what it cited. A memo point faces the same grounding rules. Offline; no AWS.
"""

from __future__ import annotations

from typing import get_args

from gldb.ai_team.verify import PATTERNS, check_lead, check_point, numbers
from gldb.schemas import Evidence, Lead, Pattern, ReviewPoint

_RESULTS = {"R01": '{"date": "2026-07-12", "credit": "18530.00", "description": "GIRO CR HARBOURLINE LOGISTICS INV-1002"}'}


def _finding(evidence_id: str = "E01", **fields: str) -> Evidence:
    return Evidence(evidence_id=evidence_id, check_id="invoice_total", severity="high", document="D1", page=1,
                    method="deterministic", confidence=0.9, explanation=fields.pop("explanation", "Total differs."),
                    **fields)


def _lead(**fields: object) -> Lead:
    base: dict[str, object] = {
        "agent": "transaction", "kind": "pattern", "pattern": "third_party_payment",
        "title": "INV-1002 paid by a non-buyer", "detail": "SGD 18,530.00 credit on 2026-07-12", "cites": ["R01"],
    }
    return Lead.model_validate(base | fields)


def test_numbers_are_normalised() -> None:
    assert numbers("SGD 18,530.00 and 10,000 on 2026-07-12") == {"18530", "10000", "2026", "07", "12"}


def test_numbers_drop_trailing_zeros_only_after_a_decimal_point() -> None:
    assert numbers("0.50 and 100.00 and 100 and 1,000.") == {"0.5", "100", "1000"}


def test_every_pattern_belongs_to_exactly_one_specialist() -> None:
    owned = [p for patterns in PATTERNS.values() for p in patterns]
    assert sorted(owned) == sorted(get_args(Pattern))


def test_grounded_lead_is_kept() -> None:
    results = {"R01": '{"date": "2026-07-12", "credit": "18530.00", "description": "GIRO CR HARBOURLINE LOGISTICS INV-1002"}'}
    lead = Lead(agent="transaction", kind="pattern", pattern="third_party_payment",
                title="INV-1002 paid by a non-buyer", detail="SGD 18,530.00 credit from Harbourline Logistics on 2026-07-12",
                cites=["R01"])
    assert check_lead(lead, {"R01"}, results, {}) is None


def test_lead_without_citations_is_withheld() -> None:
    assert check_lead(_lead(cites=[]), {"R01"}, _RESULTS, {}) == "no citations"


def test_lead_citing_a_result_its_agent_did_not_receive_is_withheld() -> None:
    lead = _lead(cites=["R01", "R09", "R08"])
    assert check_lead(lead, {"R01"}, _RESULTS, {}) == "cites a tool result it did not receive: R09"


def test_triage_lead_citing_an_unknown_finding_is_withheld() -> None:
    lead = _lead(kind="triage", pattern=None, finding_ids=["E01", "E99"])
    assert check_lead(lead, {"R01"}, _RESULTS, {"E01": _finding()}) == "cites an unknown finding: E99"


def test_triage_lead_needs_a_finding_or_a_verdict() -> None:
    lead = _lead(kind="triage", pattern=None)
    assert check_lead(lead, {"R01"}, _RESULTS, {}) == "triage lead without a finding or verdict"
    assert check_lead(lead.model_copy(update={"verdict": "unclear"}), {"R01"}, _RESULTS, {}) is None
    with_finding = lead.model_copy(update={"finding_ids": ["E01"]})
    assert check_lead(with_finding, {"R01"}, _RESULTS, {"E01": _finding()}) is None


def test_pattern_outside_the_specialists_list_is_withheld() -> None:
    lead = _lead(agent="kyc", pattern="round_trip")
    assert check_lead(lead, {"R01"}, _RESULTS, {}) == "pattern not in this specialist's list: round_trip"


def test_verdict_language_is_withheld() -> None:
    lead = _lead(title="INV-1002 looks FRAUDULENT", detail="Please approve")
    assert check_lead(lead, {"R01"}, _RESULTS, {}) == "uses verdict language: fraudulent"


def test_number_absent_from_cited_results_is_withheld() -> None:
    lead = _lead(detail="SGD 245 credit and 18,530.00 on 2026-07-12")
    assert check_lead(lead, {"R01"}, _RESULTS, {}) == "uses a number not in its cited results: 245"


def test_numbers_may_come_from_a_cited_findings_text() -> None:
    findings = {"E01": _finding(explanation="Total differs by 55.", expected="300", observed="245")}
    detail = "Invoice total 245 against 300 expected, 55 apart"
    assert check_lead(_lead(detail=detail), {"R01"}, _RESULTS, findings) == "uses a number not in its cited results: 245"
    assert check_lead(_lead(detail=detail, finding_ids=["E01"]), {"R01"}, _RESULTS, findings) is None


def test_point_needs_known_ids_and_numbers() -> None:
    assert check_point(ReviewPoint(text="See 18530", evidence_ids=["E01", "L02"]), {"E01", "L02"}, {"18530"})
    assert not check_point(ReviewPoint(text="x", evidence_ids=[]), {"E01"}, set())
    assert not check_point(ReviewPoint(text="Approve it", evidence_ids=["E01"]), {"E01"}, set())


def test_point_rejects_unknown_ids_and_unknown_numbers() -> None:
    assert not check_point(ReviewPoint(text="See it", evidence_ids=["E01", "E99"]), {"E01"}, set())
    assert not check_point(ReviewPoint(text="See 245", evidence_ids=["E01"]), {"E01"}, {"18530"})
