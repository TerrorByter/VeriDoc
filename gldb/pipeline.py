"""
gldb/pipeline.py
----------------
Pipeline orchestration for the gldb-prototype.

Stage functions are named after future LangGraph nodes:
    intake -> classify_and_extract -> run_checks -> score -> explain

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
from gldb.config import CASES_DIR, EXPLAIN_ENABLED
from gldb.explain import explain
from gldb.extract import parse_pack
from gldb.extract_stub import parse_pack as parse_pack_stub
from gldb.intake import intake, load_manifest
from gldb.schemas import (
    ApplicationManifest,
    AuditEvent,
    CaseResult,
    Evidence,
    ExtractedPack,
    RiskResult,
)
from gldb.scoring import readiness, score


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
    """intake → classify_and_extract stage.

    Validates, hashes, classifies all files and stub-parses their content.
    Returns the populated ExtractedPack and an empty stage_evidence list
    (reserved for intake-level findings in future stages).
    """
    docs = intake(case_id, file_paths)
    pack = parse_pack(case_id, docs, file_paths, manifest)
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
    -> readiness -> explain. No audit log, no files written: run() adds those,
    and the evaluation harness calls this directly."""
    pack, stage_evidence = _intake(case_id, file_paths, manifest)
    evidence, agent_reports = run_agents(pack, stage_evidence)
    risk = score(evidence)
    return CaseResult(
        case_id=case_id,
        pack=pack,
        evidence=evidence,
        agent_reports=agent_reports,
        risk=risk,
        readiness=readiness(evidence, pack),
        summary=_explain(evidence, risk),
    )


# ---------------------------------------------------------------------------
# Public run()
# ---------------------------------------------------------------------------


def run(
    case_id: str,
    file_paths: list[Path | str],
    manifest_path: Path | None = None,
    actor: str = "system",
) -> CaseResult:
    """Run the full loan-assessment pipeline for *case_id*.

    Stages
    ------
    1. Log ``case_created`` audit event.
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
    out_dir = Path(CASES_DIR) / case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "result.json").write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

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
