"""
gldb/evaluate.py
----------------
Evaluation harness: runs every pack under PACKS_DIR through the pipeline
stages and compares the result with the pack's ground_truth.json.

Run:  python -m gldb.evaluate [--json]

Uses the configured extractor (EXTRACTION_MODE), so the same command
compares the stub and the LLM extractor. Nothing is written to the audit log
or the cases directory. Exit code is 1 if any pack has a wrong band, wrong
readiness, or any missed or unexpected finding.

Only non-"info" findings count. Fraud checks are compared against
expected_check_ids (plus expected_check_ids_ocr when OCR_ENABLED, since those
checks need the OCR stage); compliance and completeness checks against
expected_compliance_check_ids.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel

from gldb.config import OCR_ENABLED, PACKS_DIR
from gldb.intake import load_manifest
from gldb.pipeline import assess


class PackResult(BaseModel):
    pack: str
    band: str
    expected_band: str
    readiness: str
    expected_readiness: str
    missed: list[str]
    unexpected: list[str]
    #: Set when the pipeline raised; the pack then counts every expected
    #: finding as missed, so a crash can never look like a clean pass.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.band == self.expected_band
            and self.readiness == self.expected_readiness
            and not self.missed
            and not self.unexpected
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
    clean_pack_false_positives: int
    #: Packs marked requires_ocr, not run because OCR_ENABLED is off
    skipped: list[str] = []


def expected_ids(truth: dict, ocr_enabled: bool) -> set[tuple[str, bool]]:
    """(check_id, is_fraud_signal) pairs the pack should produce."""
    fraud = list(truth["expected_check_ids"])
    if ocr_enabled:
        fraud += truth.get("expected_check_ids_ocr", [])
    return {(c, True) for c in fraud} | {
        (c, False) for c in truth["expected_compliance_check_ids"]
    }


def evaluate_pack(folder: Path) -> tuple[PackResult, int]:
    """Assess one pack. Returns the result and its true-positive count.

    A pipeline exception (for example a malformed LLM response) is reported
    on this pack instead of aborting the whole evaluation.
    """
    truth = json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
    expected = expected_ids(truth, OCR_ENABLED)
    try:
        result = assess(folder.name, sorted(folder.glob("*.pdf")), load_manifest(folder))
    except Exception as exc:  # noqa: BLE001 - report any pipeline failure per pack
        return PackResult(
            pack=folder.name,
            band="-",
            expected_band=truth["expected_band"],
            readiness="-",
            expected_readiness=truth["expected_readiness"],
            missed=sorted(c for c, _ in expected),
            unexpected=[],
            error=f"{type(exc).__name__}: {exc}",
        ), 0

    found = {
        (ev.check_id, ev.category == "fraud_signal")
        for ev in result.evidence
        if ev.severity != "info"
    }
    assert result.readiness is not None
    pack_result = PackResult(
        pack=folder.name,
        band=result.risk.band,
        expected_band=truth["expected_band"],
        readiness=result.readiness.status,
        expected_readiness=truth["expected_readiness"],
        missed=sorted(c for c, _ in expected - found),
        unexpected=sorted(c for c, _ in found - expected),
    )
    return pack_result, len(expected & found)


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def _requires_ocr(folder: Path) -> bool:
    truth = json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
    return bool(truth.get("requires_ocr"))


def evaluate(packs_dir: Path) -> Report:
    all_folders = sorted(p for p in packs_dir.iterdir() if (p / "ground_truth.json").is_file())
    skipped = [] if OCR_ENABLED else [f for f in all_folders if _requires_ocr(f)]
    folders = [f for f in all_folders if f not in skipped]
    results, tps = zip(*(evaluate_pack(f) for f in folders))
    tp = sum(tps)
    fp = sum(len(r.unexpected) for r in results)
    fn = sum(len(r.missed) for r in results)
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
        clean_pack_false_positives=sum(
            len(r.unexpected) for r in results if r.expected_band == "Low"
        ),
        skipped=[f.name for f in skipped],
    )


def _print(report: Report) -> None:
    print(f"{'pack':<12}{'band':<16}{'readiness':<24}missed / unexpected")
    for r in report.packs:
        if r.error:
            print(f"{r.pack:<12}ERROR {r.error}")
            continue
        band = f"{r.band}/{r.expected_band}"
        ready = f"{r.readiness}/{r.expected_readiness}"
        print(f"{r.pack:<12}{band:<16}{ready:<24}{r.missed or '-'} / {r.unexpected or '-'}")
    print(
        f"\nprecision {report.precision:.2f}  recall {report.recall:.2f}  "
        f"band accuracy {report.band_accuracy:.2f}  "
        f"readiness accuracy {report.readiness_accuracy:.2f}  "
        f"false positives on Low packs {report.clean_pack_false_positives}"
    )
    if report.skipped:
        print(f"skipped (need OCR_ENABLED=true): {', '.join(report.skipped)}")


def main(argv: list[str]) -> int:
    report = evaluate(Path(PACKS_DIR))
    if "--json" in argv:
        print(report.model_dump_json(indent=2))
    else:
        _print(report)
    return 0 if all(r.ok for r in report.packs) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
