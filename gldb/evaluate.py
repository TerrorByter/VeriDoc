"""
gldb/evaluate.py
----------------
Evaluation harness: runs every pack under PACKS_DIR through the pipeline
stages and compares the result with the pack's ground_truth.json.

Run:  python -m gldb.evaluate [--json] [--save]

--save keeps the report at EVAL_REPORT_PATH, stamped with when it ran and
which capabilities were on, for the API's controls page.

Uses the configured extractor (EXTRACTION_MODE), so the same command
compares the stub and the LLM extractor. Nothing is written to the audit log
or the cases directory. Exit code is 1 if any pack has a wrong band, wrong
readiness, or any missed or unexpected finding.

Only non-"info" findings count. Fraud checks are compared against
expected_check_ids (plus expected_check_ids_ocr when OCR_ENABLED, since those
checks need the OCR stage); compliance and completeness checks against
expected_compliance_check_ids.

Attack table: each tamper in ground_truth.json names the checks expected to
catch it (check_ids). It counts as detected only when one of them raises a
finding on the tampered document itself; a hit elsewhere in the pack is not
a detection.

Capabilities: a pack or attack may list what it needs ("requires": "ocr",
"prompt_screen"). A pack needing a capability that is off is skipped; an
attack needing one is "not run". Neither counts as a miss.

Evidence coverage: the share of risk findings traceable to a document, page
and quote (gldb.coverage). Anything below 1.0 fails the run.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel

from gldb.config import (
    EVAL_REPORT_PATH,
    EXTRACTION_MODE,
    OCR_ENABLED,
    PACKS_DIR,
    PROMPT_SCREEN_ENABLED,
)
from gldb.coverage import untraceable
from gldb.intake import document_paths, load_manifest
from gldb.pipeline import assess
from gldb.schemas import Evidence
from gldb.signal_library import signal_for


#: Optional capabilities and whether this run has them
CAPABILITIES: dict[str, bool] = {"ocr": OCR_ENABLED, "prompt_screen": PROMPT_SCREEN_ENABLED}


def _missing(requires: list[str]) -> list[str]:
    return [c for c in requires if not CAPABILITIES.get(c, False)]


class AttackResult(BaseModel):
    pack: str
    attack: str
    document: str
    detail: str
    #: True / False, or None when not run (needs a capability that is off)
    detected: bool | None
    signals: list[str] = []
    evidence_ids: list[str] = []


class PackResult(BaseModel):
    pack: str
    band: str
    expected_band: str
    readiness: str
    expected_readiness: str
    path: str
    expected_path: str
    missed: list[str]
    unexpected: list[str]
    attacks: list[AttackResult] = []
    risk_findings: int = 0
    #: Risk findings that cannot be traced to their source (gldb.coverage)
    untraceable: list[str] = []
    #: Set when the pipeline raised; the pack then counts every expected
    #: finding as missed, so a crash can never look like a clean pass.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.band == self.expected_band
            and self.readiness == self.expected_readiness
            and self.path == self.expected_path
            and not self.missed
            and not self.unexpected
            and all(a.detected is not False for a in self.attacks)
            and not self.untraceable
        )


class Report(BaseModel):
    packs: list[PackResult]
    true_positives: int
    false_positives: int
    false_negatives: int
    precision: float
    recall: float
    band_accuracy: float
    readiness_accuracy: float
    routing_accuracy: float
    clean_pack_false_positives: int
    attacks: list[AttackResult]
    #: Detected attacks / attacks run
    attack_detection_rate: float
    #: Traceable risk findings / all risk findings
    evidence_coverage: float
    #: Packs not run because a capability they require is off
    skipped: list[str] = []
    #: Set by save(): when the run finished, and the configuration it ran with
    evaluated_at: str = ""
    capabilities: dict[str, bool] = {}
    extraction_mode: str = ""


def expected_ids(truth: dict, ocr_enabled: bool) -> set[tuple[str, bool]]:
    """(check_id, is_fraud_signal) pairs the pack should produce."""
    fraud = list(truth["expected_check_ids"])
    if ocr_enabled:
        fraud += truth.get("expected_check_ids_ocr", [])
    return {(c, True) for c in fraud} | {
        (c, False) for c in truth["expected_compliance_check_ids"]
    }


def _attacks(pack: str, truth: dict, evidence: list[Evidence] | None) -> list[AttackResult]:
    """One row per tamper. *evidence* None means the pipeline crashed."""
    rows = []
    for tamper in truth["tampers"]:
        hits = [
            e for e in evidence or []
            if e.check_id in tamper["check_ids"] and e.document == tamper["doc"]
            and e.severity != "info"
        ]
        detected: bool | None = bool(hits)
        if _missing(tamper.get("requires", [])):
            detected = None
        rows.append(AttackResult(
            pack=pack, attack=tamper["type"], document=tamper["doc"], detail=tamper["detail"],
            detected=detected,
            signals=sorted({signal_for(e.check_id).signal_id for e in hits}),
            evidence_ids=[e.evidence_id for e in hits],
        ))
    return rows


def evaluate_pack(folder: Path) -> tuple[PackResult, int]:
    """Assess one pack. Returns the result and its true-positive count.

    A pipeline exception (for example a malformed LLM response) is reported
    on this pack instead of aborting the whole evaluation.
    """
    truth = json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
    expected = expected_ids(truth, OCR_ENABLED)
    try:
        # Rules only: the AI team never changes the score or route, and only
        # the agent evaluation runs it
        result = assess(folder.name, document_paths(folder), load_manifest(folder), team=None)
    except Exception as exc:  # noqa: BLE001 - report any pipeline failure per pack
        return PackResult(
            pack=folder.name,
            band="-",
            expected_band=truth["expected_band"],
            readiness="-",
            expected_readiness=truth["expected_readiness"],
            path="-",
            expected_path=truth["expected_path"],
            missed=sorted(c for c, _ in expected),
            unexpected=[],
            attacks=_attacks(folder.name, truth, None),
            error=f"{type(exc).__name__}: {exc}",
        ), 0

    found = {
        (ev.check_id, ev.category == "fraud_signal")
        for ev in result.evidence
        if ev.severity != "info"
    }
    assert result.readiness is not None and result.routing is not None
    pack_result = PackResult(
        pack=folder.name,
        band=result.risk.band,
        expected_band=truth["expected_band"],
        readiness=result.readiness.status,
        expected_readiness=truth["expected_readiness"],
        path=result.routing.path,
        expected_path=truth["expected_path"],
        missed=sorted(c for c, _ in expected - found),
        unexpected=sorted(c for c, _ in found - expected),
        attacks=_attacks(folder.name, truth, result.evidence),
        risk_findings=sum(
            e.category == "fraud_signal" and e.severity != "info" for e in result.evidence
        ),
        untraceable=untraceable(result.evidence, result.pack),
    )
    return pack_result, len(expected & found)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _skipped(folder: Path) -> bool:
    truth = json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
    return bool(_missing(truth.get("requires", [])))


def evaluate(packs_dir: Path) -> Report:
    all_folders = sorted(p for p in packs_dir.iterdir() if (p / "ground_truth.json").is_file())
    skipped = [f for f in all_folders if _skipped(f)]
    folders = [f for f in all_folders if f not in skipped]
    results, tps = zip(*(evaluate_pack(f) for f in folders))
    tp = sum(tps)
    fp = sum(len(r.unexpected) for r in results)
    fn = sum(len(r.missed) for r in results)
    attacks = [a for r in results for a in r.attacks]
    run = [a for a in attacks if a.detected is not None]
    findings = sum(r.risk_findings for r in results)
    return Report(
        packs=list(results),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        precision=_ratio(tp, tp + fp),
        recall=_ratio(tp, tp + fn),
        band_accuracy=_ratio(sum(r.band == r.expected_band for r in results), len(results)),
        readiness_accuracy=_ratio(
            sum(r.readiness == r.expected_readiness for r in results), len(results)
        ),
        routing_accuracy=_ratio(sum(r.path == r.expected_path for r in results), len(results)),
        clean_pack_false_positives=sum(
            len(r.unexpected) for r in results if r.expected_band == "Low"
        ),
        attacks=attacks,
        attack_detection_rate=_ratio(sum(bool(a.detected) for a in run), len(run)),
        evidence_coverage=_ratio(findings - sum(len(r.untraceable) for r in results), findings),
        skipped=[f.name for f in skipped],
    )


def _print(report: Report) -> None:
    # The pack column is as wide as the longest pack name, so no name runs into the next column
    width = max([len("pack"), *(len(r.pack) for r in report.packs)]) + 2
    print(f"{'pack':<{width}}{'band':<16}{'readiness':<24}{'route':<36}missed / unexpected")
    for r in report.packs:
        if r.error:
            print(f"{r.pack:<{width}}ERROR {r.error}")
            continue
        band = f"{r.band}/{r.expected_band}"
        ready = f"{r.readiness}/{r.expected_readiness}"
        path = f"{r.path}/{r.expected_path}"
        print(f"{r.pack:<{width}}{band:<16}{ready:<24}{path:<36}{r.missed or '-'} / {r.unexpected or '-'}")
    print(
        f"\nprecision {report.precision:.2f}  recall {report.recall:.2f}  "
        f"band accuracy {report.band_accuracy:.2f}  "
        f"readiness accuracy {report.readiness_accuracy:.2f}  "
        f"routing accuracy {report.routing_accuracy:.2f}  "
        f"false positives on Low packs {report.clean_pack_false_positives}"
    )
    print(f"\n{'attack':<28}{'pack':<{width}}{'document':<26}{'detected':<10}signals (evidence)")
    for a in report.attacks:
        mark = {True: "yes", False: "NO", None: "not run"}[a.detected]
        found = f"{', '.join(a.signals)} ({', '.join(a.evidence_ids)})" if a.evidence_ids else "-"
        print(f"{a.attack:<28}{a.pack:<{width}}{a.document:<26}{mark:<10}{found}")
    print(
        f"\nattacks detected {report.attack_detection_rate:.2f} of those run  "
        f"evidence coverage {report.evidence_coverage:.2f}"
    )
    for r in report.packs:
        if r.untraceable:
            print(f"untraceable findings in {r.pack}: {', '.join(r.untraceable)}")
    if report.skipped:
        print(f"skipped (a required capability is off): {', '.join(report.skipped)}")


def report_path(env_key: str, default: str) -> Path:
    """Where a saved report lives: the *env_key* variable if set, else
    *default*. Read at call time, so a test can point it elsewhere."""
    return Path(os.environ.get(env_key, "").strip() or default)


def _report_path() -> Path:
    return report_path("EVAL_REPORT_PATH", EVAL_REPORT_PATH)


def save(report: Report) -> None:
    """Keep *report* as the latest evaluation, stamped with the time and the
    configuration it ran with."""
    stamped = report.model_copy(update={
        "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "capabilities": CAPABILITIES,
        "extraction_mode": EXTRACTION_MODE,
    })
    path = _report_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamped.model_dump_json(indent=2), encoding="utf-8")


def load_saved() -> Report | None:
    """The latest saved evaluation, if any."""
    path = _report_path()
    return Report.model_validate_json(path.read_text(encoding="utf-8")) if path.is_file() else None


def main(argv: list[str]) -> int:
    report = evaluate(Path(PACKS_DIR))
    if "--save" in argv:
        save(report)
    if "--json" in argv:
        print(report.model_dump_json(indent=2))
    else:
        _print(report)
    return 0 if all(r.ok for r in report.packs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
