"""
gldb/agent_eval.py
------------------
Champion vs challenger evaluation of the AI team: the synthetic packs run
with the team on under one model setup (a Supervisor model plus a specialist
model), scored against each pack's ground truth. Two setups side by side
show whether a cheaper or newer model holds up: the champion is the setup in
use, the challenger the one being tried.

One AgentEvalRow per setup, each with an AgentPackResult per pack. Metrics:
pattern recall (planted patterns the team found), triage accuracy, noise
(kept pattern leads matching nothing planted), withheld rate (leads the
verifier dropped), and cost and time per case.

The rows are kept as a JSON list at AGENT_EVAL_PATH, for the API's controls
page. The rules-only evaluation (gldb.evaluate) never runs the team.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

from pydantic import BaseModel, TypeAdapter

from gldb.config import AGENT_EVAL_PATH


class AgentPackResult(BaseModel):
    pack: str
    #: "agent:pattern"
    expected_leads: list[str]
    #: Kept pattern leads, "agent:pattern"
    found_leads: list[str]
    triage_expected: int
    triage_correct: int
    #: Kept pattern leads not expected
    noise: int
    withheld: int
    leads: int
    usd: Decimal | None
    seconds: float
    #: ai_team.status, or "error: ..."
    status: str


class AgentEvalRow(BaseModel):
    evaluated_at: str
    supervisor: str
    specialist: str
    packs: list[AgentPackResult]
    #: Matched expected leads / expected leads
    pattern_recall: float
    #: Correct / expected triage
    triage_accuracy: float
    noise: int
    #: Withheld / leads
    withheld_rate: float
    usd_per_case: Decimal | None
    seconds_per_case: float


_ROWS = TypeAdapter(list[AgentEvalRow])


def _report_path() -> Path:
    return Path(os.environ.get("AGENT_EVAL_PATH", "").strip() or AGENT_EVAL_PATH)


def load_saved() -> list[AgentEvalRow]:
    """The saved evaluation rows, if any."""
    path = _report_path()
    return _ROWS.validate_json(path.read_text(encoding="utf-8")) if path.is_file() else []
