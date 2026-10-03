"""
tests/test_investigation.py
---------------------------
The investigation queue: findings grouped into analyst tasks, ranked, each
with its remedy and what resolving it would change. Stub extraction, no AWS.
"""

from __future__ import annotations

from pathlib import Path

from gldb.config import HARD_FLAG_CHECK_IDS
from gldb.pipeline import assess
from gldb.schemas import ApplicationManifest, CaseResult
from gldb.signal_library import signal_for

PACKS = Path(__file__).parent.parent / "data" / "packs"


def _assess(name: str) -> CaseResult:
    folder = PACKS / name
    manifest = ApplicationManifest.model_validate_json((folder / "application.json").read_text())
    return assess(name, sorted(folder.glob("*.pdf")), manifest)


def test_clean_pack_has_nothing_to_investigate() -> None:
    assert _assess("clean").investigation == []


def test_every_finding_is_in_exactly_one_task() -> None:
    result = _assess("tampered")
    tasks = result.investigation
    assert [t.task_id for t in tasks] == [f"T{i:02d}" for i in range(1, len(tasks) + 1)]
    in_tasks = [eid for t in tasks for eid in t.evidence_ids]
    findings = [e.evidence_id for e in result.evidence if e.severity != "info"]
    assert sorted(in_tasks) == sorted(findings)
    for task in tasks:
        checks = {e.check_id for e in result.evidence if e.evidence_id in task.evidence_ids}
        docs = {e.document for e in result.evidence if e.evidence_id in task.evidence_ids}
        assert len(checks) == 1 and docs == {task.document}
        assert task.remedy == signal_for(checks.pop()).remedy


def test_hard_flags_come_first_then_highest_points() -> None:
    tasks = _assess("tampered").investigation
    ranks = {"urgent": 0, "high": 1, "normal": 2}
    assert [ranks[t.priority] for t in tasks] == sorted(ranks[t.priority] for t in tasks)
    assert tasks[0].priority == "urgent"
    assert tasks[0].check_id in HARD_FLAG_CHECK_IDS
    for a, b in zip(tasks, tasks[1:]):
        if a.priority == b.priority:
            assert a.points >= b.points


def test_resolving_the_sanctions_hit_would_fast_track_the_case() -> None:
    result = _assess("sanctioned")
    assert result.routing is not None and result.routing.path == "enhanced_review"
    (task,) = result.investigation
    assert (task.check_id, task.priority) == ("SANCTIONS_MATCH", "urgent")
    assert (task.score_if_resolved, task.band_if_resolved, task.path_if_resolved) == (
        0, "Low", "fast_track",
    )


def test_readiness_items_are_tasks_worth_no_points() -> None:
    tasks = _assess("incomplete").investigation
    coverage = next(t for t in tasks if t.check_id == "BANK_COVERAGE")
    assert (coverage.category, coverage.points, coverage.priority) == ("completeness", 0, "normal")
    assert coverage.path_if_resolved == "standard_review"  # other form items still open
