"""
gldb/ai_team/loop.py
--------------------
One agent's bounded tool-use loop on the Bedrock Converse API. The agent
calls its read-only tools until it hands in its answer (the finish tool) or
ends its turn; the step cap and the case's token budget stop one that does
neither. The cap counts successful evidence calls only: a failed call does not
use it (a mistyped argument costs nothing) but has its own cap of the same
size, so a confused agent still stops. Recording a lead or drafting a question
never uses the step cap, but a failed one counts towards the failed-call cap,
and is still run in the turn that hits a cap. A stop is a note in the ledger,
never an error: the team carries on with what it has.
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
        its turn without, or a cap or the token budget stopped it.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": [{"text": task}]}]
    # Closed over the case: the model can only name documents, invoices and findings that exist
    tools = specs_for(agent, finish, case, strict=model_id in AGENT_STRICT_TOOL_MODELS)
    steps = failures = 0
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
        stopped = None
        for tool_use in turn.tool_uses:
            evidence = tool_use["name"] not in RECEIPT_TOOLS
            if evidence and steps >= AGENT_MAX_STEPS:
                stopped = "step limit reached"
                continue
            if evidence and failures >= AGENT_MAX_STEPS:
                stopped = "too many failed tool calls"
                continue
            on_activity(f"{TITLES[agent]}: {tool_use['name']}")
            text, error = call_tool(agent, tool_use["name"], tool_use["input"], case, ledger)
            steps += evidence and not error
            failures += error
            results.append({"toolResult": {"toolUseId": tool_use["toolUseId"], "content": [{"text": text}],
                                           **({"status": "error"} if error else {})}})
        if stopped:
            ledger.note(f"{agent}: {stopped}")
            return None
        messages.append({"role": "user", "content": results})
