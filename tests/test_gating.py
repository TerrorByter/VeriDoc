"""
tests/test_gating.py
--------------------
Only leads code verified (or corroborated) become flags, memo points and
applicant questions; a rule-confirmed sequence is the rule's finding, not a
second flag. The false-positive safety invariant runs over every pack: a lead
that is not verified or corroborated never moves the champion score or route,
never enters the memo and is never what a question rests on. Offline; scripted
Bedrock.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from gldb import llm_client
from gldb.ai_team import team as team_module
from gldb.ai_team.checks import bank_rows, counterparties
from gldb.ai_team.shadow import decide, shadow_input
from gldb.ai_team.team import TeamModels, flagged, run_team
from gldb.ai_team.tools import CaseData
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, CaseResult
from tests.fakes import ScriptedBedrock, use

PACKS = Path(__file__).parent.parent / "data" / "packs"
MODELS = TeamModels(supervisor="sup-model", specialist="spec-model")
BRIEFS = {"forensics": "F", "transaction": "T", "kyc": "K", "timeline": "L"}


@cache
def _result(name: str) -> CaseResult:
    folder = PACKS / name
    return assess(f"gating-{name}", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None)


def _data(name: str) -> CaseData:
    result = _result(name)
    return CaseData(result.case_id, result.pack, result.evidence)


@pytest.fixture(autouse=True)
def one_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Specialists in a fixed order (forensics, transaction, kyc, timeline), so R ids are predictable."""
    monkeypatch.setattr(team_module, "ThreadPoolExecutor", lambda max_workers: ThreadPoolExecutor(1))


def _lead(cite: str, pattern: str, **fields: Any) -> dict[str, Any]:
    test = {"cites": [cite], "expected": "the documents agree", "observed": "they do not"}
    return use("record_lead", kind="pattern", pattern=pattern, hypothesis="h", tests=[test],
               investigation_status="supported", **fields)


def _memo(ids: list[str]) -> list[list[dict[str, Any]]]:
    points = [{"text": f"See {i}.", "cites": [i]} for i in ids]
    return [[use("submit_memo", observations=points, questions=points)]]


def _sent(fake: ScriptedBedrock) -> dict[str, Any]:
    """What the Supervisor was given to write the memo from."""
    return json.loads(fake.requests["memo"][0]["messages"][0]["content"][0]["text"])


def _harbourline(case: CaseData) -> str:
    return next(r for r, _, t in bank_rows(case.pack) if "HARBOURLINE LOGISTICS INV-1002" in t.description)


def test_memo_task_and_points_hold_only_verified_and_corroborated_leads() -> None:
    case = _data("third_party_payment")
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        # R01 the search; L01 verified (Harbourline), L02 an unchecked pattern: unverified
        "transaction": [[use("search_bank_transactions", text="INV-1002")],
                        [_lead("R01", "third_party_payment", invoice_number="INV-1002",
                               transaction_ids=[_harbourline(case)]),
                         _lead("R01", "split_payment")]],
        "memo": _memo(["L01", "L02"]),
    })

    team = run_team(case, MODELS, client=fake)

    assert [(lead.lead_id, lead.evidence_status) for lead in team.leads] == [("L01", "verified"), ("L02", "unverified")]
    sent = json.loads(fake.requests["memo"][0]["messages"][0]["content"][0]["text"])["leads"]
    assert [lead["lead_id"] for lead in sent] == ["L01"]
    assert team.memo is not None
    assert [p.evidence_ids for p in team.memo.observations] == [["L01"]]
    assert [p.evidence_ids for p in team.memo.questions] == [["L01"]]
    assert team.memo.withheld == 2


def test_question_resting_only_on_unverified_evidence_is_withheld() -> None:
    case = _data("third_party_payment")
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "transaction": [[use("search_bank_transactions", text="INV-1002")],
                        [_lead("R01", "split_payment"),
                         use("draft_question", text="Who paid?", cites=["R01"], lead_id="L01")]],
    })

    team = run_team(case, MODELS, client=fake)

    assert "transaction: question withheld (no verified lead rests on it)" in team.notes
    assert json.loads(fake.requests["memo"][0]["messages"][0]["content"][0]["text"])["questions"] == []


def test_question_resting_on_a_verified_lead_reaches_the_supervisor() -> None:
    case = _data("third_party_payment")
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "transaction": [[use("search_bank_transactions", text="INV-1002")],
                        [_lead("R01", "third_party_payment", invoice_number="INV-1002",
                               transaction_ids=[_harbourline(case)]),
                         use("draft_question", text="Who is Harbourline Logistics?", cites=["R01"], lead_id="L01")]],
    })

    run_team(case, MODELS, client=fake)

    sent = json.loads(fake.requests["memo"][0]["messages"][0]["content"][0]["text"])["questions"]
    assert [q["text"] for q in sent] == ["Who is Harbourline Logistics?"]


