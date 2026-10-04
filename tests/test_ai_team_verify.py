"""
tests/test_ai_team_verify.py
----------------------------
The deterministic gate on AI output: a lead is kept only if each of its tests
cites tool results its agent received and that are evidence, it cites known
findings, stays inside its specialist's patterns, avoids verdict words, keeps
to the lead contract (a supported lead shows a difference, an unresolved one
names what is missing) and states only numbers and identifiers found in what
it rests on. Strength is assigned here, by code. A memo point faces the same
grounding rules. Offline; no AWS.
"""

from __future__ import annotations

from typing import get_args

import pytest

from gldb.ai_team.verify import PATTERNS, Known, check_lead, check_point, check_question, known, numbers
from gldb.schemas import Evidence, Lead, Pattern, ReviewPoint

_ROW = '{"id": "T05", "date": "2026-07-12", "credit": "18530.00", "description": "GIRO CR HARBOURLINE LOGISTICS INV-1002"}'
_FIELDS = '{"invoice_number": "INV-1002", "buyer_name": "Bayview Marine Supplies", "total": "18530.00"}'
_RESULTS = {"R01": _ROW, "R02": _FIELDS}
_INDEX = "Documents in this case:\n- invoice_INV-1001 (invoice, 1 pages)\n- invoice_INV-1002 (invoice, 1 pages)\n" \
         "Invoices: INV-1001, INV-1002"
_ALL = {"R01", "R02"}


def _finding(evidence_id: str = "E01", **fields: str) -> Evidence:
    return Evidence(evidence_id=evidence_id, check_id="invoice_total", severity="high", document="D1", page=1,
                    method="deterministic", confidence=0.9, explanation=fields.pop("explanation", "Total differs."),
                    **fields)


def _test(cites: list[str] | None = None, expected: str = "payer: Bayview Marine Supplies",
          observed: str = "payer: HARBOURLINE LOGISTICS, SGD 18,530.00 on 2026-07-12") -> dict[str, object]:
    return {"cites": ["R01", "R02"] if cites is None else cites, "expected": expected, "observed": observed}


def _lead(**fields: object) -> Lead:
    base: dict[str, object] = {
        "agent": "transaction", "kind": "pattern", "pattern": "third_party_payment",
        "invoice_number": "INV-1002", "transaction_ids": ["T05"],
        "hypothesis": "INV-1002 was paid by someone other than its buyer", "tests": [_test()],
        "investigation_status": "supported",
    }
    return Lead.model_validate(base | fields)


def _check(lead: Lead, findings: dict[str, Evidence] | None = None, issued: set[str] = _ALL,
           citable: set[str] = _ALL) -> str | None:
    return check_lead(lead, issued, citable, _RESULTS, findings or {}, _INDEX)


def test_numbers_are_normalised() -> None:
    assert numbers("SGD 18,530.00 and 10,000 on 2026-07-12") == {"18530", "10000", "2026", "07", "12"}


def test_numbers_drop_trailing_zeros_only_after_a_decimal_point() -> None:
    assert numbers("0.50 and 100.00 and 100 and 1,000.") == {"0.5", "100", "1000"}


def test_known_holds_numbers_and_identifiers_casefolded() -> None:
    assert known("INV-1002 paid SGD 18,530.00 by uen 202205551D", "invoice_INV-1001") == Known(
        numbers=frozenset({"1002", "18530", "202205551", "1001"}),
        identifiers=frozenset({"inv-1002", "202205551d", "invoice_inv-1001"}),
    )


def test_every_pattern_belongs_to_exactly_one_specialist() -> None:
    owned = [p for patterns in PATTERNS.values() for p in patterns]
    assert sorted(owned) == sorted(get_args(Pattern))


def test_grounded_lead_is_kept() -> None:
    assert _check(_lead()) is None


def test_cites_are_every_test_s_citations_in_order() -> None:
    lead = _lead(tests=[_test(["R02", "R01"]), _test(["R01"])])
    assert lead.cites == ["R02", "R01"]


