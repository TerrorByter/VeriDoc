"""
gldb/pipeline.py
----------------
Pipeline orchestration for the gldb-prototype.

Stage functions are named after future LangGraph nodes:
    ocr -> intake -> prompt_screen -> extract -> agents -> score -> readiness -> route
    -> projections (chains, timeline, investigation) -> semantic_review -> record

Each stage is timed and recorded as a StageReport ("ran" or "off"), and an
optional callback hears each stage start and end, for live progress.

pack_from_folder is a test helper that builds an ExtractedPack directly
from a pre-generated data pack directory (no AWS calls).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, TypeVar

from gldb.audit import log_event
from gldb.checks import AGENTS, FORM_DOC_TYPES, run_agents
from gldb.config import (
    BEDROCK_MODEL_ID,
    CASES_DIR,
    EXTRACTION_MODE,
    GUARDRAIL_ID,
    GUARDRAIL_VERSION,
    OCR_ENABLED,
    POLICY_VERSION,
    PROMPT_SCREEN_ENABLED,
    SEMANTIC_REVIEW_ENABLED,
)
from gldb.coverage import coverage
from gldb.extract import parse_pack
from gldb.extract_llm import PROMPT_VERSION as EXTRACT_PROMPT_VERSION
from gldb.extract_stub import parse_pack as parse_pack_stub
from gldb.intake import intake, load_manifest, model_view
from gldb.investigation import build_queue
from gldb.ocr import OcrResult, ocr_files
from gldb.prompt_screen import screen
from gldb.reconciliation import build_chains
from gldb.reference import versions as reference_versions
from gldb.routing import route
from gldb.schemas import (
    ApplicationManifest,
    AuditEvent,
    CaseResult,
    ExtractedPack,
    StageId,
    StageReport,
    StageState,
)
from gldb.scoring import readiness, score
from gldb.semantic_review import PROMPT_VERSION as REVIEW_PROMPT_VERSION
from gldb.semantic_review import review
from gldb.signal_library import RULE_VERSION
from gldb.timeline import build_timeline

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Existing helpers (unchanged)
# ---------------------------------------------------------------------------


def pack_from_folder(folder: str | Path) -> ExtractedPack:
    """Build an ExtractedPack from a pre-generated pack directory.

    Reads all PDF files in *folder*, runs intake, stub-parses each document,
    and loads application.json as the manifest.  Used by tests to avoid AWS.

    Args:
        folder: Path to a pack directory (e.g. data/packs/clean/).

    Returns:
        A fully populated ExtractedPack.
    """
    folder = Path(folder)
    pdf_paths = sorted(folder.glob("*.pdf"))

    case_id = folder.name

    docs = intake(case_id, pdf_paths)
    manifest = load_manifest(folder)

    return parse_pack_stub(case_id, docs, pdf_paths, manifest)


# ---------------------------------------------------------------------------
# Stages (named for future LangGraph node parity)
# ---------------------------------------------------------------------------


#: The stages assess() runs, in order; run() adds "record"
ASSESS_STAGES: tuple[StageId, ...] = (
    "ocr", "intake", "prompt_screen", "extract", "agents", "score", "readiness", "route",
    "projections", "semantic_review",
)

#: Told each stage as it starts ("running") and ends ("ran" or "off")
StageCallback = Callable[[StageId, StageState], None]


def ignore_stage(stage: StageId, state: StageState) -> None:
    """The default StageCallback: nobody is watching."""


class _Stages:
    """Runs and times pipeline stages, recording a StageReport for each and
    telling *on_stage* as each starts and ends."""

    def __init__(self, on_stage: StageCallback) -> None:
        self._on_stage = on_stage
        self.reports: list[StageReport] = []

    def run(self, stage: StageId, step: Callable[[], T], version: str = "") -> T:
        self._on_stage(stage, "running")
        start = perf_counter()
        value = step()
        self.reports.append(StageReport(
            stage_id=stage, status="ran", duration_ms=round((perf_counter() - start) * 1000),
            version=version,
        ))
        self._on_stage(stage, "ran")
        return value

    def off(self, stage: StageId) -> None:
        self.reports.append(StageReport(stage_id=stage, status="off", duration_ms=0))
        self._on_stage(stage, "off")


def _extract_version() -> str:
    if EXTRACTION_MODE == "stub":
        return "stub parser"
    return f"{BEDROCK_MODEL_ID} · prompt {EXTRACT_PROMPT_VERSION}"


def screen_ledger(screened: bool) -> dict[str, Any]:
    """Which guardrail version screened the case, for the audit log."""
    return {"prompt_screen": {"guardrail_id": GUARDRAIL_ID, "version": GUARDRAIL_VERSION}
            if screened else None}


def model_ledger(extraction_mode: str, reviewed: bool) -> dict[str, Any]:
    """Which model and prompt versions produced a result, for the audit log.
    Only stages that called the model are listed; none means no model."""
    prompts: dict[str, str] = {}
    if extraction_mode != "stub":
        prompts["extract"] = EXTRACT_PROMPT_VERSION
    if reviewed:
        prompts["semantic_review"] = REVIEW_PROMPT_VERSION
    return {"model_id": BEDROCK_MODEL_ID if prompts else None, "prompt_versions": prompts}


def assess(
    case_id: str,
    file_paths: list[Path],
    manifest: ApplicationManifest | None,
    on_stage: StageCallback = ignore_stage,
) -> CaseResult:
    """Run the stages in ASSESS_STAGES: ocr -> intake -> prompt_screen ->
    extract -> agents -> score -> readiness -> route -> projections (chains,
    timeline, investigation queue) -> semantic_review. A stage switched off
    in config is recorded as "off". No audit log, no files written: run()
    adds those, and the evaluation harness calls this directly."""
    stages = _Stages(on_stage)

    if OCR_ENABLED:
        ocr = stages.run("ocr", lambda: ocr_files(file_paths, case_id),
                         version="Textract DetectDocumentText")
    else:
        stages.off("ocr")
        ocr = OcrResult()

    docs = stages.run("intake", lambda: intake(case_id, file_paths, ocr.text))

    screened = None
    if PROMPT_SCREEN_ENABLED:
        # Screen exactly what a model would read; forms never go to a model
        paths = {p.stem: p for p in file_paths}
        screened = stages.run("prompt_screen", lambda: screen({
            d.doc_id: model_view(paths[d.doc_id], ocr.text.get(d.doc_id))
            for d in docs if d.doc_type not in FORM_DOC_TYPES
        }, case_id), version=f"{GUARDRAIL_ID} v{GUARDRAIL_VERSION}")
    else:
        stages.off("prompt_screen")

    pack = stages.run("extract", lambda: parse_pack(case_id, docs, file_paths, manifest, ocr.text),
                      version=_extract_version())
    pack = pack.model_copy(update={
        "ocr_confidence": ocr.confidence, "ocr_lines": ocr.lines, "prompt_screen": screened,
    })

    evidence, agent_reports = stages.run("agents", lambda: run_agents(pack),
                                         version=f"{sum(len(a.checks) for a in AGENTS)} checks")
    risk = stages.run("score", lambda: score(evidence), version=RULE_VERSION)
    ready = stages.run("readiness", lambda: readiness(evidence, pack))
    routing = stages.run("route", lambda: route(case_id, evidence, risk, ready, pack),
                         version=POLICY_VERSION)
    chains, timeline, investigation = stages.run("projections", lambda: (
        build_chains(pack, evidence),
        build_timeline(pack, evidence),
        build_queue(case_id, evidence, pack, risk),
    ))

    reviewed = None
    if SEMANTIC_REVIEW_ENABLED:
        reviewed = stages.run(
            "semantic_review", lambda: review(case_id, evidence, chains, timeline),
            version=f"{BEDROCK_MODEL_ID} · prompt {REVIEW_PROMPT_VERSION}",
        )
    else:
        stages.off("semantic_review")

    return CaseResult(
        case_id=case_id,
        pack=pack,
        evidence=evidence,
        agent_reports=agent_reports,
        risk=risk,
        readiness=ready,
        routing=routing,
        chains=chains,
        timeline=timeline,
        investigation=investigation,
        review=reviewed,
        stages=stages.reports,
    )


# ---------------------------------------------------------------------------
# Public run()
# ---------------------------------------------------------------------------


#: Per-case file of OCR lines with positions (see _persist)
OCR_LINES_FILE = "ocr_lines.json"


def _persist(result: CaseResult) -> None:
    """Write result.json, first archiving any earlier result of the case as
    result.vN.json, so a re-assessment never overwrites the record."""
    out_dir = Path(CASES_DIR) / result.case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    current = out_dir / "result.json"
    if current.exists():
        version = len(list(out_dir.glob("result.v*.json"))) + 1
        current.rename(out_dir / f"result.v{version}.json")
    current.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    # OCR lines hold raw document text: kept beside the case for the viewer,
    # never in result.json or the API
    if result.pack.ocr_lines:
        lines = {doc: [[line.model_dump() for line in page] for page in pages]
                 for doc, pages in result.pack.ocr_lines.items()}
        (out_dir / OCR_LINES_FILE).write_text(json.dumps(lines), encoding="utf-8")


def run(
    case_id: str,
    file_paths: list[Path | str],
    manifest_path: Path | None = None,
    actor: str = "system",
    new_case: bool = True,
    on_stage: StageCallback = ignore_stage,
) -> CaseResult:
    """Run the full loan-assessment pipeline for *case_id*.

    With *new_case* False (documents re-entering after a request for
    information) no case_created event is logged and the previous result is
    archived rather than overwritten.

    Stages
    ------
    1. Log ``case_created`` audit event (new cases only).
    2. Load manifest from *manifest_path* (or None).
    3-7. assess(): the stages in ASSESS_STAGES (OCR, intake, prompt screen,
         extract, agents, score, readiness, route, projections, semantic review).
    8-9. The "record" stage: persist CaseResult JSON to
         ``<CASES_DIR>/<case_id>/result.json`` and log the
         ``pipeline_completed`` audit event.

    Args:
        case_id: Unique identifier for this assessment run.
        file_paths: Paths to the uploaded PDF files.
        manifest_path: Optional path to application.json.
        actor: Who/what is running the pipeline (default "system").
        on_stage: Told as each stage starts and ends, for live progress.

    Returns:
        A fully populated CaseResult.
    """
    resolved: list[Path] = [Path(p) for p in file_paths]

    # ------------------------------------------------------------------ 1
    if new_case:
        log_event(AuditEvent(
            case_id=case_id,
            actor=actor,
            action="case_created",
            details={
                "filenames": [p.name for p in resolved],
                "sha256s": {},   # hashes are computed inside intake; we log names here
            },
        ))

    # ------------------------------------------------------------------ 2
    manifest: ApplicationManifest | None = None
    if manifest_path is not None:
        manifest_path = Path(manifest_path)
        if manifest_path.exists():
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = ApplicationManifest.model_validate(raw)

    # ------------------------------------------------------------------ 3-7  assess
    result = assess(case_id, resolved, manifest, on_stage)
    on_stage("record", "running")
    pack, evidence, risk, ready = (
        result.pack, result.evidence, result.risk, result.readiness
    )

    # ------------------------------------------------------------------ 8  persist result
    _persist(result)

    # ------------------------------------------------------------------ 9
    category_counts: dict[str, int] = dict(
        Counter(ev.category for ev in evidence)
    )
    doc_sha256s = {d.doc_id: d.sha256 for d in pack.docs}

    log_event(AuditEvent(
        case_id=case_id,
        actor=actor,
        action="pipeline_completed",
        details={
            "score": risk.score,
            "band": risk.band,
            "readiness_status": ready.status,
            "routing_path": result.routing.path if result.routing else None,
            "policy_version": result.routing.policy_version if result.routing else None,
            "rule_version": risk.rule_version,
            "reference_data": reference_versions(),
            **model_ledger(pack.extraction_mode, result.review is not None),
            **screen_ledger(pack.prompt_screen is not None),
            "evidence_coverage": coverage(evidence, pack),
            "evidence_count_by_category": category_counts,
            "extraction_mode": pack.extraction_mode,
            "sha256s": doc_sha256s,
        },
    ))
    on_stage("record", "ran")

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _cli_main() -> None:  # pragma: no cover
    """CLI: python -m gldb.pipeline <folder>

    Runs all PDFs in <folder>, loads <folder>/application.json if present,
    and prints a summary to stdout.
    """
    if len(sys.argv) < 2:
        print("Usage: python -m gldb.pipeline <folder>", file=sys.stderr)
        sys.exit(1)

    folder = Path(sys.argv[1])
    if not folder.is_dir():
        print(f"Error: {folder} is not a directory.", file=sys.stderr)
        sys.exit(1)

    pdf_paths = sorted(folder.glob("*.pdf"))
    if not pdf_paths:
        print(f"No PDF files found in {folder}.", file=sys.stderr)
        sys.exit(1)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    case_id = f"CLI-{ts}"

    app_json = folder / "application.json"
    manifest_path: Path | None = app_json if app_json.exists() else None

    result = run(case_id, pdf_paths, manifest_path=manifest_path, actor="cli")

    # ---- Print summary ------------------------------------------------
    r = result.risk
    rd = result.readiness

    print(f"\n{'=' * 60}")
    print(f"  GLDB Loan Document Assessment  [PROTOTYPE]")
    print(f"  Case ID : {case_id}")
    print(f"{'=' * 60}")
    print(f"  Risk score       : {r.score}")
    print(f"  Band             : {r.band}")
    print(f"  Manual review    : {'YES' if r.manual_review_required else 'No'}")
    if rd:
        print(f"  Readiness status : {rd.status}")
    print()

    if rd:
        print("Checklist")
        print("-" * 40)
        for item in rd.checklist:
            print(f"  [{item['status']:30s}]  {item['label']}")
        print()

    print("Evidence")
    print("-" * 80)
    header = f"  {'ID':<5}  {'Category':<14}  {'Check ID':<30}  {'Sev':<8}  {'Doc':<30}  Explanation"
    print(header)
    print("-" * 80)
    for ev in result.evidence:
        if ev.severity == "info":
            continue
        doc = ev.document[:28]
        expl = ev.explanation[:60]
        print(
            f"  {ev.evidence_id:<5}  {ev.category:<14}  {ev.check_id:<30}"
            f"  {ev.severity:<8}  {doc:<30}  {expl}"
        )
    print()


if __name__ == "__main__":
    _cli_main()
