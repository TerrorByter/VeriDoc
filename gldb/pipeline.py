"""
gldb/pipeline.py
----------------
Pipeline orchestration for the gldb-prototype.

Stage functions are named after future LangGraph nodes:
    ocr -> intake -> classify_and_extract -> run_checks -> score -> route -> explain

pack_from_folder is a test helper that builds an ExtractedPack directly
from a pre-generated data pack directory (no AWS calls).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gldb.audit import log_event
from gldb.checks import run_agents
from gldb.config import CASES_DIR, EXPLAIN_ENABLED, OCR_ENABLED
from gldb.explain import explain
from gldb.extract import parse_pack
from gldb.extract_stub import parse_pack as parse_pack_stub
from gldb.intake import intake, load_manifest
from gldb.investigation import build_queue
from gldb.ocr import OcrResult, ocr_files
from gldb.reconciliation import build_chains
from gldb.reference import versions as reference_versions
from gldb.routing import route
from gldb.schemas import (
    ApplicationManifest,
    AuditEvent,
    CaseResult,
    Evidence,
    ExtractedPack,
    RiskResult,
)
from gldb.scoring import readiness, score
from gldb.timeline import build_timeline


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
# Internal stage helpers (named for future LangGraph node parity)
# ---------------------------------------------------------------------------


def _intake(
    case_id: str,
    file_paths: list[Path],
    manifest: ApplicationManifest | None,
) -> tuple[ExtractedPack, list[Evidence]]:
    """ocr → intake → classify_and_extract stage.

    OCRs every page once when OCR_ENABLED, then validates, hashes, classifies
    all files and parses their content. Returns the populated ExtractedPack
    and an empty stage_evidence list (reserved for intake-level findings in
    future stages).
    """
    ocr = ocr_files(file_paths) if OCR_ENABLED else OcrResult()
    docs = intake(case_id, file_paths, ocr.text)
    pack = parse_pack(case_id, docs, file_paths, manifest, ocr.text)
    pack = pack.model_copy(update={"ocr_confidence": ocr.confidence})
    stage_evidence: list[Evidence] = []
    return pack, stage_evidence


def _explain(evidence: list[Evidence], risk: RiskResult) -> str | None:
    """explain stage — LLM summary when EXPLAIN_ENABLED, otherwise None."""
    return explain(evidence, risk) if EXPLAIN_ENABLED else None


def assess(
    case_id: str,
    file_paths: list[Path],
    manifest: ApplicationManifest | None,
) -> CaseResult:
    """Run the stages intake -> classify_and_extract -> run_checks -> score
    -> readiness -> route -> chains, timeline and investigation queue ->
    explain. No audit log, no files written: run()
    adds those, and the evaluation harness calls this directly."""
    pack, stage_evidence = _intake(case_id, file_paths, manifest)
    evidence, agent_reports = run_agents(pack, stage_evidence)
    risk = score(evidence)
    ready = readiness(evidence, pack)
    return CaseResult(
        case_id=case_id,
        pack=pack,
        evidence=evidence,
        agent_reports=agent_reports,
        risk=risk,
        readiness=ready,
        routing=route(case_id, evidence, risk, ready, pack),
        chains=build_chains(pack, evidence),
        timeline=build_timeline(pack, evidence),
        investigation=build_queue(case_id, evidence, pack, risk),
        summary=_explain(evidence, risk),
    )


# ---------------------------------------------------------------------------
# Public run()
# ---------------------------------------------------------------------------


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


def run(
    case_id: str,
    file_paths: list[Path | str],
    manifest_path: Path | None = None,
    actor: str = "system",
    new_case: bool = True,
) -> CaseResult:
    """Run the full loan-assessment pipeline for *case_id*.

    With *new_case* False (documents re-entering after a request for
    information) no case_created event is logged and the previous result is
    archived rather than overwritten.

    Stages
    ------
    1. Log ``case_created`` audit event (new cases only).
    2. Load manifest from *manifest_path* (or None).
    3. intake → classify_and_extract: build ExtractedPack.
    4. run_checks: collect all Evidence.
    5. score: compute RiskResult (fraud_signal evidence only).
    6. readiness: compute ReadinessResult (compliance/completeness evidence only).
    7. explain: LLM summary if EXPLAIN_ENABLED, else None.
    8. Persist CaseResult JSON to ``<CASES_DIR>/<case_id>/result.json``.
    9. Log ``pipeline_completed`` audit event.

    Args:
        case_id: Unique identifier for this assessment run.
        file_paths: Paths to the uploaded PDF files.
        manifest_path: Optional path to application.json.
        actor: Who/what is running the pipeline (default "system").

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
    result = assess(case_id, resolved, manifest)
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
            "evidence_count_by_category": category_counts,
            "extraction_mode": pack.extraction_mode,
            "sha256s": doc_sha256s,
        },
    ))

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