def test_lead_without_tests_is_withheld() -> None:
    assert _check(_lead(tests=[])) == "no tests"


def test_test_without_citations_is_withheld() -> None:
    assert _check(_lead(tests=[_test(), _test([])])) == "a test without citations"


def test_lead_citing_a_result_its_agent_did_not_receive_is_withheld() -> None:
    lead = _lead(tests=[_test(["R01", "R09", "R08"])])
    assert _check(lead) == "cites a tool result it did not receive: R09"


def test_empty_citation_is_not_a_received_result() -> None:
    assert _check(_lead(tests=[_test(["R01", ""])])) == "cites a tool result it did not receive: "


def test_lead_citing_a_receipt_or_an_error_is_withheld() -> None:
    # R01 and R02 are evidence; R03 was an error and R04 a record_lead receipt: received, but not evidence
    issued = _ALL | {"R03", "R04"}
    for receipt in ("R03", "R04"):
        reason = _check(_lead(tests=[_test(["R01", receipt])]), issued=issued)
        assert reason == f"cites a result that is not evidence: {receipt}"
        reason = check_question("Who paid?", ["R01", receipt], issued, _ALL, _RESULTS, _INDEX)
        assert reason == f"cites a result that is not evidence: {receipt}"


def test_a_result_not_received_is_reported_before_one_that_is_not_evidence() -> None:
    reason = _check(_lead(tests=[_test(["R02", "R09"])]), issued={"R01", "R02"}, citable={"R01"})
    assert reason == "cites a tool result it did not receive: R09"


def test_a_checked_pattern_needs_its_subject() -> None:
    assert _check(_lead(transaction_ids=[])) == "third_party_payment needs invoice_number and transaction_ids"
    assert _check(_lead(invoice_number=None)) == "third_party_payment needs invoice_number and transaction_ids"
    # A triage lead or an unchecked pattern needs none
    assert _check(_lead(pattern="split_payment", invoice_number=None, transaction_ids=[])) is None


def test_a_subject_id_must_appear_in_a_cited_result() -> None:
    assert _check(_lead(transaction_ids=["T05", "T09"])) == "subject T09 is not in its cited results"
    assert _check(_lead(tests=[_test(["R02"])])) == "subject T05 is not in its cited results"


def test_triage_lead_citing_an_unknown_finding_is_withheld() -> None:
    lead = _lead(kind="triage", pattern=None, finding_ids=["E01", "E99"])
    assert _check(lead, {"E01": _finding()}) == "cites an unknown finding: E99"


def test_empty_finding_id_is_an_unknown_finding() -> None:
    lead = _lead(kind="triage", pattern=None, finding_ids=[""])
    assert _check(lead, {"E01": _finding()}) == "cites an unknown finding: "


def test_triage_lead_needs_a_finding() -> None:
    findings = {"E01": _finding()}
    lead = _lead(kind="triage", pattern=None)
    assert _check(lead, findings) == "triage lead without a finding"
    assert _check(lead.model_copy(update={"finding_ids": ["E01"]}), findings) is None


def test_pattern_outside_the_specialists_list_is_withheld() -> None:
    lead = _lead(agent="kyc", pattern="round_trip")
    assert _check(lead) == "pattern not in this specialist's list: round_trip"


def test_pattern_rule_applies_to_any_lead_that_carries_a_pattern() -> None:
    findings = {"E01": _finding()}
    triage = _lead(kind="triage", agent="kyc", pattern="round_trip", finding_ids=["E01"])
    assert _check(triage, findings) == "pattern not in this specialist's list: round_trip"
    assert _check(triage.model_copy(update={"pattern": "related_party"}), findings) is None
    assert _check(_lead(pattern=None)) == "a pattern lead needs its pattern"


