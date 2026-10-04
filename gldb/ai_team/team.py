"""
gldb/ai_team/team.py
--------------------
The Supervisor-led team, hub and spoke: the Supervisor plans, our code fans
the four specialists out in parallel (each given only its brief and the case
index), the verifier gates their leads, and the Supervisor reconciles the kept
leads into a memo that is gated in turn. Our code is the switchboard: no agent
talks to another, so every handoff is a logged record. Exceptions propagate;
the pipeline turns them into a failed team.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, get_args

from gldb.ai_team.loop import Activity, run_agent
from gldb.ai_team.prompts import DEFAULT_BRIEF, PROMPT_VERSIONS, SPECIALIST, SUPERVISOR_MEMO, SUPERVISOR_PLAN
from gldb.ai_team.tools import CaseData, Ledger, _case_overview, case_index, result_json
from gldb.ai_team.verify import check_lead, check_point, check_question, numbers
from gldb.config import AGENT_SPECIALIST_MODEL, AGENT_SUPERVISOR_MODEL, AGENTS_ENABLED
from gldb.schemas import AgentName, AgentTeam, Evidence, Lead, Memo, ReviewPoint, SpecialistName

_SPECIALISTS: tuple[SpecialistName, ...] = get_args(SpecialistName)
_PLAN_TASK = "Plan the investigation of this case."
#: What the Supervisor is told of each kept lead when it reconciles
_LEAD_FIELDS = {"lead_id", "agent", "kind", "verdict", "pattern", "title", "detail", "finding_ids"}


@dataclass(frozen=True)
class TeamModels:
    """The Bedrock model IDs (or inference profiles) the team runs on."""

    supervisor: str
    specialist: str


#: The team as configured, or None when it is switched off
CONFIGURED_TEAM: TeamModels | None = (
    TeamModels(AGENT_SUPERVISOR_MODEL, AGENT_SPECIALIST_MODEL) if AGENTS_ENABLED else None
)


def _briefs(plan: dict[str, Any] | None) -> dict[SpecialistName, str]:
    """Each specialist's brief from the plan; DEFAULT_BRIEF where it is
    missing, empty or not text (the plan may be cut short or malformed)."""
    given = plan.get("briefs") if plan else None
    if not isinstance(given, dict):
        given = {}
    return {s: b if isinstance(b := given.get(s), str) and b.strip() else DEFAULT_BRIEF for s in _SPECIALISTS}


def _verified(lead: Lead, ledger: Ledger, findings: dict[str, Evidence]) -> Lead:
    reason = check_lead(lead, ledger.issued_to(lead.agent), ledger.citable(lead.agent), ledger.results, findings)
    return lead.model_copy(update={"outcome": "kept" if reason is None else "withheld",
                                   "withheld_reason": reason})


def _verified_questions(ledger: Ledger) -> list[dict[str, Any]]:
    """The drafted questions that pass the verifier; a note for each that does not."""
    passed = []
    for question in ledger.questions:
        agent, text, cites = question["agent"], question["text"], question["cites"]
        reason = check_question(text, cites, ledger.issued_to(agent), ledger.citable(agent), ledger.results)
        if reason is None:
            passed.append(question)
        else:
            ledger.note(f"{agent}: question withheld ({reason})")
    return passed


def _points(raw: Any, known_ids: set[str], known_numbers: set[str]) -> tuple[list[ReviewPoint], int]:
    """The well-formed points of one memo list that pass the verifier, and how
    many were dropped."""
    items = raw if isinstance(raw, list) else []
    kept: list[ReviewPoint] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text, cites = item.get("text"), item.get("cites")
        if not isinstance(text, str) or not isinstance(cites, list) or not all(isinstance(c, str) for c in cites):
            continue
        point = ReviewPoint(text=text, evidence_ids=cites)
        if check_point(point, known_ids, known_numbers):
            kept.append(point)
    return kept, len(items) - len(kept)


def _memo(raw: dict[str, Any], known_ids: set[str], known_numbers: set[str]) -> Memo:
    observations, dropped_observations = _points(raw.get("observations"), known_ids, known_numbers)
    questions, dropped_questions = _points(raw.get("questions"), known_ids, known_numbers)
    return Memo(observations=observations, questions=questions,
                withheld=dropped_observations + dropped_questions)


def run_team(case: CaseData, models: TeamModels, *, on_activity: Activity = lambda _: None,
             client: Any | None = None) -> AgentTeam:
    """Run the team on *case*: plan, fan out, verify leads, reconcile, verify
    the memo. The team never changes a finding, the score or the route."""
    ledger = Ledger()

    def run(agent: AgentName, model_id: str, system: str, task: str,
            finish: str | None = None) -> dict[str, Any] | None:
        return run_agent(agent, model_id, system, task, case, ledger, finish=finish, on_activity=on_activity,
                         client=client)

    plan = run("supervisor", models.supervisor, SUPERVISOR_PLAN, _PLAN_TASK, "submit_plan")
    if plan is None:
        ledger.note("supervisor: no plan submitted, default briefs used")
    briefs = _briefs(plan)

    # Hub and spoke: each specialist's task is its brief plus the case index, which is case data
    index = case_index(case)
    with ThreadPoolExecutor(max_workers=len(_SPECIALISTS)) as pool:
        list(pool.map(lambda s: run(s, models.specialist, SPECIALIST[s], f"{briefs[s]}\n\n{index}"),
                      _SPECIALISTS))

    findings = {e.evidence_id: e for e in case.evidence if e.severity != "info"}
    leads = [_verified(lead, ledger, findings) for lead in ledger.leads]
    kept = [lead for lead in leads if lead.outcome == "kept"]

    # The Supervisor sees the kept leads and verified questions, never a tool result
    task = json.dumps({"leads": [lead.model_dump(mode="json", include=_LEAD_FIELDS) for lead in kept],
                       "questions": _verified_questions(ledger)}, ensure_ascii=False)
    raw = run("supervisor", models.supervisor, SUPERVISOR_MEMO, task, "submit_memo")
    if raw is None:
        ledger.note("supervisor: no memo submitted")
        memo = Memo()
    else:
        known_ids = set(findings) | {lead.lead_id for lead in kept}
        overview = result_json(_case_overview(case, {}, "supervisor", ledger))
        cited = " ".join(ledger.results[c] for lead in kept for c in lead.cites)
        memo = _memo(raw, known_ids, numbers(overview) | numbers(cited))

    return AgentTeam(
        status="ran", plan=dict(briefs), tool_calls=ledger.tool_calls, leads=leads, memo=memo,
        models={"supervisor": models.supervisor, "specialist": models.specialist},
        prompt_versions=PROMPT_VERSIONS, notes=ledger.notes, trace=ledger.results,
    )
