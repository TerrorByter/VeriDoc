"""
gldb/agent_eval.py
------------------
Champion vs challenger evaluation of the AI team: the synthetic packs run
with the team on under one model setup (a Supervisor model plus a specialist
model), scored against each pack's ground truth. Two setups side by side
show whether a cheaper or newer model holds up: the champion is the setup in
use, the challenger the one being tried.

One AgentEvalRow per setup, each with an AgentPackResult per pack. Metrics:
supported recall (planted patterns the team reported as supported) and
verified recall (planted patterns code verified), triage accuracy, noise
(kept supported pattern leads matching nothing planted) and flag noise (leads
code verified that match nothing planted: the false flags an analyst would
get; the target is 0), withheld rate (leads the verifier refused when they
were recorded or withheld at the end, out of all the team tried to record),
the AI observations code could not check, the agent's "supported" leads code
contradicted (false flags caught), the questions the memo would put to the
applicant, and the team's cost (its own Bedrock calls only) and time per case.

The shadow scorer and the log-only gate are scored too: the miss rate (packs
with planted fraud the gate would send to fast track; the target is 0) and
the auto-clear rate (packs the gate would send to fast track). Synthetic
packs prove the pipeline, not detection value.

The rows are kept as a JSON list at AGENT_EVAL_PATH, for the API's controls
page. The rules-only evaluation (gldb.evaluate) never runs the team.

Run:  python -m gldb.agent_eval [--supervisor MODEL] [--specialist MODEL] [--save]

It calls Bedrock for every pack, whatever AGENTS_ENABLED says. The models
default to the configured ones; --save keeps the row, replacing any saved row
of the same two models. The table is printed first; if no pack ran, nothing
is saved and the exit code is 1.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from pydantic import AliasChoices, BaseModel, Field, TypeAdapter, ValidationError

from gldb.ai_team.team import TeamModels, flagged
from gldb.config import (
    AGENT_EVAL_PATH,
    AGENT_SPECIALIST_MODEL,
    AGENT_SUPERVISOR_MODEL,
    PACKS_DIR,
    SHADOW_SCORER_MODEL,
)
from gldb.evaluate import _missing, report_path
from gldb.intake import load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, Evidence, ReviewPath
from gldb.usage import case_cost

log = logging.getLogger(__name__)


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
    #: Leads refused when recorded (a record_lead error) or withheld at the end
    withheld: int
    #: Leads the team tried to record
    leads: int
    #: Pattern leads code verified or corroborated, "agent:pattern" (the flags)
    verified_leads: list[str] = []
    #: Flags matching nothing planted: the false flags an analyst would get
    flag_noise: int = 0
    #: Kept leads code could not check that the agent called supported or unresolved
    observations: int = 0
    #: Leads the agent called supported that code contradicted: false flags caught
    contradicted: int = 0
    #: Questions for the applicant in the memo
    ai_questions: int = 0
    #: The pack plants a fraud (an expected lead, or a route above fast track)
    planted: bool = False
    shadow_route: ReviewPath | None = None
    #: The route the log-only gate would take
    would_route: ReviewPath | None = None
    #: A planted fraud the gate would send to fast track
    miss: bool = False
    #: The team's own cost (stage ai_team); None when unknown
    usd: Decimal | None
    seconds: float
    #: "ran", "failed: <the team's error>", or "error: ..."
    status: str


class AgentEvalRow(BaseModel):
    evaluated_at: str
    supervisor: str
    specialist: str
    #: The shadow scorer's model, None when it did not run
    shadow: str | None = None
    packs: list[AgentPackResult]
    #: Expected leads reported as supported / expected leads (saved before verification as pattern_recall)
    supported_recall: float = Field(validation_alias=AliasChoices("supported_recall", "pattern_recall"))
    #: Expected leads code verified / expected leads
    verified_recall: float = 0.0
    #: Flags matching nothing planted
    flag_noise: int = 0
    #: Packs with planted fraud the gate would send to fast track / packs with planted fraud
    miss_rate: float = 0.0
    #: Packs the gate would send to fast track / packs
    auto_clear_rate: float = 0.0
    #: Correct / expected triage
    triage_accuracy: float
    noise: int
    #: Withheld / leads
    withheld_rate: float
    usd_per_case: Decimal | None
    seconds_per_case: float


_ROWS = TypeAdapter(list[AgentEvalRow])


def _report_path() -> Path:
    return report_path("AGENT_EVAL_PATH", AGENT_EVAL_PATH)


def load_saved() -> list[AgentEvalRow]:
    """The saved evaluation rows, if any. A file that is malformed or from an
    older schema is reported in the log and read as none."""
    path = _report_path()
    if not path.is_file():
        return []
    try:
        return _ROWS.validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        log.warning("ignoring unreadable agent evaluation %s: %s", path, exc)
        return []


def save(row: AgentEvalRow) -> None:
    """Keep *row* with the saved rows of other setups (most recent last),
    replacing the row of the same Supervisor and specialist models. The file
    is swapped in whole, so a reader never sees half a report."""
    others = [r for r in load_saved() if (r.supervisor, r.specialist) != (row.supervisor, row.specialist)]
    path = _report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_bytes(_ROWS.dump_json([*others, row], indent=2))
    os.replace(temp, path)


def _ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _matched(result: AgentPackResult) -> int:
    return len(set(result.expected_leads) & set(result.found_leads))


def _verified(result: AgentPackResult) -> int:
    return len(set(result.expected_leads) & set(result.verified_leads))


def _pack_result(folder: Path, truth: dict, models: TeamModels, stamp: str) -> AgentPackResult:
    """Assess one pack with the team on and score its leads against its
    ground *truth*. An exception, or a result with no team, is reported on this
    pack instead of aborting the evaluation: the pack then counts everything
    expected as missed."""
    case_id = f"AGENTEVAL-{folder.name}-{stamp}"
    team: AgentTeam | None = None
    evidence: list[Evidence] = []
    shadow_route: ReviewPath | None = None
    would_route: ReviewPath | None = None
    start = perf_counter()
    try:
        result = assess(case_id, sorted(folder.glob("*.pdf")), load_manifest(folder), team=models)
        team, evidence = result.ai_team, result.evidence
        shadow_route = result.shadow.route if result.shadow else None
        would_route = result.gate.would_route if result.gate else None
        if team is None:
            status = "error: no AI team result"
        else:
            status = "ran" if team.status == "ran" else f"failed: {team.error}"
    except Exception as exc:  # noqa: BLE001 - report any pipeline failure per pack
        status = f"error: {type(exc).__name__}: {exc}"
    seconds = perf_counter() - start

    leads = team.leads if team else []
    kept = [lead for lead in leads if lead.outcome == "kept"]
    refused = sum(c.tool == "record_lead" and c.error for c in team.tool_calls) if team else 0
    expected = [f"{lead['agent']}:{lead['pattern']}" for lead in truth["expected_leads"]]
    # A refuted or unresolved lead records a check, not a pattern found
    found = [f"{lead.agent}:{lead.pattern}" for lead in kept if lead.kind == "pattern" and lead.investigation_status == "supported"]
    verified = [f"{lead.agent}:{lead.pattern}" for lead in kept if lead.kind == "pattern" and flagged(lead)]
    planted = bool(truth["expected_leads"]) or truth["expected_path"] != "fast_track"
    checks = {e.evidence_id: e.check_id for e in evidence}
    expected_triage = truth["expected_triage"]
    cost = case_cost(case_id)
    return AgentPackResult(
        pack=folder.name,
        expected_leads=expected,
        found_leads=found,
        triage_expected=len(expected_triage),
        triage_correct=sum(
            any(
                lead.kind == "triage" and lead.investigation_status == want["status"]
                and any(checks.get(f) == want["check_id"] for f in lead.finding_ids)
                for lead in kept
            )
            for want in expected_triage
        ),
        noise=sum(lead not in expected for lead in found),
        withheld=len(leads) - len(kept) + refused,
        leads=len(leads) + refused,
        verified_leads=verified,
        flag_noise=sum(v not in expected for v in verified),
        observations=sum(l.evidence_status == "unverified" and l.investigation_status != "refuted" for lead in kept),
        contradicted=sum(l.evidence_status == "contradicted" and l.investigation_status == "supported" for lead in kept),
        ai_questions=len(team.memo.questions) if team and team.memo else 0,
        planted=planted,
        shadow_route=shadow_route,
        would_route=would_route,
        miss=planted and would_route == "fast_track",
        usd=cost.usd_by_stage.get("ai_team") if cost else None,
        seconds=seconds,
        status=status,
    )


def evaluate_team(models: TeamModels, packs_dir: Path) -> AgentEvalRow:
    """Run the AI team on *models* over every pack in *packs_dir* whose
    required capabilities are on, each as its own case so its cost is metered
    on its own."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    truths = {
        folder: json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
        for folder in sorted(p for p in packs_dir.iterdir() if (p / "ground_truth.json").is_file())
    }
    results = [
        _pack_result(folder, truth, models, stamp)
        for folder, truth in truths.items()
        if not _missing(truth.get("requires", []))
    ]
    usds = [r.usd for r in results if r.usd is not None]
    return AgentEvalRow(
        evaluated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        supervisor=models.supervisor,
        specialist=models.specialist,
        shadow=models.shadow,
        packs=results,
        supported_recall=_ratio(sum(_matched(r) for r in results), sum(len(r.expected_leads) for r in results)),
        verified_recall=_ratio(sum(_verified(r) for r in results), sum(len(r.expected_leads) for r in results)),
        flag_noise=sum(r.flag_noise for r in results),
        miss_rate=_ratio(sum(r.miss for r in results), sum(r.planted for r in results)),
        auto_clear_rate=_ratio(sum(r.would_route == "fast_track" for r in results), len(results)),
        triage_accuracy=_ratio(sum(r.triage_correct for r in results), sum(r.triage_expected for r in results)),
        noise=sum(r.noise for r in results),
        withheld_rate=_ratio(sum(r.withheld for r in results), sum(r.leads for r in results)),
        usd_per_case=sum(usds, Decimal(0)) / len(usds) if usds else None,
        seconds_per_case=_ratio(sum(r.seconds for r in results), len(results)),
    )


