"""
tests/test_ai_team.py
---------------------
The Supervisor-led team end to end on a scripted Bedrock: plan, fan out to
the four specialists, verify their leads, reconcile into a memo and verify
it. Checks the hub-and-spoke rule (no specialist sees another's brief or
results) and that unsupported output is withheld, never hidden. In the
pipeline: leads never move the score or route, a failed team never stops a
case, and full tool results stay server-side. No AWS calls.
"""

from __future__ import annotations

import functools
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from gldb import llm_client, pipeline
from gldb.ai_team import prompts, tools
from gldb.ai_team.prompts import DEFAULT_BRIEF, PROMPT_VERSIONS, SPECIALIST
from gldb.ai_team.team import TeamModels, run_team
from gldb.ai_team.tools import CaseData, case_index
from gldb.audit import read_events
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, Memo
from tests.fakes import ScriptedBedrock, use

PACKS = Path(__file__).parent.parent / "data" / "packs"
MODELS = TeamModels(supervisor="sup-model", specialist="spec-model")
BRIEFS = {"forensics": "FORENSICS-BRIEF", "transaction": "TRANSACTION-BRIEF", "kyc": "KYC-BRIEF",
          "timeline": "TIMELINE-BRIEF"}
SPECIALISTS = tuple(BRIEFS)


def _case(name: str) -> CaseData:
    folder = PACKS / name
    result = assess(name, sorted(folder.glob("*.pdf")), load_manifest(folder))
    return CaseData(result.case_id, result.pack, result.evidence)


@pytest.fixture(scope="module")
def clean() -> CaseData:
    return _case("clean")


@pytest.fixture(scope="module")
def tampered() -> CaseData:
    return _case("tampered")


def _finding(case: CaseData) -> str:
    return next(e.evidence_id for e in case.evidence if e.severity != "info")


def _plan() -> list[list[dict[str, Any]]]:
    return [[use("submit_plan", briefs=BRIEFS)]]


def _lead(cites: list[str], **fields: Any) -> dict[str, Any]:
    args = {"kind": "pattern", "pattern": "split_payment", "title": "Invoice settled by several credits",
            "detail": "Several smaller credits appear to settle one invoice.", "cites": cites, **fields}
    return use("record_lead", **args)


