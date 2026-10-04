"""
tests/test_stages.py
--------------------
Per-stage records: every assessment says which pipeline stages ran or were
switched off, how long each took and which version ran, and reports each
stage as it starts and finishes (for live progress). Stub extraction, no AWS.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gldb import pipeline
from gldb.intake import load_manifest
from gldb.pipeline import ASSESS_STAGES, StageCallback, assess, ignore_stage
from gldb.schemas import CaseResult
from gldb.signal_library import RULE_VERSION

PACKS = Path(__file__).parent.parent / "data" / "packs"


def _assess(pack: str = "clean", on_stage: StageCallback = ignore_stage) -> CaseResult:
    folder = PACKS / pack
    return assess("CASE-T", sorted(folder.glob("*.pdf")), load_manifest(folder), on_stage)


def test_every_stage_is_recorded_in_order() -> None:
    result = _assess()
    assert [s.stage_id for s in result.stages] == list(ASSESS_STAGES)


def test_switched_off_stages_say_so() -> None:
    stages = {s.stage_id: s for s in _assess().stages}
    for off in ("ocr", "prompt_screen", "semantic_review"):  # conftest turns these off
        assert (stages[off].status, stages[off].duration_ms) == ("off", 0)
    assert stages["extract"].status == "ran" and stages["extract"].version == "stub parser"
    assert stages["score"].version == RULE_VERSION


def test_progress_is_reported_as_each_stage_starts_and_ends() -> None:
    seen: list[tuple[str, str]] = []
    _assess(on_stage=lambda stage, state: seen.append((stage, state)))
    assert seen[:3] == [("ocr", "off"), ("intake", "running"), ("intake", "ran")]
    assert seen[-1] == ("semantic_review", "off")
    ran = [stage for stage, state in seen if state == "ran"]
    assert ran == [s for s in ASSESS_STAGES if s not in ("ocr", "prompt_screen", "semantic_review")]


def test_enabled_stage_records_its_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline, "PROMPT_SCREEN_ENABLED", True)
    monkeypatch.setattr(pipeline, "screen", lambda pages, case_id: {d: [] for d in pages})
    stage = next(s for s in _assess().stages if s.stage_id == "prompt_screen")
    assert stage.status == "ran"
    assert stage.version == f"{pipeline.GUARDRAIL_ID} v{pipeline.GUARDRAIL_VERSION}"
