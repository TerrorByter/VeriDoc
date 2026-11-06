"""
tests/test_acceptance_run5.py
-----------------------------
The acceptance table of the evidence verification spec, with live run 5's
leads (CASE-20261004T163428Z-92b7) as the fixtures, end to end through the
team on the real packs: the planted third-party payment becomes a flag and a
question; invoice-after-shipment, the director-guarantor, the unchecked
patterns and a refuted-then-verified miss behave as the spec says. Offline;
scripted Bedrock, specialists one at a time so call ids are fixed.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from gldb.ai_team import team as team_module
from gldb.ai_team.checks import bank_rows, counterparties
from gldb.ai_team.team import TeamModels, flagged, run_team
from gldb.ai_team.tools import CaseData
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, Lead
from gldb.screening import screening_key
from tests.fakes import ScriptedBedrock, use

PACKS = Path(__file__).parent / "fixtures" / "packs"
MODELS = TeamModels(supervisor="sup-model", specialist="spec-model")
BRIEFS = {"forensics": "F", "transaction": "T", "kyc": "K", "timeline": "L"}


@pytest.fixture(autouse=True)
def one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(team_module, "ThreadPoolExecutor", lambda max_workers: ThreadPoolExecutor(1))


@cache
def _case(name: str) -> CaseData:
    folder = PACKS / name
    result = assess(f"acceptance-{name}", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None)
    return CaseData(result.case_id, result.pack, result.evidence)


def _row(name: str, ending: str) -> str:
    return next(r for r, _, t in bank_rows(_case(name).pack) if t.description.endswith(ending))


def _party(name: str, company: str) -> str:
    return counterparties(_case(name).pack)[screening_key(company)]


def _lead(cite: str, pattern: str, status: str = "supported", **subject: Any) -> dict[str, Any]:
    test = {"cites": [cite], "expected": "what the documents say", "observed": "what the results show"}
    return use("record_lead", kind="pattern", pattern=pattern, hypothesis="h", tests=[test],
               investigation_status=status, **subject)


def _question(cite: str, text: str, lead_id: str = "L01") -> dict[str, Any]:
    return use("draft_question", text=text, cites=[cite], lead_id=lead_id)


def _team(name: str, scripts: dict[str, list[list[dict[str, Any]]]], memo: list[dict[str, Any]] | None = None
          ) -> tuple[AgentTeam, ScriptedBedrock]:
    points = [{"text": "See it.", "cites": [i]} for i in memo or []]
    fake = ScriptedBedrock({"plan": [[use("submit_plan", briefs=BRIEFS)]], **scripts,
                            "memo": [[use("submit_memo", observations=points, questions=points)]]})
    return run_team(_case(name), MODELS, client=fake), fake


def _sent(fake: ScriptedBedrock) -> dict[str, Any]:
    """What the Supervisor was given to write the memo from."""
    return json.loads(fake.requests["memo"][0]["messages"][0]["content"][0]["text"])


def _only(team: AgentTeam) -> Lead:
    (lead,) = team.leads
    return lead


def test_l12_the_planted_payment_is_verified_a_flag_and_asks_the_harbourline_question() -> None:
    team, fake = _team("third_party_payment", {"transaction": [
        [use("search_bank_transactions", text="INV")],
        [_lead("R01", "third_party_payment", invoice_number="INV-1002",
               transaction_ids=[_row("third_party_payment", "HARBOURLINE LOGISTICS INV-1002")]),
         _question("R01", "Who is Harbourline Logistics to the buyer?")]]}, memo=["L01"])
    lead = _only(team)
    assert lead.evidence_status == "verified" and flagged(lead)
    assert lead.check_facts["payer"] == "HARBOURLINE LOGISTICS"
    assert [q["text"] for q in _sent(fake)["questions"]] == ["Who is Harbourline Logistics to the buyer?"]
    assert team.memo is not None and team.memo.observations and team.memo.questions


def test_a_payment_the_buyer_made_is_contradicted() -> None:
    team, _ = _team("clean", {"transaction": [
        [use("search_bank_transactions", text="INV")],
        [_lead("R01", "third_party_payment", invoice_number="INV-1001",
               transaction_ids=[_row("clean", "ORCHID LANE RETAIL INV-1001")])]]})
    assert _only(team).evidence_status == "contradicted" and not flagged(_only(team))


def test_the_innocent_split_instalments_are_contradicted() -> None:
    rows = [r for r, _, t in bank_rows(_case("innocent_split").pack) if "INV-1002 PART" in t.description]
    team, _ = _team("innocent_split", {"transaction": [
        [use("search_bank_transactions", text="INV")],
        [_lead("R01", "third_party_payment", invoice_number="INV-1002", transaction_ids=rows)]]})
    assert _only(team).evidence_status == "contradicted"


def test_l04_l08_invoice_after_shipment_is_no_flag_and_no_question() -> None:
    team, fake = _team("clean", {"timeline": [
        [use("get_timeline")],
        [_lead("R01", "sequence_anomaly", invoice_number="INV-1001"),
         _lead("R01", "sequence_anomaly", invoice_number="INV-1002"),
         _question("R01", "Why was the invoice raised after shipment?")]]}, memo=["L01", "L02"])
    assert [lead.evidence_status for lead in team.leads] == ["contradicted", "contradicted"]
    assert not any(flagged(lead) for lead in team.leads)
    assert _sent(fake) == {"leads": [], "questions": [], "resolved": {}}
    assert team.memo is not None and not team.memo.observations and not team.memo.questions


def test_the_retro_po_sequence_is_verified_but_only_confirms_its_rule_finding() -> None:
    case = _case("retro_po")
    finding = next(e for e in case.evidence if e.check_id == "TEMP_TRADE_SEQUENCE"
                   and e.document in {i.doc_id for i in case.pack.invoices})
    invoice = next(i.invoice_number for i in case.pack.invoices if i.doc_id == finding.document)
    team, fake = _team("retro_po", {"timeline": [[use("get_timeline")],
                                                 [_lead("R01", "sequence_anomaly", invoice_number=invoice)]]})
    lead = _only(team)
    assert (lead.evidence_status, lead.check_facts["rule_finding"]) == ("verified", finding.evidence_id)
    assert not flagged(lead) and _sent(fake)["leads"] == []


def test_l13_the_director_guarantor_is_contradicted_no_flag_no_question() -> None:
    buyer = _party("clean", "Orchid Lane Retail Pte. Ltd.")
    team, fake = _team("clean", {"kyc": [
        [use("get_parties")],
        [_lead("R01", "related_party", counterparty=buyer),
         _question("R01", "Why is Daniel Brooks not a signatory?")]]}, memo=["L01"])
    assert _only(team).evidence_status == "contradicted"
    assert _sent(fake) == {"leads": [], "questions": [], "resolved": {}}
    assert team.memo is not None and team.memo.withheld == 2


def test_the_planted_related_party_is_verified_and_names_the_shared_officer() -> None:
    seabright = _party("related_party", "Seabright Provisions Pte. Ltd.")
    team, _ = _team("related_party", {"kyc": [[use("get_parties")],
                                              [_lead("R01", "related_party", counterparty=seabright)]]})
    lead = _only(team)
    assert flagged(lead) and lead.check_facts["shared_officer"] == "Daniel Brooks"


def test_l02_l14_unchecked_patterns_are_observations_never_flags_memo_points_or_questions() -> None:
    doc = _case("clean").pack.invoices[0].doc_id
    team, fake = _team("clean", {
        "forensics": [[use("get_forensic_facts", doc_id=doc)],
                      [_lead("R01", "document_inconsistency"), _question("R01", "Why do the documents match?")]],
        "kyc": [[use("get_parties")], [_lead("R04", "business_mismatch")]],  # R02, R03: forensics' receipts
    }, memo=["L01", "L02"])
    assert [(lead.evidence_status, lead.investigation_status) for lead in team.leads] == [
        ("unverified", "supported"), ("unverified", "supported")]
    assert not any(flagged(lead) for lead in team.leads)
    assert _sent(fake) == {"leads": [], "questions": [], "resolved": {}}
    assert team.memo is not None and not team.memo.observations and not team.memo.questions
    assert team.memo.withheld == 4  # both points cited an unverified lead, in both lists
    # Each is kept on the record with what it cited and why it was withheld
    held = "cites neither an open finding nor a confirmed AI flag: "
    assert [(p.section, p.evidence_ids, p.reason) for p in team.memo.withheld_points] == [
        ("observation", ["L01"], held + "L01"), ("observation", ["L02"], held + "L02"),
        ("question", ["L01"], held + "L01"), ("question", ["L02"], held + "L02"),
    ]
    assert "forensics: question withheld (no verified lead rests on it)" in team.notes


def test_a_lead_the_agent_called_refuted_that_code_verifies_is_a_caught_miss() -> None:
    team, fake = _team("third_party_payment", {"transaction": [
        [use("search_bank_transactions", text="INV")],
        [_lead("R01", "third_party_payment", status="refuted", invoice_number="INV-1002",
               transaction_ids=[_row("third_party_payment", "HARBOURLINE LOGISTICS INV-1002")])]]})
    lead = _only(team)
    assert (lead.investigation_status, lead.evidence_status, flagged(lead)) == ("refuted", "verified", True)
    assert [x["lead_id"] for x in _sent(fake)["leads"]] == ["L01"]


def test_the_clean_pack_gets_no_flags_and_no_questions_whatever_is_recorded() -> None:
    team, fake = _team("clean", {
        "transaction": [[use("search_bank_transactions", text="INV")], [_lead("R01", "split_payment")]],
        "timeline": [[use("get_timeline")], [_lead("R03", "sequence_anomaly", invoice_number="INV-1001")]],
    }, memo=["L01", "L02"])  # R01 the search, R02 its lead's receipt, R03 the timeline
    assert len(team.leads) == 2 and not any(flagged(lead) for lead in team.leads)
    assert _sent(fake) == {"leads": [], "questions": [], "resolved": {}}
