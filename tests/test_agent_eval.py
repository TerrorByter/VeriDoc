"""
tests/test_agent_eval.py
------------------------
Tests for gldb/agent_eval.py: the AI team run over a temp packs directory on a
scripted Bedrock, scored against each pack's ground truth. No AWS calls.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import replace
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

import pytest

from gldb import agent_eval, llm_client
from gldb.agent_eval import AgentEvalRow, AgentPackResult
from gldb.ai_team.checks import bank_rows
from gldb.ai_team.team import TeamModels
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.usage import CaseCost
from tests.fakes import ScriptedBedrock, use

PACKS = Path(__file__).parent / "fixtures" / "packs"
#: Models with a price in config, so the cost of a case is known
MODELS = TeamModels(
    supervisor="global.anthropic.claude-sonnet-4-6",
    specialist="global.anthropic.claude-haiku-4-5-20251001-v1:0",
)
DONE = [{"text": "done"}]


@pytest.fixture(autouse=True)
def _isolated_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    monkeypatch.setenv("AGENT_EVAL_PATH", str(tmp_path / "agent_eval.json"))


def _packs(tmp_path: Path, *names: str) -> Path:
    folder = tmp_path / "packs"
    for name in names:
        shutil.copytree(PACKS / name, folder / name)
    return folder


def _script(monkeypatch: pytest.MonkeyPatch, transaction: list[list[dict[str, Any]]]) -> None:
    """Every call to the team goes to a fake whose transaction specialist
    plays *transaction*: its responses run pack after pack, in order."""
    fake = ScriptedBedrock({"transaction": transaction})
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)


def _search() -> list[dict[str, Any]]:
    return [use("search_bank_transactions", text="INV")]


def _test(cites: list[str]) -> dict[str, Any]:
    return {"cites": cites, "expected": "one credit of the invoice total", "observed": "several smaller credits"}


def _lead(cites: list[str], **fields: Any) -> list[dict[str, Any]]:
    args = {"kind": "pattern", "pattern": "split_payment", "hypothesis": "An invoice was settled by several credits",
            "tests": [_test(cites)], "investigation_status": "supported", **fields}
    return [use("record_lead", **args)]


@cache
def _tpp() -> dict[str, Any]:
    """The subject of the planted third-party payment: INV-1002 and its bank row."""
    folder = PACKS / "third_party_payment"
    pack = assess("tpp", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None).pack
    row = next(row_id for row_id, _, txn in bank_rows(pack) if "INV-1002" in txn.description)
    return {"invoice_number": "INV-1002", "transaction_ids": [row]}


def _triage(cites: list[str], finding: str, status: str) -> list[dict[str, Any]]:
    return [use("record_lead", kind="triage", hypothesis="The payment differs from the invoice",
                tests=[_test(cites)], finding_ids=[finding], investigation_status=status,
                missing_evidence=["the remitter's statement"])]


def _pack(status: str = "ran") -> AgentPackResult:
    return AgentPackResult(pack="clean", expected_leads=[], found_leads=[], triage_expected=0, triage_correct=0,
                           noise=0, withheld=0, leads=0, usd=None, seconds=1.0, status=status)


def _row(supervisor: str, specialist: str, noise: int = 0, statuses: tuple[str, ...] = ("ran",)) -> AgentEvalRow:
    return AgentEvalRow(
        evaluated_at="2026-10-04T00:00:00+00:00", supervisor=supervisor, specialist=specialist,
        packs=[_pack(s) for s in statuses],
        pattern_recall=1.0, triage_accuracy=1.0, noise=noise, withheld_rate=0.0,
        usd_per_case=Decimal("0.5"), seconds_per_case=1.0,
    )


def test_metrics_count_recall_noise_and_withheld(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packs = _packs(tmp_path, "clean", "third_party_payment")
    _script(monkeypatch, [
        # clean: a pattern lead that matches nothing planted
        _search(), _lead(["R01"]), DONE,
        # third_party_payment: the planted lead, and one that names a verdict, so is refused when recorded
        _search(), _lead(["R01"], pattern="third_party_payment", **_tpp()),
        _lead(["R01"], pattern="round_trip", hypothesis="Possible fraud on the invoice"), DONE,
    ])

    row = agent_eval.evaluate_team(MODELS, packs)

    clean, third = row.packs
    assert (clean.pack, clean.status, clean.expected_leads, clean.found_leads) == (
        "clean", "ran", [], ["transaction:split_payment"])
    assert (clean.noise, clean.withheld, clean.leads) == (1, 0, 1)
    assert (third.pack, third.status, third.expected_leads, third.found_leads) == (
        "third_party_payment", "ran", ["transaction:third_party_payment"], ["transaction:third_party_payment"])
    assert (third.noise, third.withheld, third.leads) == (0, 1, 2)
    assert (row.supervisor, row.specialist) == (MODELS.supervisor, MODELS.specialist)
    assert row.supported_recall == 1.0
    assert row.noise == 1
    assert row.withheld_rate == pytest.approx(1 / 3)
    assert row.triage_accuracy == 0.0  # nothing expected: a zero denominator reads 0.0
    assert clean.usd is not None and third.usd is not None and clean.usd > 0 and third.usd > 0
    assert row.usd_per_case == (clean.usd + third.usd) / 2
    assert row.seconds_per_case == pytest.approx((clean.seconds + third.seconds) / 2)
    assert row.evaluated_at


def _script_all(monkeypatch: pytest.MonkeyPatch, transaction: list[list[dict[str, Any]]],
                shadow: list[list[dict[str, Any]]]) -> None:
    """The transaction specialist plays *transaction* and the shadow scorer *shadow*, pack after pack."""
    fake = ScriptedBedrock({"transaction": transaction, "shadow": shadow})
    monkeypatch.setattr(llm_client, "_default_client", lambda: fake)


def _disposition(route: str, code: str, *ids: str) -> list[dict[str, Any]]:
    return [use("submit_disposition", route=route, reasons=[{"code": code, "evidence_ids": list(ids)}])]


def _both_statuses(*, planted_found: bool) -> list[list[dict[str, Any]]]:
    """On INV-1002 (paid by Harbourline) and INV-1001 (paid by its buyer), the agent says both are paid by a
    third party: code verifies the first and contradicts the second."""
    first = _lead(["R01"], pattern="third_party_payment", **_tpp())
    second = _lead(["R01"], pattern="third_party_payment", invoice_number="INV-1001",
                   transaction_ids=[_row_of("INV-1001")])
    return [_search(), first + second, DONE] if planted_found else [_search(), DONE]


@cache
def _row_of(invoice: str) -> str:
    folder = PACKS / "third_party_payment"
    pack = assess("tpp-row", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None).pack
    return next(row_id for row_id, _, txn in bank_rows(pack) if txn.description.endswith(invoice))


def test_verified_recall_flag_noise_observations_and_shadow_are_scored(monkeypatch: pytest.MonkeyPatch,
                                                                       tmp_path: Path) -> None:
    packs = _packs(tmp_path, "clean", "third_party_payment")
    _script_all(
        monkeypatch,
        [_search(), _lead(["R01"]), DONE,  # clean: an unverified split_payment lead
         *_both_statuses(planted_found=True)],
        [[_disposition("fast_track", "no_issue")[0]],  # clean: nothing to review
         [_disposition("standard_review", "third_party_payment", "L01")[0]]],
    )

    row = agent_eval.evaluate_team(replace(MODELS, shadow="shadow-model"), packs)

    clean, third = row.packs
    assert (clean.verified_leads, clean.flag_noise, clean.observations, clean.contradicted, clean.ai_questions) == (
        [], 0, 1, 0, 0)
    assert (clean.planted, clean.shadow_route, clean.would_route, clean.miss) == (False, "fast_track", "fast_track", False)
    assert third.verified_leads == ["transaction:third_party_payment"]
    assert (third.flag_noise, third.observations, third.contradicted) == (0, 0, 1)
    assert (third.planted, third.shadow_route, third.would_route, third.miss) == (
        True, "standard_review", "standard_review", False)
    assert row.shadow == "shadow-model"
    assert (row.verified_recall, row.flag_noise, row.miss_rate, row.auto_clear_rate) == (1.0, 0, 0.0, 0.5)
    assert row.supported_recall == 1.0 and row.noise == 1  # the unverified split_payment on clean is AI noise, not a flag


def test_a_planted_fraud_the_gate_sends_to_fast_track_is_a_miss(monkeypatch: pytest.MonkeyPatch,
                                                                tmp_path: Path) -> None:
    packs = _packs(tmp_path, "third_party_payment")
    _script_all(monkeypatch, _both_statuses(planted_found=False), [[_disposition("fast_track", "no_issue")[0]]])

    row = agent_eval.evaluate_team(replace(MODELS, shadow="shadow-model"), packs)

    (third,) = row.packs
    assert (third.planted, third.would_route, third.miss) == (True, "fast_track", True)
    assert (row.miss_rate, row.auto_clear_rate, row.verified_recall) == (1.0, 1.0, 0.0)


def test_the_scorer_alone_sending_a_planted_fraud_to_fast_track_is_a_miss_too(monkeypatch: pytest.MonkeyPatch,
                                                                              tmp_path: Path) -> None:
    # Code verifies the planted payment, so the gate raises the case; the scorer, which saw the flag, still said fast_track
    packs = _packs(tmp_path, "third_party_payment")
    _script_all(monkeypatch, _both_statuses(planted_found=True), [[_disposition("fast_track", "no_issue")[0]]])

    row = agent_eval.evaluate_team(replace(MODELS, shadow="shadow-model"), packs)

    (third,) = row.packs
    assert (third.would_route, third.shadow_route) == ("standard_review", "fast_track")
    assert (third.miss, third.shadow_miss) == (True, True)
    assert (third.shadow_agrees_champion, third.shadow_matches_truth) == (True, True)  # fast_track is the rule route
    assert (row.miss_rate, row.shadow_miss_rate) == (1.0, 1.0)
    assert (row.shadow_champion_agreement, row.shadow_truth_agreement) == (1.0, 1.0)


def test_a_flag_matching_nothing_planted_is_flag_noise(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packs = _packs(tmp_path, "clean")
    _script_all(monkeypatch, [_search(), _lead(["R01"], pattern="third_party_payment", **_tpp()), DONE], [])
    # The Harbourline row is not in the clean pack: the lead is refused when recorded, so no flag
    (clean,) = agent_eval.evaluate_team(MODELS, packs).packs
    assert (clean.flag_noise, clean.verified_leads) == (0, [])


def test_a_row_saved_before_the_new_metrics_still_loads() -> None:
    old = {"evaluated_at": "2026-10-04T00:00:00+00:00", "supervisor": "s", "specialist": "p", "packs": [],
           "pattern_recall": 0.5, "triage_accuracy": 1.0, "noise": 2, "withheld_rate": 0.0, "usd_per_case": None,
           "seconds_per_case": 1.0}
    row = AgentEvalRow.model_validate(old)
    # Not measured then: None, never a zero that reads as a result
    assert (row.supported_recall, row.verified_recall, row.flag_noise, row.shadow) == (0.5, None, None, None)
    assert (row.miss_rate, row.shadow_miss_rate, row.auto_clear_rate) == (None, None, None)
    pack = {"pack": "clean", "expected_leads": [], "found_leads": [], "triage_expected": 0, "triage_correct": 0,
            "noise": 0, "withheld": 0, "leads": 0, "usd": None, "seconds": 1.0, "status": "ran"}
    assert AgentPackResult.model_validate(pack).shadow_route is None


def test_a_clear_check_is_neither_a_found_pattern_nor_noise(monkeypatch: pytest.MonkeyPatch,
                                                              tmp_path: Path) -> None:
    same = {"cites": ["R01"], "expected": "paid by the buyer", "observed": "paid by the buyer"}
    _script(monkeypatch, [_search(), _lead(["R01"], tests=[same], investigation_status="refuted"), DONE])

    (clean,) = agent_eval.evaluate_team(MODELS, _packs(tmp_path, "clean")).packs

    assert (clean.found_leads, clean.noise, clean.withheld, clean.leads) == ([], 0, 0, 1)


@pytest.mark.parametrize(("status", "correct"), [("refuted", 1), ("supported", 0)])
def test_triage_is_correct_only_for_the_expected_status_on_the_expected_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str, correct: int
) -> None:
    packs = _packs(tmp_path, "innocent_split")
    folder = packs / "innocent_split"
    finding = next(
        e.evidence_id
        for e in assess("innocent_split", sorted(folder.glob("*.pdf")), load_manifest(folder), team=None).evidence
        if e.check_id == "XDOC_INVOICE_BANK" and e.severity != "info"
    )
    _script(monkeypatch, [_search(), _triage(["R01"], finding, status), DONE])

    row = agent_eval.evaluate_team(MODELS, packs)

    (result,) = row.packs
    assert (result.triage_expected, result.triage_correct) == (1, correct)
    assert row.triage_accuracy == correct
    assert row.supported_recall == 0.0  # the planted split_payment lead was not raised


def test_pack_that_raises_is_recorded_as_an_error_without_aborting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packs = _packs(tmp_path, "clean", "third_party_payment")
    _script(monkeypatch, [])
    real_assess = agent_eval.assess

    def flaky(case_id: str, *args: Any, **kwargs: Any) -> Any:
        if case_id.startswith("AGENTEVAL-third_party_payment-"):
            raise RuntimeError("model returned junk")
        return real_assess(case_id, *args, **kwargs)

    monkeypatch.setattr(agent_eval, "assess", flaky)

    row = agent_eval.evaluate_team(MODELS, packs)

    clean, third = row.packs
    assert clean.status == "ran"
    assert third.status == "error: RuntimeError: model returned junk"
    # Its expected lead still counts, as missed
    assert third.expected_leads == ["transaction:third_party_payment"] and third.found_leads == []
    assert (third.leads, third.noise, third.withheld, third.usd) == (0, 0, 0, None)
    assert row.supported_recall == 0.0
    assert row.usd_per_case == clean.usd  # the mean of the costs that are known


def test_usd_is_the_teams_cost_only(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packs = _packs(tmp_path, "clean", "third_party_payment")
    _script(monkeypatch, [])
    by_stage = {"AGENTEVAL-clean-": {"extract": Decimal("7"), "ai_team": Decimal("2")},
                "AGENTEVAL-third_party_payment-": {"extract": Decimal("7")}}
    monkeypatch.setattr(agent_eval, "case_cost", lambda case_id: CaseCost(
        case_id=case_id, last_seen="", usd=Decimal("9"),
        usd_by_stage=next(v for k, v in by_stage.items() if case_id.startswith(k))))

    clean, third = agent_eval.evaluate_team(MODELS, packs).packs

    assert (clean.usd, third.usd) == (Decimal("2"), None)  # extraction is not the team's cost


def test_failed_team_reports_its_error_as_the_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packs = _packs(tmp_path, "clean")
    monkeypatch.setattr(llm_client, "_default_client", lambda: ScriptedBedrock({}, error=RuntimeError("boom")))

    (result,) = agent_eval.evaluate_team(MODELS, packs).packs

    assert result.status == "failed: unexpected error; see the server log"


def test_pack_without_a_team_result_is_an_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    packs = _packs(tmp_path, "third_party_payment")
    real_assess = agent_eval.assess
    monkeypatch.setattr(agent_eval, "assess", lambda *args, **kwargs: real_assess(*args, **{**kwargs, "team": None}))

    (result,) = agent_eval.evaluate_team(MODELS, packs).packs

    assert result.status.startswith("error: ")
    assert result.found_leads == [] and result.leads == 0


def test_packs_needing_a_capability_that_is_off_are_skipped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packs = _packs(tmp_path, "clean", "third_party_payment")
    gt = packs / "clean" / "ground_truth.json"
    gt.write_text(json.dumps({**json.loads(gt.read_text()), "requires": ["ocr"]}))
    _script(monkeypatch, [])

    row = agent_eval.evaluate_team(MODELS, packs)

    assert [r.pack for r in row.packs] == ["third_party_payment"]


def test_save_replaces_same_setup_and_keeps_others(tmp_path: Path) -> None:
    agent_eval.save(_row("sonnet", "haiku", noise=1))
    agent_eval.save(_row("sonnet", "nova", noise=2))
    agent_eval.save(_row("sonnet", "haiku", noise=3))

    saved = agent_eval.load_saved()
    assert [(r.supervisor, r.specialist, r.noise) for r in saved] == [("sonnet", "nova", 2), ("sonnet", "haiku", 3)]
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith("agent_eval")] == ["agent_eval.json"]


def test_save_creates_the_directory_and_leaves_no_temp_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = tmp_path / "logs" / "nested"
    monkeypatch.setenv("AGENT_EVAL_PATH", str(folder / "agent_eval.json"))

    agent_eval.save(_row("sonnet", "haiku"))

    assert [p.name for p in folder.iterdir()] == ["agent_eval.json"]
    assert len(agent_eval.load_saved()) == 1


def test_load_saved_without_file_is_empty() -> None:
    assert agent_eval.load_saved() == []


@pytest.mark.parametrize("content", ["{not json", json.dumps([{"supervisor": "sonnet"}])])
def test_load_saved_of_a_malformed_or_stale_file_is_empty_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, content: str
) -> None:
    (tmp_path / "agent_eval.json").write_text(content, encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="gldb.agent_eval"):
        assert agent_eval.load_saved() == []

    assert "agent_eval.json" in caplog.text


def test_main_defaults_to_the_configured_models_and_prints_without_saving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[TeamModels] = []

    def fake_evaluate(models: TeamModels, packs_dir: Path) -> AgentEvalRow:
        seen.append(models)
        return _row(models.supervisor, models.specialist)

    monkeypatch.setattr(agent_eval, "evaluate_team", fake_evaluate)

    assert agent_eval.main([]) == 0

    assert seen == [TeamModels(agent_eval.AGENT_SUPERVISOR_MODEL, agent_eval.AGENT_SPECIALIST_MODEL,
                               agent_eval.SHADOW_SCORER_MODEL)]  # the scorer is always evaluated
    out = capsys.readouterr().out
    assert out.startswith("This calls Bedrock for every pack (estimated US$1-3).")
    assert agent_eval.AGENT_SUPERVISOR_MODEL in out
    assert agent_eval.load_saved() == []


def test_main_takes_models_and_saves_only_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(agent_eval, "evaluate_team", lambda models, _: _row(models.supervisor, models.specialist))

    assert agent_eval.main(["--supervisor", "big", "--specialist", "small", "--save"]) == 0

    assert [(r.supervisor, r.specialist) for r in agent_eval.load_saved()] == [("big", "small")]


def test_main_prints_the_table_before_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    monkeypatch.setattr(agent_eval, "evaluate_team", lambda models, _: _row(models.supervisor, models.specialist))
    monkeypatch.setattr(agent_eval, "_print", lambda row: events.append("print"))
    monkeypatch.setattr(agent_eval, "save", lambda row: events.append("save"))

    assert agent_eval.main(["--save"]) == 0

    assert events == ["print", "save"]


@pytest.mark.parametrize("statuses", [(), ("error: RuntimeError: x", "failed: AWS call failed")])
def test_main_fails_and_saves_nothing_when_no_pack_ran(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], statuses: tuple[str, ...]
) -> None:
    monkeypatch.setattr(agent_eval, "evaluate_team",
                        lambda models, _: _row(models.supervisor, models.specialist, statuses=statuses))

    assert agent_eval.main(["--save"]) == 1

    assert agent_eval.load_saved() == []
    out = capsys.readouterr().out
    assert "supported recall" in out and "no pack ran" in out  # the table still shows why


def test_main_prints_a_row_per_pack_with_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packs = _packs(tmp_path, "clean", "third_party_payment")
    monkeypatch.setattr(agent_eval, "PACKS_DIR", str(packs))
    _script(monkeypatch, [_search(), _lead(["R01"]), DONE, _search(), _lead(["R01"], pattern="third_party_payment", **_tpp()), DONE])

    assert agent_eval.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.startswith("pack"))
    third = next(line for line in lines if line.startswith("third_party_payment"))
    assert header.index("patterns") == third.index("1/1")  # long names do not run into the next column
    assert "supported recall 1.00" in "\n".join(lines)
