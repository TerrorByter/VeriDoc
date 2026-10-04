"""
gldb/jobs.py
------------
Progress of pipeline runs started by the API, so the browser can show which
stage a case is on while it runs.

The API answers a new case at once and runs the pipeline in the background;
pipeline.run() reports each stage here through its on_stage callback, and
the browser polls GET /api/cases/{id}/progress.

Prototype limits: progress lives in this process's memory, so it is lost on
restart (a finished case still reports "done" from its stored result) and is
not shared between server workers. In production this is a job queue (SQS or
Step Functions) with progress in a shared store.
"""

from __future__ import annotations

from threading import Lock
from typing import Literal

from pydantic import BaseModel

from gldb.schemas import StageId, StageState

RunState = Literal["running", "done", "failed"]


class StageProgress(BaseModel):
    stage_id: StageId
    state: StageState


class Progress(BaseModel):
    """Where one run is: each stage reported so far, in the order reported."""

    case_id: str
    state: RunState
    stages: list[StageProgress] = []
    #: The stage running now; None when no stage is running
    current: StageId | None = None
    #: What the running stage is doing now (e.g. an agent's tool call); cleared
    #: when a stage changes state
    detail: str | None = None
    error: str | None = None


_lock = Lock()
_runs: dict[str, Progress] = {}


def start(case_id: str) -> None:
    with _lock:
        _runs[case_id] = Progress(case_id=case_id, state="running")


def stage(case_id: str, stage_id: StageId, state: StageState) -> None:
    """Record that *stage_id* of the case's run is now in *state*."""
    with _lock:
        run = _runs[case_id]
        stages = [s for s in run.stages if s.stage_id != stage_id]
        at = next((i for i, s in enumerate(run.stages) if s.stage_id == stage_id), len(stages))
        stages.insert(at, StageProgress(stage_id=stage_id, state=state))
        _runs[case_id] = run.model_copy(update={
            "stages": stages, "current": stage_id if state == "running" else None, "detail": None,
        })


def detail(case_id: str, text: str) -> None:
    """Record what the case's running stage is doing now. Called from worker
    threads; a case with no run is ignored."""
    with _lock:
        run = _runs.get(case_id)
        if run is not None:
            _runs[case_id] = run.model_copy(update={"detail": text})


def finish(case_id: str) -> None:
    with _lock:
        _runs[case_id] = _runs[case_id].model_copy(update={"state": "done", "current": None})


def fail(case_id: str, error: str) -> None:
    with _lock:
        _runs[case_id] = _runs[case_id].model_copy(
            update={"state": "failed", "current": None, "error": error}
        )


def get(case_id: str) -> Progress | None:
    with _lock:
        return _runs.get(case_id)


def unfinished() -> list[Progress]:
    """Runs still going or failed (neither has a stored result), for the case list."""
    with _lock:
        return [p for p in _runs.values() if p.state != "done"]


def forget(case_id: str) -> None:
    """Drop the case's progress, as a server restart would."""
    with _lock:
        _runs.pop(case_id, None)