def test_supported_lead_needs_a_test_whose_observed_differs_from_its_expected() -> None:
    # A confirmation dressed as a finding: nothing differs, so it cannot be "supported"
    same = _test(expected="payer: Bayview Marine Supplies", observed="Payer:  bayview marine supplies.")
    reason = "supported without a test whose observed differs from its expected"
    assert _check(_lead(tests=[same])) == reason
    assert _check(_lead(tests=[same, _test()])) is None


def test_refuted_lead_records_a_clear_check() -> None:
    same = _test(expected="payer: Bayview Marine Supplies", observed="payer: Bayview Marine Supplies")
    assert _check(_lead(tests=[same], investigation_status="refuted")) is None


def test_unresolved_lead_needs_missing_evidence() -> None:
    assert _check(_lead(investigation_status="unresolved")) == "unresolved without missing evidence"
    assert _check(_lead(investigation_status="unresolved", missing_evidence=["The remitter's name on the GIRO credit"])) is None


def test_verdict_language_is_withheld_wherever_it_appears() -> None:
    assert _check(_lead(hypothesis="INV-1002 looks FRAUDULENT")) == "uses verdict language: fraudulent"
    assert _check(_lead(tests=[_test(observed="please approve")])) == "uses verdict language: approve"
    assert _check(_lead(investigation_status="unresolved", missing_evidence=["proof before we reject it"])) == \
        "uses verdict language: reject"


def test_number_absent_from_cited_results_is_withheld() -> None:
    lead = _lead(tests=[_test(observed="SGD 245 credit and 18,530.00 on 2026-07-12")])
    assert _check(lead) == "uses a number not in its cited results: 245"


def test_a_test_s_numbers_must_come_from_its_own_citations() -> None:
    # 18530 is in R01 and R02; the date 2026-07-12 only in R01, so a test citing R02 alone cannot state it
    lead = _lead(pattern="split_payment", invoice_number=None, transaction_ids=[],
                 tests=[_test(["R02"], observed="SGD 18,530.00 credited on 2026-07-12")])
    assert _check(lead) == \
        "uses a number not in its cited results: 2026"


def test_numbers_may_come_from_a_judged_finding_s_text() -> None:
    findings = {"E01": _finding(explanation="Total differs by 55.", expected="300", observed="245")}
    test = _test(expected="invoice total 300", observed="paid 245, 55 apart")
    triage = _lead(kind="triage", pattern=None, tests=[test])
    assert _check(triage.model_copy(update={"finding_ids": []}), findings) == "triage lead without a finding"
    assert _check(triage.model_copy(update={"finding_ids": ["E01"]}), findings) is None


def test_identifiers_are_not_numbers_but_must_be_known() -> None:
    # INV-1001 is in no cited result but in the case index: a name, not the quantity 1001 (live run 3)
    contrast = _lead(hypothesis="INV-1002, unlike INV-1001, was paid by someone other than its buyer")
    assert _check(contrast) is None
    invented = _lead(hypothesis="INV-1002 and INV-9999 were paid by someone other than their buyer")
    assert _check(invented) == "uses an identifier not in its cited results: INV-9999"


@pytest.mark.parametrize("identifier", ["inv-1002", "invoice_INV-1001", "BL-3002/A", "202205551D"])
def test_identifier_shapes(identifier: str) -> None:
    results = {"R01": _ROW + ' "BL-3002/A" 202205551D'}
    lead = _lead(tests=[_test(["R01"], expected="payer: the buyer", observed=f"HARBOURLINE, see {identifier}")])
    assert check_lead(lead, {"R01"}, {"R01"}, results, {}, _INDEX) is None