def _memo(observations: list[dict[str, Any]], questions: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [[use("submit_memo", observations=observations, questions=questions)]]


def _run(fake: ScriptedBedrock, case: CaseData) -> AgentTeam:
    return run_team(case, MODELS, client=fake)


def _first_text(request: dict[str, Any]) -> str:
    return request["messages"][0]["content"][0]["text"]


def _task(brief: str, case: CaseData) -> str:
    """A specialist's task: its brief, a blank line, then the case index."""
    return f"{brief}\n\n{case_index(case)}"


def _received(requests: list[dict[str, Any]]) -> str:
    """Everything an agent was sent: its system prompt and every user turn
    (task text and tool results), but not its own assistant turns."""
    parts: list[str] = []
    for request in requests:
        parts += [block["text"] for block in request["system"]]
        for message in request["messages"]:
            if message["role"] == "user":
                parts += [json.dumps(block) for block in message["content"]]
    return "\n".join(parts)


def _result_tag(call_id: str) -> str:
    """How a tool result's id reads inside a JSON-dumped toolResult block."""
    return f'id=\\"{call_id}\\"'


def test_team_plans_fans_out_and_writes_a_memo(tampered: CaseData) -> None:
    finding = _finding(tampered)
    fake = ScriptedBedrock({
        "plan": _plan(),
        # Only the transaction specialist calls tools, so its ids are deterministic: R01 search, R02 lead
        "transaction": [[use("search_bank_transactions", text="INV")], [_lead(["R01"])]],
        "memo": _memo([{"text": "One invoice appears settled by several credits.", "cites": ["L01", finding]}],
                      [{"text": "Who made each of the credits?", "cites": [finding]}]),
    })

    team = _run(fake, tampered)

    assert team.status == "ran"
    assert team.plan == BRIEFS
    assert [(c.call_id, c.agent, c.tool) for c in team.tool_calls] == [
        ("R01", "transaction", "search_bank_transactions"), ("R02", "transaction", "record_lead")]
    assert [(lead.lead_id, lead.outcome) for lead in team.leads] == [("L01", "kept")]
    assert team.memo is not None
    assert len(team.memo.observations) + len(team.memo.questions) == 2 and team.memo.withheld == 0
    assert team.memo.observations[0].evidence_ids == ["L01", finding]
    assert set(team.prompt_versions) == {"supervisor_plan", "supervisor_memo", *SPECIALISTS}
    assert team.prompt_versions == PROMPT_VERSIONS
    assert team.models == {"supervisor": "sup-model", "specialist": "spec-model"}
    assert team.notes == []
    assert set(team.trace) == {"R01", "R02"}
    # Each specialist was given exactly its brief and the case index; the models are the configured ones
    for name in SPECIALISTS:
        first = fake.requests[name][0]
        assert first["messages"] == [{"role": "user", "content": [{"text": _task(BRIEFS[name], tampered)}]}]
        assert first["modelId"] == "spec-model"
    assert {r["modelId"] for r in fake.requests["plan"] + fake.requests["memo"]} == {"sup-model"}
    # The reconcile step gets the kept lead, never the tool results behind it
    reconcile = json.loads(_first_text(fake.requests["memo"][0]))
    assert [lead["lead_id"] for lead in reconcile["leads"]] == ["L01"]
    assert "tool_result" not in _first_text(fake.requests["memo"][0])
    assert team.trace["R01"] not in _first_text(fake.requests["memo"][0])


def test_prompt_versions_cover_the_tools_each_agent_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    assert prompts.version(SPECIALIST["kyc"], "kyc") == PROMPT_VERSIONS["kyc"]
    assert prompts.version(prompts.SUPERVISOR_PLAN, "supervisor", "submit_plan") == PROMPT_VERSIONS["supervisor_plan"]
    monkeypatch.setitem(tools.TOOLS, "get_parties", replace(tools.TOOLS["get_parties"], description="changed"))
    assert prompts.version(SPECIALIST["kyc"], "kyc") != PROMPT_VERSIONS["kyc"]  # a tool is an instruction
    assert prompts.version(SPECIALIST["timeline"], "timeline") == PROMPT_VERSIONS["timeline"]  # not its tool
    monkeypatch.setitem(tools.FINISH_TOOLS, "submit_memo", {**tools.FINISH_TOOLS["submit_memo"], "description": "x"})
    assert prompts.version(prompts.SUPERVISOR_MEMO, "supervisor", "submit_memo") != PROMPT_VERSIONS["supervisor_memo"]


def test_every_specialist_records_leads_only_for_inconsistencies_and_verdicts() -> None:
    for prompt in SPECIALIST.values():
        assert ("Record a lead only for an inconsistency or a triage verdict on a finding; never record a "
                "confirmation that something is in order.") in prompt


def test_each_specialist_is_told_how_to_look() -> None:
    transaction, kyc, timeline = SPECIALIST["transaction"], SPECIALIST["kyc"], SPECIALIST["timeline"]
    assert "find its payment with search_bank_transactions (by amount, from the invoice date)" in transaction
    assert "compare who paid with the invoice's buyer" in transaction
    assert "several credits that add up to an invoice's total, none matching it alone (split_payment)" in transaction
    assert "money paid out that comes back from the same counterparty (round_trip)" in transaction
    assert ("Compare the officers of each counterparty in the register with the applicant's directors, "
            "guarantors and signatories (get_parties)") in kyc
    assert "Nothing should be dated before its purchase order" in timeline
    assert "an invoice may come before or after shipment" in timeline
    assert "payment follows the invoice" in timeline
    assert "An anomaly is a document dated before its purchase order, or a date before incorporation" in timeline
    # Each procedure is its own specialist's: no other prompt carries another's
    assert "compare who paid" not in kyc + timeline + SPECIALIST["forensics"]
    assert "Compare the officers" not in transaction + timeline + SPECIALIST["forensics"]
    assert "dated before its purchase order" not in transaction + kyc + SPECIALIST["forensics"]


def test_the_supervisor_prompts_cover_the_empty_memo_and_the_case_index() -> None:
    assert "Always call submit_memo; if there are no leads, call it with empty lists." in prompts.SUPERVISOR_MEMO
    assert "appends a case index to every brief automatically" in prompts.SUPERVISOR_PLAN
    assert "need not list files" in prompts.SUPERVISOR_PLAN


def test_hub_and_spoke_isolation(clean: CaseData) -> None:
    fake = ScriptedBedrock({
        "plan": _plan(),
        "transaction": [[use("search_bank_transactions", text="INV")]],
        # Three calls are made in all (two by kyc), so R01 to R03 always includes transaction's one
        "kyc": [[use("registry_lookup", uen="000000000X")],
                [use("record_lead", kind="pattern", pattern="related_party", title="Register entry",
                     detail="The register result is unclear.", cites=["R01", "R02", "R03"])]],
    })

    team = _run(fake, clean)

    own = {name: {c.call_id for c in team.tool_calls if c.agent == name} for name in ("transaction", "kyc")}
    (transaction_id,) = own["transaction"]
    assert len(own["kyc"]) == 2
    for name, other in (("kyc", "transaction"), ("transaction", "kyc")):
        received = _received(fake.requests[name])
        # Positive control: the agent's own brief and results do show up in what it received
        assert BRIEFS[name] in received
        assert all(_result_tag(call_id) in received for call_id in own[name])
        # So does the case index, which is case data and the same for each
        assert _first_text(fake.requests[name][0]) == _task(BRIEFS[name], clean)
        assert BRIEFS[other] not in received
        assert not any(f'"{call_id}"' in received or _result_tag(call_id) in received for call_id in own[other])
    (lead,) = team.leads
    # Nor does kyc's lead reach transaction, which was run alongside it
    assert lead.title not in _received(fake.requests["transaction"])
    assert lead.outcome == "withheld"
    assert lead.withheld_reason == f"cites a tool result it did not receive: {transaction_id}"


def test_unsupported_leads_are_withheld_not_hidden(tampered: CaseData) -> None:
    fake = ScriptedBedrock({
        "plan": _plan(),
        "transaction": [[use("search_bank_transactions", text="INV")],
                        [_lead(["R01"], detail="This looks like invoice fraud.")]],
    })

    team = _run(fake, tampered)

    (lead,) = team.leads
    assert lead.outcome == "withheld" and lead.withheld_reason == "uses verdict language: fraud"
    reconcile = _first_text(fake.requests["memo"][0])
    assert json.loads(reconcile)["leads"] == []
    assert "fraud" not in reconcile and lead.title not in reconcile


def test_supervisor_without_submit_uses_defaults(clean: CaseData) -> None:
    fake = ScriptedBedrock({"plan": [[{"text": "no plan"}]], "memo": [[{"text": "no memo"}]]})

    team = _run(fake, clean)

    assert team.plan == dict.fromkeys(SPECIALISTS, DEFAULT_BRIEF)
    for name in SPECIALISTS:
        assert _first_text(fake.requests[name][0]) == _task(DEFAULT_BRIEF, clean)
    assert team.notes == ["supervisor: no plan submitted, default briefs used", "supervisor: no memo submitted"]
    assert team.memo == Memo()


def test_partial_plan_fills_missing_briefs(clean: CaseData) -> None:
    fake = ScriptedBedrock({"plan": [[use("submit_plan", briefs={"forensics": "F", "kyc": "", "timeline": 3})]]})

    team = _run(fake, clean)

    assert team.plan == {"forensics": "F", "transaction": DEFAULT_BRIEF, "kyc": DEFAULT_BRIEF,
                         "timeline": DEFAULT_BRIEF}
    assert team.notes == ["supervisor: no memo submitted"]


def test_memo_points_citing_withheld_leads_are_dropped(tampered: CaseData) -> None:
    finding = _finding(tampered)
    fake = ScriptedBedrock({
        "plan": _plan(),
        "transaction": [[use("search_bank_transactions", text="INV")],
                        [_lead(["R01"])],
                        [_lead(["R01"], detail="A recommended decline.")]],
        "memo": _memo([{"text": "Kept lead.", "cites": ["L01", finding]},
                       {"text": "Withheld lead.", "cites": ["L02"]},
                       {"text": "Unknown lead.", "cites": ["L09"]},
                       {"text": "Malformed point.", "cites": "L01"},
                       "not a point"],
                      [{"text": "Uses a number never seen: 98765432.", "cites": [finding]}]),
    })

    team = _run(fake, tampered)

    assert [lead.outcome for lead in team.leads] == ["kept", "withheld"]
    assert team.memo is not None
    assert [p.text for p in team.memo.observations] == ["Kept lead."]
    assert team.memo.questions == []
    assert team.memo.withheld == 5


def test_only_verified_questions_reach_the_supervisor(tampered: CaseData) -> None:
    # Only the transaction specialist calls tools: R01 is its search, R02 to R04 its drafts
    fake = ScriptedBedrock({
        "plan": _plan(),
        "transaction": [[use("search_bank_transactions", text="INV")],
                        [use("draft_question", text="Who made the payments in 2026?", cites=["R01"]),
                         use("draft_question", text="Is this a laundering scheme?", cites=["R01"]),
                         use("draft_question", text="Why was 98765432 paid?", cites=["R01"])]],
    })

    team = _run(fake, tampered)

    reconcile = json.loads(_first_text(fake.requests["memo"][0]))
    assert reconcile["questions"] == [
        {"agent": "transaction", "text": "Who made the payments in 2026?", "cites": ["R01"]}]
    assert team.notes == [
        "transaction: question withheld (uses verdict language: laundering)",
        "transaction: question withheld (uses a number not in its cited results: 98765432)",
        "supervisor: no memo submitted",
    ]


def test_question_citing_another_agents_result_is_withheld(clean: CaseData) -> None:
    # Two calls in all, one each: whichever order they run in, kyc cites transaction's
    fake = ScriptedBedrock({
        "plan": _plan(),
        "transaction": [[use("search_bank_transactions", text="INV")]],
        "kyc": [[use("draft_question", text="Who is the payer?", cites=["R01", "R02"])]],
    })

    team = _run(fake, clean)

    (transaction_id,) = {c.call_id for c in team.tool_calls if c.agent == "transaction"}
    assert team.notes == [f"kyc: question withheld (cites a tool result it did not receive: {transaction_id})",
                          "supervisor: no memo submitted"]
    reconcile = _first_text(fake.requests["memo"][0])
    assert json.loads(reconcile)["questions"] == [] and "Who is the payer?" not in reconcile


def test_lead_citing_its_own_receipt_or_an_error_is_withheld(clean: CaseData) -> None:
    # Only kyc calls tools: R01 an error, R02 a lead receipt, R03 and R04 leads citing them
    fake = ScriptedBedrock({
        "plan": _plan(),
        "kyc": [[use("registry_lookup")],
                [_lead(["R01"], pattern="related_party")],
                [_lead(["R01"], pattern="related_party"), _lead(["R02"], pattern="related_party")]],
    })

    team = _run(fake, clean)

    assert [lead.withheld_reason for lead in team.leads] == [
        "cites a result that is not evidence: R01", "cites a result that is not evidence: R01",
        "cites a result that is not evidence: R02"]


# ---------------------------------------------------------------------------
# In the pipeline: stage 5's ai_team step
# ---------------------------------------------------------------------------


def _files(name: str) -> tuple[list[Path], Any]:
    folder = PACKS / name
    return sorted(folder.glob("*.pdf")), load_manifest(folder)


def _grounded(case: CaseData) -> ScriptedBedrock:
    """A team run with one kept pattern lead (R01 search, R02 lead) and a memo citing it."""
    finding = _finding(case)
    return ScriptedBedrock({
        "plan": _plan(),
        "transaction": [[use("search_bank_transactions", text="INV")], [_lead(["R01"])]],
        "memo": _memo([{"text": "One invoice appears settled by several credits.", "cites": ["L01", finding]}], []),
    })


def test_score_and_route_are_identical_with_team_on_and_off(
    tampered: CaseData, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _grounded(tampered)
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    files, manifest = _files("tampered")
    off = assess("tampered", files, manifest, team=None)
    on = assess("tampered", files, manifest, team=MODELS)
    assert on.ai_team is not None and [lead.outcome for lead in on.ai_team.leads] == ["kept"]
    assert on.risk == off.risk and on.routing == off.routing and on.readiness == off.readiness
    stage = next(s for s in on.stages if s.stage_id == "ai_team")
    assert (stage.status, stage.version) == ("ran", "sup-model + spec-model")


def test_bedrock_error_marks_team_failed_and_case_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    throttled = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Converse")
    fake = ScriptedBedrock({}, error=throttled)
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    result = assess("tampered", *_files("tampered"), team=MODELS)
    assert result.ai_team is not None and result.ai_team.status == "failed"
    assert result.ai_team.error is not None and result.ai_team.error.startswith("AWS call failed")
    assert result.ai_team.models == {"supervisor": "sup-model", "specialist": "spec-model"}
    stages = {s.stage_id: s.status for s in result.stages}
    assert stages["ai_team"] == "failed" and stages["score"] == "ran" and stages["projections"] == "ran"


def test_unexpected_error_marks_team_failed_without_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ScriptedBedrock({}, error=RuntimeError("secret internals"))
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    result = assess("clean", *_files("clean"), team=MODELS)
    assert result.ai_team is not None
    assert (result.ai_team.status, result.ai_team.error) == ("failed", "unexpected error; see the server log")


def test_team_off_records_stage_off() -> None:
    result = assess("clean", *_files("clean"), team=None)
    assert result.ai_team is None
    stage = next(s for s in result.stages if s.stage_id == "ai_team")
    assert (stage.status, stage.duration_ms) == ("off", 0)


def test_activity_is_passed_to_the_team(tampered: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _grounded(tampered)
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    seen: list[str] = []
    assess("tampered", *_files("tampered"), team=MODELS, on_activity=seen.append)
    assert seen


def test_trace_is_persisted_server_side_only(
    tampered: CaseData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "CASES_DIR", str(tmp_path / "cases"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    fake = _grounded(tampered)
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    monkeypatch.setattr(pipeline, "assess", functools.partial(pipeline.assess, team=MODELS))
    files, _ = _files("tampered")
    result = pipeline.run("CASE-TRACE", files, PACKS / "tampered" / "application.json")
    assert result.ai_team is not None

    case_dir = tmp_path / "cases" / "CASE-TRACE"
    trace = json.loads((case_dir / pipeline.AGENT_TRACE_FILE).read_text(encoding="utf-8"))
    assert set(trace) == {"R01", "R02"}
    stored = (case_dir / "result.json").read_text(encoding="utf-8")
    assert trace["R01"] not in stored and "trace" not in json.loads(stored)["ai_team"]

    details = read_events("CASE-TRACE")[-1].details
    assert details["ai_team"] == {
        "status": "ran", "models": {"supervisor": "sup-model", "specialist": "spec-model"},
        "tool_calls": 2, "leads_kept": 1, "leads_withheld": 0, "error": None, "strict_tools": [],
    }
    assert details["ai_team_models"] == details["ai_team"]["models"]
    assert {f"ai_team_{k}" for k in PROMPT_VERSIONS} <= set(details["prompt_versions"])


def test_a_rerun_without_a_trace_removes_the_earlier_runs_trace(
    tampered: CaseData, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R ids of the current result must never resolve to an older run's tool results."""
    monkeypatch.setattr(pipeline, "CASES_DIR", str(tmp_path / "cases"))
    result = assess("tampered", *_files("tampered"), team=None)
    trace_file = tmp_path / "cases" / "tampered" / pipeline.AGENT_TRACE_FILE
    pipeline._persist(result.model_copy(update={"ai_team": AgentTeam(status="ran", trace={"R01": "OLD"})}))
    assert trace_file.is_file()
    pipeline._persist(result.model_copy(update={"ai_team": AgentTeam(status="failed", error="x")}))
    assert not trace_file.exists()
    pipeline._persist(result)  # team off: nothing to remove, nothing raised
    assert not trace_file.exists()


def test_the_audit_names_the_models_that_ran_tools_strictly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "AGENT_STRICT_TOOL_MODELS", frozenset({"spec-model", "other"}))
    team = AgentTeam(status="ran", models={"supervisor": "sup-model", "specialist": "spec-model"})
    assert pipeline.team_ledger(team)["ai_team"]["strict_tools"] == ["spec-model"]
    assert pipeline.team_ledger(None) == {"ai_team": None}