def test_content_about_a_contradicted_leads_subject_does_not_ride_in_on_a_shared_result() -> None:
    # Run 5's review: one search result holds both invoices' rows; L01 (INV-1002) is verified, L02 (INV-1001)
    # is not. Nothing about INV-1001 may reach a question or a memo point because L01 cites the same result.
    case = _data("third_party_payment")
    orchid = next(r for r, _, t in bank_rows(case.pack) if t.description.endswith("INV-1001"))
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "transaction": [[use("search_bank_transactions", text="INV")],
                        [_lead("R01", "third_party_payment", invoice_number="INV-1002",
                               transaction_ids=[_harbourline(case)]),
                         _lead("R01", "third_party_payment", invoice_number="INV-1001", transaction_ids=[orchid]),
                         use("draft_question", text="Why was INV-1001 paid by Orchid Lane Retail?", cites=["R01"],
                             lead_id="L01"),
                         use("draft_question", text="Why was it paid on 2026-06-18?", cites=["R01"], lead_id="L01"),
                         use("draft_question", text="Who is Harbourline Logistics?", cites=["R01"], lead_id="L01")]],
        "memo": [[use("submit_memo", questions=[], observations=[
            {"text": "INV-1001 was paid by Orchid Lane Retail on 2026-06-18.", "cites": ["L01"]},
            {"text": "INV-1002 was paid by HARBOURLINE LOGISTICS, not its buyer.", "cites": ["L01"]}])]],
    })

    team = run_team(case, MODELS, client=fake)

    assert [lead.evidence_status for lead in team.leads] == ["verified", "contradicted"]
    assert [q["text"] for q in _sent(fake)["questions"]] == ["Who is Harbourline Logistics?"]
    assert team.memo is not None
    assert [p.text for p in team.memo.observations] == ["INV-1002 was paid by HARBOURLINE LOGISTICS, not its buyer."]
    assert team.memo.withheld == 1
    # The Supervisor sees what code established about the flag and none of the agent's prose
    (sent,) = _sent(fake)["leads"]
    assert set(sent) == {"lead_id", "agent", "kind", "finding_ids", "pattern", "invoice_number", "transaction_ids",
                         "counterparty", "evidence_status", "check_reason", "check_facts"}
    assert sent["check_facts"]["amount"] == "18530.00" and sent["check_facts"]["date"] == "2026-07-12"


def test_rule_confirmed_sequence_lead_is_not_a_flag() -> None:
    case = _data("retro_po")
    finding = next(e for e in case.evidence if e.check_id == "TEMP_TRADE_SEQUENCE"
                   and e.document in {i.doc_id for i in case.pack.invoices})
    invoice = next(i.invoice_number for i in case.pack.invoices if i.doc_id == finding.document)
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "timeline": [[use("get_timeline")], [_lead("R01", "sequence_anomaly", invoice_number=invoice)]],
    })

    team = run_team(case, MODELS, client=fake)

    (lead,) = team.leads
    assert lead.evidence_status == "verified" and lead.check_facts["rule_finding"] == finding.evidence_id
    assert not flagged(lead)
    assert json.loads(fake.requests["memo"][0]["messages"][0]["content"][0]["text"])["leads"] == []


def _unmatched(case: CaseData) -> str:
    return next(e.evidence_id for e in case.evidence if e.check_id == "XDOC_INVOICE_BANK" and e.severity != "info")


def _split_case(second_payer: str | None = None) -> CaseData:
    """innocent_split; with *second_payer*, its INV-1002 second part paid by that name instead of the buyer."""
    case = _data("innocent_split")
    if second_payer is None:
        return case
    part = next(r for r, _, t in bank_rows(case.pack) if t.description.endswith("INV-1002 PART 2"))
    statements = [s.model_copy(update={"transactions": [
        t.model_copy(update={"description": f"GIRO CR {second_payer} INV-1002 PART 2"}) if rid == part else t
        for rid, st, t in bank_rows(case.pack) if st is s]}) for s in case.pack.bank_statements]
    return CaseData(case.case_id, case.pack.model_copy(update={"bank_statements": statements}), case.evidence)


def test_a_finding_code_resolved_is_not_asked_about_and_the_supervisor_is_told() -> None:
    case = _split_case()
    finding = _unmatched(case)
    fake = ScriptedBedrock({"plan": [[use("submit_plan", briefs=BRIEFS)]], "memo": _memo([finding])})

    team = run_team(case, MODELS, client=fake)

    assert team.memo is not None
    assert team.memo.observations == [] and team.memo.questions == [] and team.memo.withheld == 2
    assert _sent(fake)["resolved"] == {finding: next(iter(_sent(fake)["resolved"].values()))}
    assert "paid in parts by its buyer" in _sent(fake)["resolved"][finding]
    assert f"{finding} resolved by code: INV-1002 was paid in parts by its buyer" in " ".join(team.notes)


