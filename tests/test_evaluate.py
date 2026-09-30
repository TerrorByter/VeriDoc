"""
tests/test_evaluate.py
----------------------
Tests for gldb/evaluate.py. Stub extraction, no AWS calls.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from gldb import evaluate

PACKS = Path(__file__).parent.parent / "data" / "packs"


def test_stub_extractor_is_perfect_on_all_packs() -> None:
    report = evaluate.evaluate(PACKS)
    assert [r.pack for r in report.packs] == ["clean", "incomplete", "tampered"]
    assert all(r.ok for r in report.packs)
    assert report.precision == report.recall == 1.0
    assert report.band_accuracy == report.readiness_accuracy == 1.0
    assert report.clean_pack_false_positives == 0
    assert report.true_positives == 12  # 8 fraud + 4 compliance/completeness


def test_wrong_ground_truth_is_reported_and_fails_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(PACKS / "tampered", packs / "tampered")
    gt = packs / "tampered" / "ground_truth.json"
    truth = json.loads(gt.read_text())
    truth["expected_check_ids"] = truth["expected_check_ids"][1:] + ["NOT_A_CHECK"]
    truth["expected_band"] = "Low"
    gt.write_text(json.dumps(truth))

    report = evaluate.evaluate(packs)
    result = report.packs[0]
    assert result.missed == ["NOT_A_CHECK"]
    assert result.unexpected == ["XDOC_INVOICE_BANK"]
    assert result.band == "High" and result.expected_band == "Low"
    assert report.precision < 1.0 and report.recall < 1.0

    monkeypatch.setattr(evaluate, "PACKS_DIR", str(packs))
    assert evaluate.main([]) == 1


def test_main_passes_on_real_packs(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluate.main([]) == 0
    assert "precision 1.00  recall 1.00" in capsys.readouterr().out
