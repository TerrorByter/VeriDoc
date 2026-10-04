"""
gldb/ai_team/shadow.py
----------------------
The shadow case scorer and the log-only disposition gate.

The scorer is a deliberately boring experiment against the rule-based route
(the champion). One model call reads the case context and the leads code
verified (structured data, nothing else: no memo, no specialist prose, no
unverified lead, never the champion's score or route) and proposes a route
with reasons through one tool whose arguments are closed over the case. Code
validates the disposition; an invalid one is stored as failed, with no retry.
The result is logged beside the champion, which never reads it.

The gate records the route it would take and applies nothing: it never lowers
a route, and raises a fast-track case to standard review only on a verified
lead or on a shadow proposal to review.
"""

from __future__ import annotations

import json
import logging
from typing import Any, get_args

from botocore.exceptions import BotoCoreError, ClientError

from gldb.ai_team.prompts import SHADOW_SCORER
from gldb.ai_team.team import flagged
from gldb.ai_team.tools import CaseData, Ledger, _case_overview, result_json
from gldb.config import AGENT_STRICT_TOOL_MODELS
from gldb.llm_client import converse_turn, prompt_version, tool_spec
from gldb.schemas import GateDecision, Lead, ReviewPath, Routing, ShadowReason, ShadowScore

log = logging.getLogger(__name__)

TOOL_NAME = "submit_disposition"
#: Usage-log label of the scorer's one call: its own stage in the cost record
PURPOSE = "shadow_score"
NO_ISSUE = "no_issue"
_DESCRIPTION = ("Propose the review route for this case, with each reason's code and the finding and lead ids it "
                "rests on. no_issue stands alone.")
#: What the scorer is told of each lead: code-built fields only, never the agent's prose
_LEAD_FIELDS = {"lead_id", "pattern", "evidence_status", "invoice_number", "transaction_ids", "counterparty",
                "check_facts"}


def shadow_input(case: CaseData, leads: list[Lead]) -> dict[str, Any]:
    """What the scorer reads: the case context and the flags (leads code
    verified), as structured data. The champion's score and route are not in it."""
    context = _case_overview(case, {}, "supervisor", Ledger())
    return {
        "case": context,
        "leads": [lead.model_dump(mode="json", include=_LEAD_FIELDS) for lead in leads if flagged(lead)],
    }


def _allowed(case: CaseData, leads: list[Lead]) -> tuple[list[str], list[str]]:
    """The reason codes and evidence ids the scorer may use for *case*: its
    risky findings' checks and ids, and its flags' patterns and ids."""
    flags = [lead for lead in leads if flagged(lead)]
    codes = [*dict.fromkeys(e.check_id for e in case.risky),
             *dict.fromkeys(lead.pattern for lead in flags if lead.pattern), NO_ISSUE]
    ids = [*(e.evidence_id for e in case.risky), *(lead.lead_id for lead in flags)]
    return list(dict.fromkeys(codes)), ids