def test_a_finding_code_cannot_resolve_is_still_asked_about() -> None:
    case = _split_case("HARBOURLINE LOGISTICS")
    finding = _unmatched(case)
    fake = ScriptedBedrock({"plan": [[use("submit_plan", briefs=BRIEFS)]], "memo": _memo([finding])})

    team = run_team(case, MODELS, client=fake)

    assert team.memo is not None
    assert [p.evidence_ids for p in team.memo.questions] == [[finding]]
    assert _sent(fake)["resolved"] == {}


def test_an_agents_refutation_alone_never_suppresses_a_rule_finding() -> None:
    # Transaction refutes the triage of the finding, as it did live; code cannot explain the finding
    case = _split_case("HARBOURLINE LOGISTICS")
    finding = _unmatched(case)
    triage = use("record_lead", kind="triage", finding_ids=[finding], hypothesis="the finding stands",
                 tests=[{"cites": ["R01"], "expected": "one credit", "observed": "two credits"}],
                 investigation_status="refuted")
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "transaction": [[use("search_bank_transactions", text="INV-1002")], [triage]],
        "memo": _memo([finding]),
    })

    team = run_team(case, MODELS, client=fake)

    (lead,) = team.leads
    assert lead.investigation_status == "refuted" and lead.outcome == "kept"
    assert team.memo is not None
    assert [p.evidence_ids for p in team.memo.questions] == [[finding]]
    assert [p.evidence_ids for p in team.memo.observations] == [[finding]]


# ---------------------------------------------------------------------------
# The false-positive safety invariant
# ---------------------------------------------------------------------------


def _every_pattern_script(case: CaseData) -> tuple[dict[str, list[list[dict[str, Any]]]], int]:
    """A team that, in a fixed order, records a lead for every checked pattern
    on every subject the case has, each citing its agent's one evidence call,
    and a memo citing every lead. Returns the scripts and how many leads."""
    invoices = [i.invoice_number for i in case.pack.invoices]
    rows = {i: [r for r, _, t in bank_rows(case.pack) if i in t.description and t.credit] for i in invoices}
    transaction = [_lead("R01", "third_party_payment", invoice_number=i, transaction_ids=rows[i])
                   for i in invoices if rows[i]] + [_lead("R01", "split_payment")]
    kyc_call = f"R{1 + 1 + len(transaction):02d}"
    kyc = [_lead(kyc_call, "related_party", counterparty=p) for p in counterparties(case.pack).values()]
    timeline_call = f"R{int(kyc_call[1:]) + 1 + len(kyc):02d}"
    timeline = [_lead(timeline_call, "sequence_anomaly", invoice_number=i) for i in invoices]
    count = len(transaction) + len(kyc) + len(timeline)
    return {
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "transaction": [[use("search_bank_transactions", text="INV")], transaction],
        "kyc": [[use("get_parties")], kyc],
        "timeline": [[use("get_timeline")], timeline],
        "memo": _memo([f"L{n:02d}" for n in range(1, count + 1)]),
    }, count


def _packs() -> list[str]:
    return sorted(p.name for p in PACKS.iterdir()
                  if (p / "ground_truth.json").is_file()
                  and not json.loads((p / "ground_truth.json").read_text()).get("requires"))


@pytest.mark.parametrize("name", _packs())
def test_false_positive_safety_invariant(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    off = _result(name)
    scripts, count = _every_pattern_script(_data(name))
    fake = ScriptedBedrock(scripts)
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    folder = PACKS / name
    on = assess(off.case_id, sorted(folder.glob("*.pdf")), load_manifest(folder), team=MODELS)

    team: AgentTeam | None = on.ai_team
    assert team is not None and team.status == "ran" and len(team.leads) == count, team and team.notes
    assert (on.risk, on.routing) == (off.risk, off.routing)  # the champion never moves
    memo = team.memo
    assert memo is not None
    cited = {i for p in memo.observations + memo.questions for i in p.evidence_ids}
    non_flags = [lead for lead in team.leads if not flagged(lead)]
    scorer_sees = {lead["lead_id"] for lead in shadow_input(_data(name), team.leads)["leads"]}
    for lead in team.leads:
        if lead.evidence_status in ("verified", "corroborated"):
            continue
        assert not flagged(lead), lead
        assert lead.lead_id not in cited, (lead.lead_id, lead.evidence_status, lead.check_reason)
        assert lead.lead_id not in scorer_sees, lead.lead_id  # not in the shadow scorer's input
    # Leads that are not flags never raise the gate's route
    assert off.routing is not None
    assert decide(off.routing, non_flags, None).would_route == off.routing.path