def test_result_finding_and_lead_ids_are_references() -> None:
    results = {"R05": '[{"credit": "10000.00"}, {"credit": "8530.00"}]'}
    test = _test(["R05"], expected="one credit of the invoice total", observed="R05 shows 10,000.00 and 8,530.00")
    lead = _lead(agent="transaction", pattern="split_payment", hypothesis="Paid in parts (see E04 and L01)",
                 tests=[test], invoice_number=None, transaction_ids=[])
    assert check_lead(lead, {"R05"}, {"R05"}, results, {}, _INDEX) is None
    assert check_question("Who sent the credits in R05 (see finding E04)?", ["R05"], {"R05"}, {"R05"}, results,
                          _INDEX) is None


def test_point_needs_known_ids_numbers_and_identifiers() -> None:
    grounding = known("18530 INV-1002")
    assert check_point(ReviewPoint(text="See 18,530.00 on INV-1002", evidence_ids=["E01", "L02"]), {"E01", "L02"},
                       grounding)
    assert not check_point(ReviewPoint(text="x", evidence_ids=[]), {"E01"}, grounding)
    assert not check_point(ReviewPoint(text="Approve it", evidence_ids=["E01"]), {"E01"}, grounding)
    assert not check_point(ReviewPoint(text="See it", evidence_ids=["E01", "E99"]), {"E01"}, grounding)
    assert not check_point(ReviewPoint(text="See 245", evidence_ids=["E01"]), {"E01"}, grounding)
    assert not check_point(ReviewPoint(text="See INV-9999", evidence_ids=["E01"]), {"E01"}, grounding)
    assert check_point(ReviewPoint(text="L01 and E04 agree", evidence_ids=["L01", "E04"]), {"L01", "E04"}, grounding)


def test_grounded_question_is_kept() -> None:
    assert check_question("Who sent the SGD 18,530.00 credit for INV-1001?", ["R01"], _ALL, _ALL, _RESULTS,
                          _INDEX) is None


def test_question_without_citations_is_withheld() -> None:
    assert check_question("Who paid?", [], _ALL, _ALL, _RESULTS, _INDEX) == "no citations"


def test_question_citing_a_result_its_agent_did_not_receive_is_withheld() -> None:
    reason = check_question("Who paid?", ["R01"], {"R02"}, {"R02"}, _RESULTS, _INDEX)
    assert reason == "cites a tool result it did not receive: R01"


def test_question_with_verdict_language_is_withheld() -> None:
    reason = check_question("Should this be declined?", ["R01"], _ALL, _ALL, _RESULTS, _INDEX)
    assert reason == "uses verdict language: declined"


def test_question_number_absent_from_cited_results_is_withheld() -> None:
    reason = check_question("Why SGD 18,531?", ["R01"], _ALL, _ALL, _RESULTS, _INDEX)
    assert reason == "uses a number not in its cited results: 18531"


def test_lead_stored_before_the_contract_still_loads() -> None:
    # Results saved by earlier runs (title, detail, cites, verdict) must keep loading
    old = {"lead_id": "L01", "agent": "transaction", "kind": "triage", "finding_ids": ["E01"],
           "verdict": "possibly_innocent", "title": "Paid in two parts", "detail": "Two credits", "cites": ["R01"],
           "outcome": "kept"}
    lead = Lead.model_validate(old)
    assert (lead.hypothesis, lead.investigation_status, lead.cites) == ("Paid in two parts", "refuted", ["R01"])
    assert lead.evidence_status == "unverified"
    assert lead.tests[0].observed == "Two credits"
    pattern = Lead.model_validate({**old, "kind": "pattern", "pattern": "split_payment", "verdict": None})
    assert pattern.investigation_status == "supported"


def test_lead_stored_before_the_status_rename_still_loads() -> None:
    old = {"lead_id": "L01", "agent": "transaction", "kind": "pattern", "pattern": "split_payment",
           "hypothesis": "h", "tests": [], "status": "supported", "strength": "corroborated", "outcome": "kept"}
    lead = Lead.model_validate(old)
    assert (lead.investigation_status, lead.evidence_status) == ("supported", "unverified")
    withheld = Lead.model_validate({**old, "outcome": "withheld", "strength": None})
    assert withheld.evidence_status is None