def disposition_schema(case: CaseData | None, leads: list[Lead]) -> dict[str, Any]:
    """The input schema of submit_disposition, closed over *case*: reason
    codes and evidence ids are enums of the case's own. With nothing to cite
    there is no evidence_ids argument (an empty enum cannot be satisfied).
    Without a case it stays open: the form a prompt version hashes."""
    code: dict[str, Any] = {"type": "string", "description": "A rule's check id, a verified pattern, or no_issue"}
    ids: dict[str, Any] = {"type": "array", "description": "Finding (E...) and lead (L...) ids the reason rests on",
                           "items": {"type": "string"}}
    allowed: list[str] = []
    if case is not None:
        codes, allowed = _allowed(case, leads)
        code["enum"] = codes
        ids["items"] = {"type": "string", "enum": allowed}
    properties: dict[str, Any] = {"code": code}
    if case is None or allowed:
        properties["evidence_ids"] = ids
    return {
        "type": "object",
        "properties": {
            "route": {"type": "string", "enum": list(get_args(ReviewPath))},
            "reasons": {"type": "array", "items": {
                "type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}},
        },
        "required": ["route", "reasons"],
        "additionalProperties": False,
    }


#: Content hash of the scorer's prompt and tool, recorded with its output
PROMPT_VERSION = prompt_version(SHADOW_SCORER, json.dumps(disposition_schema(None, []), sort_keys=True))


def _validated(raw: dict[str, Any], case: CaseData, leads: list[Lead]) -> tuple[ReviewPath, list[ShadowReason]]:
    """The route and reasons of a disposition that obeys the case's lists, or
    ValueError saying what is wrong. A strict-tool model cannot get these
    wrong; any other can, so code checks them all the same."""
    codes, ids = _allowed(case, leads)
    route = raw.get("route")
    if route not in get_args(ReviewPath):
        raise ValueError(f"unknown route: {route}")
    items = raw.get("reasons")
    if not isinstance(items, list) or not items:
        raise ValueError("no reasons")
    reasons: list[ShadowReason] = []
    for item in items:
        code = item.get("code") if isinstance(item, dict) else None
        cited = item.get("evidence_ids", []) if isinstance(item, dict) else []
        if code not in codes:
            raise ValueError(f"unknown reason code: {code}")
        if not isinstance(cited, list):
            raise ValueError("evidence_ids must be a list")
        if (unknown := next((c for c in cited if c not in ids), None)) is not None:
            raise ValueError(f"unknown evidence id: {unknown}")
        if code != NO_ISSUE and not cited:
            raise ValueError("a reason cites no evidence")
        reasons.append(ShadowReason(code=code, evidence_ids=cited))
    if any(r.code == NO_ISSUE for r in reasons) and len(reasons) > 1:
        raise ValueError("no_issue cannot sit beside another reason")
    return route, reasons


def run_shadow(case: CaseData, leads: list[Lead], model_id: str, *, client: Any | None = None) -> ShadowScore:
    """Ask *model_id* for a disposition of *case*. Never raises: an AWS
    error, no answer or an invalid disposition is a failed score with its
    reason, so the case completes without it."""
    failed = ShadowScore(status="failed", model=model_id, prompt_version=PROMPT_VERSION)
    try:
        spec = tool_spec(TOOL_NAME, _DESCRIPTION, disposition_schema(case, leads),
                         strict=model_id in AGENT_STRICT_TOOL_MODELS)
        task = result_json(shadow_input(case, leads))
        turn = converse_turn(model_id, SHADOW_SCORER, [{"role": "user", "content": [{"text": task}]}],
                             tools=[spec], purpose=PURPOSE, case_id=case.case_id, client=client)
        answer = next((u["input"] for u in turn.tool_uses if u["name"] == TOOL_NAME), None)
        if answer is None:
            return failed.model_copy(update={"error": "no disposition submitted"})
        route, reasons = _validated(answer, case, leads)
    except ValueError as exc:
        return failed.model_copy(update={"error": str(exc)})
    except (BotoCoreError, ClientError) as exc:
        log.exception("shadow scorer AWS call failed for %s", case.case_id)
        return failed.model_copy(update={"error": f"AWS call failed: {exc}"})
    except Exception:
        log.exception("shadow scorer failed for %s", case.case_id)
        return failed.model_copy(update={"error": "unexpected error; see the server log"})
    return ShadowScore(status="ran", route=route, reasons=reasons, model=model_id, prompt_version=PROMPT_VERSION)


def compared(shadow: ShadowScore, champion: Routing | None) -> ShadowScore:
    """*shadow* with whether its route is the champion's, worked out after
    the fact: the scorer never saw it."""
    if shadow.route is None or champion is None:
        return shadow
    return shadow.model_copy(update={"agrees_with_champion": shadow.route == champion.path})


def decide(champion: Routing, leads: list[Lead], shadow: ShadowScore | None) -> GateDecision:
    """The route the disposition gate would take, from the champion's route,
    the flags and the shadow proposal: never lower than the champion's, raised
    from fast track to standard review by a flag or a proposal to review."""
    if champion.path != "fast_track":
        return GateDecision(would_route=champion.path,
                            reasons=[f"Champion route {champion.path} kept: AI never lowers a route"])
    reasons = []
    if flags := [lead.lead_id for lead in leads if flagged(lead)]:
        reasons.append(f"Code-verified lead {', '.join(flags)}: would raise fast_track to standard_review")
    if shadow is not None and shadow.status == "ran" and shadow.route not in (None, "fast_track"):
        reasons.append(f"Shadow scorer proposes {shadow.route}: would raise fast_track to standard_review")
    if reasons:
        return GateDecision(would_route="standard_review", reasons=reasons)
    return GateDecision(would_route="fast_track", reasons=["Nothing raises the champion's fast_track route"])
