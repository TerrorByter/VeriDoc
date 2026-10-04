"""
gldb/ai_team/team.py
--------------------
The Supervisor-led team, hub and spoke: the Supervisor plans, our code fans
the four specialists out in parallel (each given only its brief and the case
index), the verifier gates their leads, code checks each kept lead's pattern
against the case data, and the Supervisor reconciles the flags (leads code
verified) into a memo that is gated in turn. Our code is the switchboard: no agent
talks to another, so every handoff is a logged record. Exceptions propagate;
the pipeline turns them into a failed team.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, get_args

from gldb.ai_team.checks import check_pattern, corroborate
from gldb.ai_team.loop import Activity, run_agent
from gldb.ai_team.prompts import DEFAULT_BRIEF, PROMPT_VERSIONS, SPECIALIST, SUPERVISOR_MEMO, SUPERVISOR_PLAN
from gldb.ai_team.tools import CaseData, Ledger, _case_overview, case_index, result_json
from gldb.ai_team.verify import Known, check_lead, check_point, check_question, known
from gldb.config import AGENT_SPECIALIST_MODEL, AGENT_SUPERVISOR_MODEL, AGENTS_ENABLED
from gldb.schemas import AgentName, AgentTeam, Lead, Memo, ReviewPoint, SpecialistName

_SPECIALISTS: tuple[SpecialistName, ...] = get_args(SpecialistName)
_PLAN_TASK = "Plan the investigation of this case."
#: What the Supervisor is told of each kept lead when it reconciles
_LEAD_FIELDS = {"lead_id", "agent", "kind", "finding_ids", "pattern", "invoice_number", "transaction_ids",
                "counterparty", "hypothesis", "tests", "investigation_status", "missing_evidence", "evidence_status",
                "check_facts"}


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


def _verified(lead: Lead, ledger: Ledger, case: CaseData, index: str) -> Lead:
    """*lead* with the verifier's outcome and, if kept, what code establishes
    about its pattern from the case data (whatever the agent concluded)."""
    findings = {e.evidence_id: e for e in case.risky}
    reason = check_lead(lead, ledger.issued_to(lead.agent), ledger.citable(lead.agent), ledger.results, findings,
                        index)
    if reason is not None:
        return lead.model_copy(update={"outcome": "withheld", "withheld_reason": reason})
    outcome = corroborate(lead, check_pattern(lead, case.pack, case.evidence), case.pack, case.evidence)
    return lead.model_copy(update={"outcome": "kept", "evidence_status": outcome.status,
                                   "check_facts": outcome.facts, "check_reason": outcome.reason})


def flagged(lead: Lead) -> bool:
    """Whether *lead* is a flag for the analyst: kept, verified or corroborated
    by code, and not merely confirming a rule finding (the rule's flag already
    stands). Only a flag may become a memo point, an applicant question or a
    shadow scorer input."""
    return (lead.outcome == "kept" and lead.evidence_status in ("verified", "corroborated")
            and "rule_finding" not in lead.check_facts)


def _verified_questions(ledger: Ledger, index: str, flags: list[Lead]) -> list[dict[str, Any]]:
    """The drafted questions that pass the verifier and rest only on results a
    flag of the same agent cites; a note for each that does not."""
    passed = []
    for question in ledger.questions:
        agent, text, cites = question["agent"], question["text"], question["cites"]
        reason = check_question(text, cites, ledger.issued_to(agent), ledger.citable(agent), ledger.results,
                                index)
        backed = {c for lead in flags if lead.agent == agent for c in lead.cites}
        if reason is None and not set(cites) <= backed:
            reason = "no verified lead rests on it"
        if reason is None:
            passed.append(question)
        else:
            ledger.note(f"{agent}: question withheld ({reason})")
    return passed


def _points(raw: Any, known_ids: set[str], grounding: Known) -> tuple[list[ReviewPoint], int]:
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
        if check_point(point, known_ids, grounding):
            kept.append(point)
    return kept, len(items) - len(kept)


def _memo(raw: dict[str, Any], known_ids: set[str], grounding: Known) -> Memo:
    observations, dropped_observations = _points(raw.get("observations"), known_ids, grounding)
    questions, dropped_questions = _points(raw.get("questions"), known_ids, grounding)
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

    findings = {e.evidence_id: e for e in case.risky}
    leads = [_verified(lead, ledger, case, index) for lead in ledger.leads]
    flags = [lead for lead in leads if flagged(lead)]

    # The Supervisor sees the flags and the questions resting on them, never a tool result or an unverified lead
    task = json.dumps({"leads": [lead.model_dump(mode="json", include=_LEAD_FIELDS) for lead in flags],
                       "questions": _verified_questions(ledger, index, flags)}, ensure_ascii=False)
    raw = run("supervisor", models.supervisor, SUPERVISOR_MEMO, task, "submit_memo")
    if raw is None:
        ledger.note("supervisor: no memo submitted")
        memo = Memo()
    else:
        known_ids = set(findings) | {lead.lead_id for lead in flags}
        overview = result_json(_case_overview(case, {}, "supervisor", ledger))
        cited = " ".join(ledger.results[c] for lead in flags for c in lead.cites)
        memo = _memo(raw, known_ids, known(overview, cited, index))

    return AgentTeam(
        status="ran", plan=dict(briefs), tool_calls=ledger.tool_calls, leads=leads, memo=memo,
        models={"supervisor": models.supervisor, "specialist": models.specialist},
        prompt_versions=PROMPT_VERSIONS, notes=ledger.notes, trace=ledger.results,
    )