def _usd(amount: Decimal | None) -> str:
    return f"{amount:.4f}" if amount is not None else "-"


def _print(row: AgentEvalRow) -> None:
    width = max([len("pack"), *(len(r.pack) for r in row.packs)]) + 2
    print(f"supervisor {row.supervisor}  specialist {row.specialist}")
    print(f"{'pack':<{width}}{'patterns':<10}{'verified':<10}{'triage':<8}{'noise':<7}{'flags':<7}{'withheld':<10}"
          f"{'shadow':<18}{'gate':<18}{'USD':<10}{'seconds':<9}status")
    for r in row.packs:
        patterns = f"{_matched(r)}/{len(r.expected_leads)}"
        verified = f"{_verified(r)}/{len(r.expected_leads)}"
        triage = f"{r.triage_correct}/{r.triage_expected}"
        withheld = f"{r.withheld}/{r.leads}"
        gate = f"{r.would_route or '-'}{' MISS' if r.miss else ''}"
        print(
            f"{r.pack:<{width}}{patterns:<10}{verified:<10}{triage:<8}{r.noise:<7}{r.flag_noise:<7}{withheld:<10}"
            f"{r.shadow_route or '-':<18}{gate:<18}{_usd(r.usd):<10}{r.seconds:<9.1f}{r.status}"
        )
    print(
        f"\nsupported recall {row.supported_recall:.2f}  verified recall {row.verified_recall:.2f}  "
        f"triage accuracy {row.triage_accuracy:.2f}  noise {row.noise}  flag noise {row.flag_noise}  "
        f"withheld rate {row.withheld_rate:.2f}\n"
        f"miss rate {row.miss_rate:.2f}  auto-clear rate {row.auto_clear_rate:.2f}  "
        f"USD per case {_usd(row.usd_per_case)}  seconds per case {row.seconds_per_case:.1f}"
    )


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gldb.agent_eval",
        description="Run the AI team over the packs on one model setup and score its leads.",
    )
    parser.add_argument("--supervisor", default=AGENT_SUPERVISOR_MODEL, help="Supervisor model ID")
    parser.add_argument("--specialist", default=AGENT_SPECIALIST_MODEL, help="specialist model ID")
    parser.add_argument("--shadow", default=SHADOW_SCORER_MODEL, help="shadow case scorer model ID")
    parser.add_argument("--save", action="store_true", help="keep the row for the API's controls page")
    args = parser.parse_args(argv)
    print("This calls Bedrock for every pack (estimated US$1-3).")
    # Always runs the team, whatever AGENTS_ENABLED says: evaluating it is the point
    row = evaluate_team(TeamModels(args.supervisor, args.specialist, args.shadow), Path(PACKS_DIR))
    _print(row)
    if not any(r.status == "ran" for r in row.packs):
        print("no pack ran: nothing saved")
        return 1
    if args.save:
        save(row)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
