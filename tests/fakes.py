"""
tests/fakes.py
--------------
Shared test doubles for the AI team: a scripted bedrock-runtime client, so
no test calls AWS.
"""

from __future__ import annotations

import copy
import itertools
from typing import Any

from gldb.ai_team.tools import TOOLSETS

_tool_use_ids = itertools.count(1)


def use(name: str, **args: Any) -> dict[str, Any]:
    """A toolUse content block calling *name*, with a fresh toolUseId."""
    return {"toolUse": {"toolUseId": f"tu-{next(_tool_use_ids)}", "name": name, "input": args}}


class ScriptedBedrock:
    """Plays back Converse responses per agent. The agent is recognised by the
    tool names offered: submit_plan -> "plan", submit_memo -> "memo", else the
    agent whose TOOLSET matches. An exhausted script answers end_turn "done".

    *scripts* maps each key to its responses in order, each the content blocks
    of one assistant message. *requests* keeps every request received, per key,
    as a snapshot. If *error* is set, converse raises it."""

    def __init__(self, scripts: dict[str, list[list[dict[str, Any]]]], error: Exception | None = None) -> None:
        self._scripts = {key: list(responses) for key, responses in scripts.items()}
        self._error = error
        self.requests: dict[str, list[dict[str, Any]]] = {}

    def converse(self, **request: Any) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        key = self._key(request)
        # The caller keeps appending to its messages list: record what was sent, as sent
        self.requests.setdefault(key, []).append(copy.deepcopy(request))
        script = self._scripts.get(key)
        content = script.pop(0) if script else [{"text": "done"}]
        return {
            "output": {"message": {"role": "assistant", "content": content}},
            "stopReason": "tool_use" if any("toolUse" in block for block in content) else "end_turn",
            "usage": {"inputTokens": 10, "outputTokens": 5},
        }

    @staticmethod
    def _key(request: dict[str, Any]) -> str:
        offered = {spec["toolSpec"]["name"] for spec in request["toolConfig"]["tools"]}
        if "submit_plan" in offered:
            return "plan"
        if "submit_memo" in offered:
            return "memo"
        return next(agent for agent, names in TOOLSETS.items() if set(names) == offered)
