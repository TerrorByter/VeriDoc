"""
tests/test_ai_team_loop.py
--------------------------
One agent's bounded tool loop: it runs the model's tool calls, sends the
results back (errors flagged), and stops on a finish tool, the end of the
model's turn, the step cap (successful evidence calls), the cap on failed
calls or the token budget. A cap does not stop the agent at once: it gets a
few grace turns to record its conclusions, with evidence calls refused. A
scripted fake stands in for Bedrock, so no AWS call is made.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gldb.ai_team import loop, tools
from gldb.ai_team.loop import run_agent
from gldb.ai_team.tools import CaseData, Ledger
from gldb.intake import load_manifest
from gldb.pipeline import assess
from tests.fakes import ScriptedBedrock, use

CLEAN = Path(__file__).parent / "fixtures" / "packs" / "clean"
BRIEFS = {"forensics": "f", "transaction": "t", "kyc": "k", "timeline": "l"}


@pytest.fixture(scope="module")
def case() -> CaseData:
    result = assess("clean", sorted(CLEAN.glob("*.pdf")), load_manifest(CLEAN))
    return CaseData(result.case_id, result.pack, result.evidence)


def _search() -> dict[str, Any]:
    return use("search_bank_transactions", text="GIRO")


def _searches(n: int) -> list[list[dict[str, Any]]]:
    """*n* turns that each call the bank search once."""
    return [[_search()] for _ in range(n)]


def _run(fake: ScriptedBedrock, case: CaseData, ledger: Ledger, **kwargs: Any) -> dict[str, Any] | None:
    return run_agent("transaction", "model-1", "system", "task", case, ledger, client=fake, **kwargs)


def test_loop_runs_tools_and_returns_on_end_turn(case: CaseData) -> None:
    fake = ScriptedBedrock({"transaction": [*_searches(2), [{"text": "done"}]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.tool for c in ledger.tool_calls] == ["search_bank_transactions"] * 2
    first, second, third = fake.requests["transaction"]
    assert first["messages"] == [{"role": "user", "content": [{"text": "task"}]}]
    # The recorded requests are snapshots: the loop's later appends do not reach them
    assert len(second["messages"]) == 3 and len(third["messages"]) == 5
    sent = second["messages"][-1]
    assert sent["role"] == "user"
    result = sent["content"][0]["toolResult"]
    assert result["toolUseId"] == second["messages"][-2]["content"][0]["toolUse"]["toolUseId"]
    assert result["content"][0]["text"].startswith('<tool_result id="R01"') and "status" not in result


def test_step_cap_stops_the_loop(case: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    fake = ScriptedBedrock({"transaction": _searches(5)})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert len(ledger.tool_calls) == 2
    assert ledger.notes == ["transaction: step limit reached"]
    # The two turns that ran the calls, then the grace turns
    assert len(fake.requests["transaction"]) == 2 + loop._GRACE_TURNS


def test_step_cap_applies_within_a_turn_and_every_tool_use_is_answered(
    case: CaseData, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    fake = ScriptedBedrock({"transaction": [[_search(), _search(), _search()]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert len(ledger.tool_calls) == 1
    assert ledger.notes == ["transaction: step limit reached"]
    first, second = fake.requests["transaction"]
    asked = [b["toolUse"]["toolUseId"] for b in second["messages"][-2]["content"]]
    results = [b["toolResult"] for b in second["messages"][-1]["content"]]
    assert [r["toolUseId"] for r in results] == asked and len(asked) == 3
    assert [r.get("status") for r in results] == [None, "error", "error"]


def test_a_lead_recorded_in_the_turn_after_the_last_evidence_call_is_kept(
    case: CaseData, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    fake = ScriptedBedrock({"transaction": [[_search()], [_search()], [_lead()], [{"text": "done"}]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.tool for c in ledger.tool_calls] == ["search_bank_transactions"] * 2 + ["record_lead"]
    assert len(ledger.leads) == 1
    # Never blocked from anything it asked for: no stop to note
    assert ledger.notes == [] and len(fake.requests["transaction"]) == 4


def test_evidence_calls_in_grace_turns_are_refused_not_run(case: CaseData,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    fake = ScriptedBedrock({"transaction": [[_search()], [_search(), _lead()], [{"text": "done"}]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    # The refused call is neither run nor recorded (it takes no R id); the lead beside it is
    assert [c.tool for c in ledger.tool_calls] == ["search_bank_transactions", "record_lead"]
    assert len(ledger.leads) == 1 and ledger.notes == ["transaction: step limit reached"]
    refused, recorded = fake.requests["transaction"][2]["messages"][-1]["content"]
    assert refused["toolResult"]["status"] == "error"
    assert refused["toolResult"]["content"][0]["text"].startswith("Step limit reached: no more evidence tools.")
    assert "record_lead" in refused["toolResult"]["content"][0]["text"]
    assert "status" not in recorded["toolResult"]
    # The same tools are offered every turn: the refusal is in code
    configs = [r["toolConfig"] for r in fake.requests["transaction"]]
    assert configs[0] == configs[1] == configs[2]


def test_grace_turns_are_bounded(case: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    fake = ScriptedBedrock({"transaction": _searches(10)})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert len(ledger.tool_calls) == 1
    assert ledger.notes == ["transaction: step limit reached"]
    assert len(fake.requests["transaction"]) == 1 + loop._GRACE_TURNS


def test_the_token_budget_still_applies_in_grace_turns(case: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    # Each scripted turn costs 15 tokens: the budget allows the cap turn and the first grace turn only
    monkeypatch.setattr(tools, "AGENT_TOKEN_BUDGET", 20)
    fake = ScriptedBedrock({"transaction": _searches(5)})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert ledger.notes == ["transaction: step limit reached", "transaction: budget reached"]
    assert len(fake.requests["transaction"]) == 2


def _bad() -> dict[str, Any]:
    """A call that fails: get_chain without its invoice number."""
    return use("get_chain")


def _lead(cite: str = "R01") -> dict[str, Any]:
    test = {"cites": [cite], "expected": "one credit", "observed": "two credits"}
    return use("record_lead", kind="pattern", pattern="split_payment", hypothesis="h", tests=[test],
               investigation_status="supported")


def test_leads_and_questions_do_not_count_towards_the_step_cap(case: CaseData,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    question = use("draft_question", text="q", cites=["R01"], lead_id="L01")
    fake = ScriptedBedrock({"transaction": [[_search()], [_lead("R01"), question, _lead("R01")],
                                            [{"text": "done"}]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.tool for c in ledger.tool_calls] == ["search_bank_transactions", "record_lead", "draft_question",
                                                   "record_lead"]
    assert ledger.notes == [] and len(ledger.leads) == 2 and len(ledger.questions) == 1


def test_leads_are_recorded_even_in_the_turn_that_hits_the_cap(case: CaseData,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    fake = ScriptedBedrock({"transaction": [[_search(), _lead(), _search(), _lead()]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.tool for c in ledger.tool_calls] == ["search_bank_transactions", "record_lead", "record_lead"]
    assert len(ledger.leads) == 2 and ledger.notes == ["transaction: step limit reached"]
    assert len(fake.requests["transaction"]) == 2


def test_failed_calls_do_not_use_the_step_cap(case: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    fake = ScriptedBedrock({"transaction": [[_bad()], [_search()], [_search()], [{"text": "done"}]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.error for c in ledger.tool_calls] == [True, False, False] and ledger.notes == []
    assert len(fake.requests["transaction"]) == 4


def test_successful_calls_still_stop_at_the_cap_after_a_failure(case: CaseData,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    fake = ScriptedBedrock({"transaction": [[_bad()], *_searches(4)]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.error for c in ledger.tool_calls] == [True, False, False]
    assert ledger.notes == ["transaction: step limit reached"]


def test_failed_calls_are_capped_separately(case: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    fake = ScriptedBedrock({"transaction": [[_bad()] for _ in range(5)]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert len(ledger.tool_calls) == 2 and all(c.error for c in ledger.tool_calls)
    assert ledger.notes == ["transaction: too many failed tool calls"]
    assert len(fake.requests["transaction"]) == 2 + loop._GRACE_TURNS


def test_failed_leads_and_questions_count_towards_the_failure_cap(case: CaseData,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 2)
    # Each is missing its required arguments
    fake = ScriptedBedrock({"transaction": [[use("record_lead"), use("draft_question")], [_search()]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [(c.tool, c.error) for c in ledger.tool_calls] == [("record_lead", True), ("draft_question", True)]
    assert ledger.notes == ["transaction: too many failed tool calls"]
    assert len(fake.requests["transaction"]) == 3


def test_the_failure_cap_applies_within_a_turn_and_leads_are_still_recorded(
    case: CaseData, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "AGENT_MAX_STEPS", 1)
    fake = ScriptedBedrock({"transaction": [[_bad(), _search(), _bad(), _lead()]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert [c.tool for c in ledger.tool_calls] == ["get_chain", "record_lead"]
    assert ledger.notes == ["transaction: too many failed tool calls"]
    assert len(fake.requests["transaction"]) == 2


def test_budget_stops_the_loop(case: CaseData, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tools, "AGENT_TOKEN_BUDGET", 1)
    fake = ScriptedBedrock({"transaction": _searches(3)})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    assert ledger.notes == ["transaction: budget reached"]
    assert len(fake.requests["transaction"]) == 1


def test_finish_tool_returns_its_input(case: CaseData) -> None:
    fake = ScriptedBedrock({"plan": [[_search(), use("submit_plan", briefs=BRIEFS)]]})
    ledger = Ledger()

    plan = run_agent("supervisor", "model-1", "system", "task", case, ledger, finish="submit_plan", client=fake)

    assert plan == {"briefs": BRIEFS}
    # The finish tool is not run, and neither is anything else in its turn
    assert ledger.tool_calls == []
    assert len(fake.requests["plan"]) == 1


def test_a_model_that_never_finishes_returns_none(case: CaseData) -> None:
    fake = ScriptedBedrock({"memo": [[{"text": "all clear"}]]})

    assert run_agent("supervisor", "model-1", "system", "task", case, Ledger(), finish="submit_memo",
                     client=fake) is None


def test_tool_error_is_sent_back_with_error_status(case: CaseData) -> None:
    fake = ScriptedBedrock({"transaction": [[use("get_chain")], [{"text": "done"}]]})
    ledger = Ledger()

    assert _run(fake, case, ledger) is None

    result = fake.requests["transaction"][1]["messages"][-1]["content"][0]["toolResult"]
    assert result["status"] == "error"
    assert "missing argument: invoice_number" in result["content"][0]["text"]
    assert ledger.tool_calls[0].error


def test_activity_is_reported_before_each_call(case: CaseData) -> None:
    fake = ScriptedBedrock({"transaction": [[_search()], [use("get_chain", invoice_number="INV-0")]]})
    ledger = Ledger()
    seen: list[tuple[str, int]] = []

    _run(fake, case, ledger, on_activity=lambda text: seen.append((text, len(ledger.tool_calls))))

    assert seen == [("Transaction specialist: search_bank_transactions", 0),
                    ("Transaction specialist: get_chain", 1)]


def test_a_model_error_propagates(case: CaseData) -> None:
    fake = ScriptedBedrock({}, error=RuntimeError("throttled"))

    with pytest.raises(RuntimeError, match="throttled"):
        _run(fake, case, Ledger())


def _specs(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [spec["toolSpec"] for spec in request["toolConfig"]["tools"]]


def test_tools_are_closed_over_the_case_and_strict_only_for_allowlisted_models(
    case: CaseData, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(loop, "AGENT_STRICT_TOOL_MODELS", frozenset({"strict-model"}))
    fake = ScriptedBedrock({})

    run_agent("transaction", "strict-model", "system", "task", case, Ledger(), client=fake)
    run_agent("transaction", "model-1", "system", "task", case, Ledger(), client=fake)

    strict, plain = (_specs(r) for r in fake.requests["transaction"])
    # record_lead is the one tool left out of strict mode: Bedrock compiles every strict schema of a request into
    # one grammar capped at 300MB, and the live run on third_party_payment went over it. Code checks its arguments.
    assert all(spec.get("strict") is True for spec in strict if spec["name"] != "record_lead")
    assert "strict" not in next(spec for spec in strict if spec["name"] == "record_lead")
    assert not any("strict" in spec for spec in plain)
    chain = next(spec for spec in plain if spec["name"] == "get_chain")
    assert chain["inputSchema"]["json"]["properties"]["invoice_number"]["enum"] == \
        [i.invoice_number for i in case.pack.invoices]
