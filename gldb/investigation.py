"""
gldb/investigation.py
---------------------
The investigation queue: what the analyst should resolve, in what order,
how, and what resolving it would change.

Each task groups the findings of one signal on one document, names the
signal's remedy, and answers "what would change it?" by re-running the real
scoring, readiness and routing rules without those findings. The answer is
a calculation, never an estimate, so it cannot contradict the decision layer.

Order: urgent (a hard flag), then high (a high or critical finding), then
normal; within a priority, the most score points first.

Guidance only: the queue never closes a finding. People confirm or dismiss
flags, and documents re-enter the case after a request for information.
"""

from __future__ import annotations

from gldb.config import HARD_FLAG_CHECK_IDS
from gldb.routing import route
from gldb.schemas import Evidence, ExtractedPack, InvestigationTask, RiskResult, TaskPriority
from gldb.scoring import readiness, score
from gldb.signal_library import signal_for

_PRIORITY_RANK: dict[TaskPriority, int] = {"urgent": 0, "high": 1, "normal": 2}


def _task(
    case_id: str, findings: list[Evidence], evidence: list[Evidence], pack: ExtractedPack,
    risk: RiskResult,
) -> InvestigationTask:
    first = findings[0]
    signal = signal_for(first.check_id)
    ids = {e.evidence_id for e in findings}
    rest = [e for e in evidence if e.evidence_id not in ids]
    risk_after = score(rest)
    priority: TaskPriority
    if first.check_id in HARD_FLAG_CHECK_IDS:
        priority = "urgent"
    elif any(e.severity in ("high", "critical") for e in findings):
        priority = "high"
    else:
        priority = "normal"
    return InvestigationTask(
        task_id="",
        priority=priority,
        signal_id=signal.signal_id,
        check_id=first.check_id,
        name=signal.name,
        category=first.category,
        document=first.document,
        evidence_ids=[e.evidence_id for e in findings],
        remedy=signal.remedy,
        points=sum(c.points for c in risk.contributions if c.evidence_id in ids),
        score_if_resolved=risk_after.score,
        band_if_resolved=risk_after.band,
        path_if_resolved=route(case_id, rest, risk_after, readiness(rest, pack), pack).path,
    )


def build_queue(
    case_id: str, evidence: list[Evidence], pack: ExtractedPack, risk: RiskResult,
) -> list[InvestigationTask]:
    """One task per (check, document) with a non-info finding, ranked."""
    groups: dict[tuple[str, str], list[Evidence]] = {}
    for ev in evidence:
        if ev.severity != "info":
            groups.setdefault((ev.check_id, ev.document), []).append(ev)
    tasks = sorted(
        (_task(case_id, findings, evidence, pack, risk) for findings in groups.values()),
        key=lambda t: (_PRIORITY_RANK[t.priority], -t.points, t.signal_id, t.document),
    )
    return [t.model_copy(update={"task_id": f"T{i:02d}"}) for i, t in enumerate(tasks, start=1)]
