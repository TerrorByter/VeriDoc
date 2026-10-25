"""
tests/test_shadow.py
--------------------
The shadow case scorer and the log-only disposition gate. The scorer reads
the case context and the leads code verified, as structured data, and nothing
else (no memo, no specialist prose, no unverified lead, never the champion's
score or route); its disposition is closed over the case and validated in
code; it never changes the champion. The gate only records the route it would
take: it never lowers one, and raises a fast-track case only on a verified
lead or a shadow proposal to review. Offline; scripted Bedrock.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from gldb import llm_client
from gldb.ai_team.checks import bank_rows
from gldb.ai_team.shadow import decide, disposition_schema, run_shadow, shadow_input
from gldb.ai_team.team import TeamModels, run_team
from gldb.ai_team.tools import CaseData
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, Lead, ReviewPath, Routing, ShadowScore
from tests.fakes import ScriptedBedrock, use

PACKS = Path(__file__).parent / "fixtures" / "packs"
MODELS = TeamModels(supervisor="sup-model", specialist="spec-model", shadow="shadow-model")
BRIEFS = {"forensics": "F", "transaction": "T", "kyc": "K", "timeline": "L"}


@cache
def _case(name: str) -> CaseData:
    folder = PACKS / name
    result = assess(f"shadow-{name}", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None)
    return CaseData(result.case_id, result.pack, result.evidence)


def _lead(cite: str, pattern: str, hypothesis: str, **fields: Any) -> dict[str, Any]:
    test = {"cites": [cite], "expected": "the documents agree", "observed": "they do not"}
    return use("record_lead", kind="pattern", pattern=pattern, hypothesis=hypothesis, tests=[test],
               investigation_status="supported", **fields)


@cache
def _team() -> tuple[CaseData, AgentTeam]:
    """A team on third_party_payment with L01 verified, L02 unverified and L03 contradicted, and a memo."""
    case = _case("third_party_payment")
    row = {i: next(r for r, _, t in bank_rows(case.pack) if t.description.endswith(f"INV-{i}"))
           for i in (1001, 1002)}
    fake = ScriptedBedrock({
        "plan": [[use("submit_plan", briefs=BRIEFS)]],
        "transaction": [[use("search_bank_transactions", text="INV")],
                        [_lead("R01", "third_party_payment", "Paid by a non-buyer", invoice_number="INV-1002",
                               transaction_ids=[row[1002]]),
                         _lead("R01", "split_payment", "SECRET-PROSE-ZEBRA"),
                         _lead("R01", "third_party_payment", "CONTRADICTED-ZEBRA", invoice_number="INV-1001",
                               transaction_ids=[row[1001]])]],
        "memo": [[use("submit_memo", observations=[{"text": "MEMO-PROSE-ZEBRA.", "cites": ["L01"]}],
                      questions=[])]],
    })
    team = run_team(case, TeamModels(supervisor="sup-model", specialist="spec-model"), client=fake)
    assert [(lead.lead_id, lead.evidence_status) for lead in team.leads] == [
        ("L01", "verified"), ("L02", "unverified"), ("L03", "contradicted")]
    return case, team


def _disposition(route: str, reasons: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    return [[use("submit_disposition", route=route, reasons=reasons)]]


def _routing(path: ReviewPath) -> Routing:
    return Routing(path=path, analyst_review=path != "fast_track", qa_sampled=False, edd_required=False,
                   hard_flags=[], low_confidence=[], reasons=[], policy_version="test")


def _verified(lead_id: str = "L01", **facts: str) -> Lead:
    test = {"cites": ["R01"], "expected": "e", "observed": "o"}
    return Lead.model_validate({
        "lead_id": lead_id, "agent": "transaction", "kind": "pattern", "pattern": "third_party_payment",
        "hypothesis": "h", "tests": [test], "investigation_status": "supported", "evidence_status": "verified",
        "check_facts": facts})


# ---------------------------------------------------------------------------
# What the scorer reads
# ---------------------------------------------------------------------------


def test_shadow_input_excludes_memo_prose_and_unverified_leads() -> None:
    case, team = _team()
    fake = ScriptedBedrock({"shadow": _disposition("standard_review", [
        {"code": "third_party_payment", "evidence_ids": ["L01"]}])})

    shadow = run_shadow(case, team.leads, "shadow-model", client=fake)

    assert shadow.status == "ran"
    (request,) = fake.requests["shadow"]
    text = request["messages"][0]["content"][0]["text"]
    sent = json.loads(text)
    assert set(sent) == {"case", "leads"}
    assert [lead["lead_id"] for lead in sent["leads"]] == ["L01"]
    assert set(sent["leads"][0]) == {"lead_id", "pattern", "evidence_status", "invoice_number", "transaction_ids",
                                     "counterparty", "check_facts"}
    for absent in ("SECRET-PROSE-ZEBRA", "CONTRADICTED-ZEBRA", "MEMO-PROSE-ZEBRA", "hypothesis", '"tests"',
                   '"L02"', '"L03"', '"band"', "fast_track", "standard_review", "enhanced_review"):
        assert absent not in text, absent
    assert "champion" not in request["system"][0]["text"].lower()
    assert set(shadow_input(case, team.leads)) == {"case", "leads"}


def test_the_disposition_is_closed_over_the_case() -> None:
    tampered = _case("tampered")
    risky = [e for e in tampered.evidence if e.severity != "info"]
    schema = disposition_schema(tampered, [_verified("L07")])
    reason = schema["properties"]["reasons"]["items"]
    assert schema["properties"]["route"]["enum"] == ["fast_track", "standard_review", "enhanced_review"]
    assert set(reason["properties"]["code"]["enum"]) == {e.check_id for e in risky} | {"third_party_payment",
                                                                                      "no_issue"}
    assert set(reason["properties"]["evidence_ids"]["items"]["enum"]) == {e.evidence_id for e in risky} | {"L07"}
    assert reason["required"] == ["code", "evidence_ids"] and reason["additionalProperties"] is False
    # Nothing to cite: only no_issue, and no evidence_ids to name
    clean = disposition_schema(_case("clean"), [])["properties"]["reasons"]["items"]
    assert clean["properties"]["code"]["enum"] == ["no_issue"] and "evidence_ids" not in clean["properties"]
    assert clean["required"] == ["code"]


@pytest.mark.parametrize(("route", "reasons", "error"), [
    ("standard_review", [{"code": "third_party_payment", "evidence_ids": ["L01"]},
                         {"code": "no_issue", "evidence_ids": []}], "no_issue cannot sit beside another reason"),
    ("standard_review", [{"code": "third_party_payment", "evidence_ids": []}], "a reason cites no evidence"),
    ("standard_review", [{"code": "made_up", "evidence_ids": ["L01"]}], "unknown reason code: made_up"),
    ("standard_review", [{"code": "third_party_payment", "evidence_ids": ["L09"]}], "unknown evidence id: L09"),
    ("approve", [{"code": "no_issue", "evidence_ids": []}], "unknown route: approve"),
    ("fast_track", [], "no reasons"),
    ("enhanced_review", [{"code": "no_issue", "evidence_ids": []}], "no_issue means fast_track"),
])
def test_an_invalid_disposition_is_stored_failed(route: str, reasons: list[dict[str, Any]], error: str) -> None:
    case, team = _team()
    shadow = run_shadow(case, team.leads, "shadow-model", client=ScriptedBedrock({"shadow": _disposition(route, reasons)}))
    assert (shadow.status, shadow.route, shadow.reasons, shadow.error) == ("failed", None, [], error)


def test_a_valid_disposition_is_stored_with_its_model_and_prompt_version() -> None:
    case, team = _team()
    fake = ScriptedBedrock({"shadow": _disposition("standard_review", [
        {"code": "third_party_payment", "evidence_ids": ["L01"]}])})
    shadow = run_shadow(case, team.leads, "shadow-model", client=fake)
    assert (shadow.status, shadow.route, shadow.model) == ("ran", "standard_review", "shadow-model")
    assert [(r.code, r.evidence_ids) for r in shadow.reasons] == [("third_party_payment", ["L01"])]
    assert len(shadow.prompt_version) == 12


def test_no_issue_alone_is_valid() -> None:
    shadow = run_shadow(_case("clean"), [], "shadow-model", client=ScriptedBedrock({
        "shadow": _disposition("fast_track", [{"code": "no_issue"}])}))
    assert (shadow.status, shadow.route) == ("ran", "fast_track")


def test_no_disposition_or_an_aws_error_is_stored_failed_never_raised() -> None:
    case, team = _team()
    silent = run_shadow(case, team.leads, "shadow-model", client=ScriptedBedrock({"shadow": [[{"text": "ok"}]]}))
    assert (silent.status, silent.error) == ("failed", "no disposition submitted")
    boom = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "Converse")
    broken = run_shadow(case, team.leads, "shadow-model", client=ScriptedBedrock({}, error=boom))
    assert broken.status == "failed" and broken.error is not None and "ThrottlingException" in broken.error


def test_strict_tool_use_only_for_models_that_support_it(monkeypatch: pytest.MonkeyPatch) -> None:
    case, team = _team()
    from gldb.ai_team import shadow
    for model, strict in (("strict-model", True), ("plain-model", False)):
        monkeypatch.setattr(shadow, "AGENT_STRICT_TOOL_MODELS", frozenset({"strict-model"}))
        fake = ScriptedBedrock({"shadow": [[{"text": "ok"}]]})
        run_shadow(case, team.leads, model, client=fake)
        spec = fake.requests["shadow"][0]["toolConfig"]["tools"][0]["toolSpec"]
        assert spec.get("strict", False) is strict


# ---------------------------------------------------------------------------
# In the pipeline: the champion never moves
# ---------------------------------------------------------------------------


def _assess(name: str, team: TeamModels | None, monkeypatch: pytest.MonkeyPatch, fake: ScriptedBedrock | None = None):
    if fake is not None:
        monkeypatch.setattr(llm_client, "_default_client", lambda: fake)
    folder = PACKS / name
    return assess(f"shadow-pipeline-{name}", sorted(folder.glob("*.pdf")), load_manifest(folder), team=team)


def test_shadow_never_changes_the_champion(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ScriptedBedrock({"shadow": _disposition("fast_track", [{"code": "no_issue"}])})
    on = _assess("tampered", MODELS, monkeypatch, fake)
    off = _assess("tampered", None, monkeypatch)
    assert (on.risk, on.readiness, on.routing) == (off.risk, off.readiness, off.routing)
    assert on.shadow is not None and on.shadow.status == "ran" and on.shadow.route == "fast_track"
    # The tampered pack's rule route is above fast track: the scorer disagrees, and the champion is untouched
    assert on.routing is not None and on.routing.path != "fast_track" and on.shadow.agrees_with_champion is False
    stages = {s.stage_id: s.status for s in on.stages}
    assert stages["shadow_score"] == "ran"
    assert off.shadow is None and off.gate is None


def test_a_failed_shadow_scorer_never_stops_the_case(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ScriptedBedrock({"shadow": [[{"text": "no tool call"}]]})
    result = _assess("clean", MODELS, monkeypatch, fake)
    assert result.shadow is not None and result.shadow.status == "failed" and result.routing is not None
    assert {s.stage_id: s.status for s in result.stages}["shadow_score"] == "failed"
    assert result.gate is not None and result.gate.would_route == result.routing.path


def test_the_scorer_is_off_when_the_team_is_off_or_has_no_scorer_model(monkeypatch: pytest.MonkeyPatch) -> None:
    no_scorer = TeamModels(supervisor="sup-model", specialist="spec-model")
    for team in (None, no_scorer):
        result = _assess("clean", team, monkeypatch, ScriptedBedrock({}))
        assert result.shadow is None
        assert {s.stage_id: s.status for s in result.stages}["shadow_score"] == "off"


def test_the_audit_entry_logs_the_gate_even_when_the_scorer_is_off() -> None:
    from gldb.pipeline import shadow_ledger
    from gldb.schemas import GateDecision
    gate = GateDecision(would_route="standard_review", reasons=["a flag"])
    assert shadow_ledger(None, gate) == {"shadow": None, "gate_would_route": "standard_review"}
    ran = ShadowScore(status="ran", route="fast_track", model="m", prompt_version="v", agrees_with_champion=True)
    logged = shadow_ledger(ran, gate)
    assert logged["gate_would_route"] == "standard_review"
    assert logged["shadow"] == {"status": "ran", "model": "m", "prompt_version": "v", "route": "fast_track",
                                "agrees_with_champion": True, "error": None}
    assert shadow_ledger(None, None) == {"shadow": None, "gate_would_route": None}


# ---------------------------------------------------------------------------
# The log-only gate
# ---------------------------------------------------------------------------


def _shadow(route: ReviewPath | None, status: str = "ran") -> ShadowScore:
    return ShadowScore(status="ran" if status == "ran" else "failed", route=route, model="m", prompt_version="v")


@pytest.mark.parametrize("champion", ["standard_review", "enhanced_review"])
def test_the_gate_never_lowers_a_route(champion: ReviewPath) -> None:
    decision = decide(_routing(champion), [_verified()], _shadow("fast_track"))
    assert decision.would_route == champion and decision.reasons == [f"Champion route {champion} kept: AI never lowers a route"]


def test_a_fast_track_case_with_nothing_raising_it_stays_fast_track() -> None:
    assert decide(_routing("fast_track"), [], None).would_route == "fast_track"
    assert decide(_routing("fast_track"), [], _shadow("fast_track")).would_route == "fast_track"
    assert decide(_routing("fast_track"), [], _shadow(None, "failed")).would_route == "fast_track"


@pytest.mark.parametrize("status", ["verified", "corroborated"])
def test_a_verified_or_corroborated_lead_raises_fast_track_to_standard_review(status: str) -> None:
    lead = _verified().model_copy(update={"evidence_status": status})
    decision = decide(_routing("fast_track"), [lead], None)
    assert decision.would_route == "standard_review" and "L01" in decision.reasons[0]


@pytest.mark.parametrize("shadow_route", ["standard_review", "enhanced_review"])
def test_a_shadow_proposal_to_review_raises_fast_track_to_standard_review_only(shadow_route: ReviewPath) -> None:
    decision = decide(_routing("fast_track"), [], _shadow(shadow_route))
    assert decision.would_route == "standard_review" and shadow_route in decision.reasons[0]


def test_leads_that_are_not_flags_never_raise() -> None:
    unverified = _verified("L02").model_copy(update={"evidence_status": "unverified"})
    contradicted = _verified("L03").model_copy(update={"evidence_status": "contradicted"})
    withheld = _verified("L04").model_copy(update={"outcome": "withheld", "evidence_status": None})
    confirms_a_rule = _verified("L05", rule_finding="E07")
    leads = [unverified, contradicted, withheld, confirms_a_rule]
    assert decide(_routing("fast_track"), leads, None).would_route == "fast_track"
