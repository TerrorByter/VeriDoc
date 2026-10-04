"""
gldb/ai_team/loop.py
--------------------
One agent's bounded tool-use loop on the Bedrock Converse API. The agent
calls its read-only tools until it hands in its answer (the finish tool) or
ends its turn; the step cap and the case's token budget stop one that does
neither. The cap counts successful evidence calls only: a failed call does not
use it (a mistyped argument costs nothing) but has its own cap of the same
size, so a confused agent still stops. Recording a lead or drafting a question
never uses the step cap, but a failed one counts towards the failed-call cap.
Reaching a cap does not end the agent on the spot: the evidence found by its
last call is worth recording. Every tool use of the turn is still answered
(an evidence call past the cap is refused with an error result, not run), and
the agent gets _GRACE_TURNS more model turns in which evidence calls are
refused the same way while record_lead and draft_question still run. The tool
list stays the same throughout, so the conversation stays valid. The agent
then ends when it stops calling tools or the grace turns are used up; the
token budget is still checked before every model call. A stop is a note in the
ledger, never an error: the team carries on with what it has.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gldb.ai_team.tools import RECEIPT_TOOLS, TITLES, CaseData, Ledger, call_tool, specs_for
from gldb.config import AGENT_MAX_STEPS, AGENT_STRICT_TOOL_MODELS
from gldb.llm_client import converse_turn
from gldb.schemas import AgentName

#: Told what the agent is doing, "Transaction specialist: search_bank_transactions", just before each tool call
Activity = Callable[[str], None]
#: Model turns an agent gets after reaching a cap, to record its conclusions
_GRACE_TURNS = 2
#: The error result of an evidence call made once a cap is reached: it is not run
_LIMIT_REACHED = (
    "Step limit reached: no more evidence tools. Record your conclusions now with record_lead "
    "(citing the R ids you already have) and draft_question, then end your turn."
)


def _limit(steps: int, failures: int) -> str | None:
    """Why an agent with *steps* successful evidence calls and *failures* failed ones may run no more, if so."""
    if steps >= AGENT_MAX_STEPS:
        return "step limit reached"
    if failures >= AGENT_MAX_STEPS:
        return "too many failed tool calls"
    return None


def run_agent(
    agent: AgentName,
    model_id: str,
    system: str,
    task: str,
    case: CaseData,
    ledger: Ledger,
    *,
    finish: str | None = None,
    on_activity: Activity = lambda _: None,
    client: Any | None = None,
) -> dict[str, Any] | None:
    """Run *agent* on *task* until it finishes or a limit stops it.

    Args:
        agent: Which agent: its tools, its title and its usage-log label.
        model_id: Bedrock model ID or inference profile to run it on.
        system: The agent's system prompt.
        task: Its first message: the brief.
        case: The case its tools read.
        ledger: Where its tool calls, tokens and stop notes are recorded.
        finish: Name of the tool that hands in its answer, if it has one.
        on_activity: Told each tool call just before it runs.
        client: Injectable bedrock-runtime client (tests). Defaults to boto3.

    Returns:
        The finish tool's input when the agent called it; None when it ended
        its turn without, or a cap (after its grace turns) or the token
        budget stopped it.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": task}]}]
    # Closed over the case: the model can only name documents, invoices and findings that exist
    tools = specs_for(agent, finish, case, strict=model_id in AGENT_STRICT_TOOL_MODELS)
    steps = failures = 0
    grace = _GRACE_TURNS
    noted = False
    while True:
        if ledger.over_budget():
            ledger.note(f"{agent}: budget reached")
            return None
        turn = converse_turn(model_id, system, messages, tools=tools,
                             purpose=f"ai_team_{agent}", case_id=case.case_id, client=client)
        ledger.add_tokens(turn.input_tokens + turn.output_tokens)
        messages.append({"role": "assistant", "content": turn.content})
        if not turn.tool_uses:
            return None
        # The answer ends the turn: nothing else the model asked for in it is run
        answer = next((u for u in turn.tool_uses if u["name"] == finish), None)
        if answer is not None:
            return answer["input"]
        results: list[dict[str, Any]] = []
        refused = False
        for tool_use in turn.tool_uses:
            evidence = tool_use["name"] not in RECEIPT_TOOLS
            if evidence and _limit(steps, failures):
                refused = True
                text, error = _LIMIT_REACHED, True
            else:
                on_activity(f"{TITLES[agent]}: {tool_use['name']}")
                text, error = call_tool(agent, tool_use["name"], tool_use["input"], case, ledger)
                steps += evidence and not error
                failures += error
            results.append({"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"text": text}],
                                           **({"status": "error"} if error else {})}})
        messages.append({"role": "user", "content": results})
        limit = _limit(steps, failures)
        if limit and not noted and (refused or not grace):
            ledger.note(f"{agent}: {limit}")
            noted = True
        if limit:
            if not grace:
                return None
            grace -= 1
