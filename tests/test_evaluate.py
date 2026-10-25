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

from gldb import evaluate, pipeline

PACKS = Path(__file__).parent / "fixtures" / "packs"


def test_stub_extractor_is_perfect_on_all_packs() -> None:
    report = evaluate.evaluate(PACKS)
    assert [r.pack for r in report.packs] == [
        "clean", "incomplete", "innocent_split", "missing_noa", "related_party", "retro_po",
        "round_trip", "sanctioned", "tampered", "third_party_payment",
    ]
    assert all(r.ok for r in report.packs)
    assert report.precision == report.recall == 1.0
    assert report.band_accuracy == report.readiness_accuracy == report.routing_accuracy == 1.0
    assert report.clean_pack_false_positives == 0
    assert report.true_positives == 18  # 13 fraud + 5 compliance/completeness


def test_beyond_rules_packs_match_their_rule_expectations() -> None:
    """The packs for patterns no rule catches: silent, except the innocent split,
    where only the invoice-to-bank rule fires."""
    results = {r.pack: r for r in evaluate.evaluate(PACKS).packs}
    for name in ("third_party_payment", "related_party", "round_trip", "innocent_split"):
        assert results[name].ok, results[name]
    assert results["innocent_split"].risk_findings == 1
    assert results["third_party_payment"].risk_findings == 0


def test_attack_table_on_real_packs() -> None:
    report = evaluate.evaluate(PACKS)
    attacks = report.attacks
    assert len(attacks) == 10  # tampered 7, sanctioned 1, retro_po 2
    not_run = [(a.pack, a.attack) for a in attacks if a.detected is None]
    assert not_run == [("tampered", "figure_pasted_as_image")]  # needs OCR
    assert all(a.detected for a in attacks if a.detected is not None)
    hidden = next(a for a in attacks if a.attack == "prompt_injection")
    assert hidden.signals == ["DOC-001"] and hidden.evidence_ids
    assert report.attack_detection_rate == 1.0
    assert report.evidence_coverage == 1.0


def test_attack_caught_only_on_another_document_is_not_detected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(PACKS / "tampered", packs / "tampered")
    gt = packs / "tampered" / "ground_truth.json"
    truth = json.loads(gt.read_text())
    injection = next(t for t in truth["tampers"] if t["type"] == "prompt_injection")
    injection["doc"] = "fs_summary"
    gt.write_text(json.dumps(truth))

    report = evaluate.evaluate(packs)
    attack = next(a for a in report.attacks if a.attack == "prompt_injection")
    assert (attack.detected, attack.evidence_ids) == (False, [])
    assert report.attack_detection_rate < 1.0
    monkeypatch.setattr(evaluate, "PACKS_DIR", str(packs))
    assert evaluate.main([]) == 1


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


def test_crashing_pack_is_reported_and_counted_as_missed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_assess = evaluate.assess

    def flaky(case_id: str, *args: object, **kwargs: object) -> object:
        if case_id == "tampered":
            raise RuntimeError("model returned junk")
        return real_assess(case_id, *args, **kwargs)

    monkeypatch.setattr(evaluate, "assess", flaky)
    monkeypatch.setattr(evaluate, "PACKS_DIR", str(PACKS))
    report = evaluate.evaluate(PACKS)
    *others, tampered = sorted(report.packs, key=lambda r: r.pack == "tampered")
    assert len(others) == 9 and all(r.ok for r in others)
    assert not tampered.ok
    assert tampered.error == "RuntimeError: model returned junk"
    assert len(tampered.missed) == 10  # every expected tampered finding
    assert all(a.detected is not True for a in tampered.attacks)
    assert report.recall < 1.0 and report.precision == 1.0

    assert evaluate.main([]) == 1
    assert "ERROR RuntimeError: model returned junk" in capsys.readouterr().out


def test_main_passes_on_real_packs(capsys: pytest.CaptureFixture[str]) -> None:
    assert evaluate.main([]) == 0
    assert "precision 1.00  recall 1.00" in capsys.readouterr().out


def test_saved_report_is_stamped_and_read_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EVAL_REPORT_PATH", str(tmp_path / "evaluation.json"))
    assert evaluate.load_saved() is None
    report = evaluate.Report(
        packs=[], true_positives=0, false_positives=0, false_negatives=0, precision=1.0,
        recall=1.0, band_accuracy=1.0, readiness_accuracy=1.0, routing_accuracy=1.0,
        clean_pack_false_positives=0, attacks=[], attack_detection_rate=1.0, evidence_coverage=1.0,
    )
    evaluate.save(report)
    saved = evaluate.load_saved()
    assert saved is not None and saved.evaluated_at
    assert saved.capabilities == evaluate.CAPABILITIES
    assert saved.extraction_mode == "stub"


def test_rule_evaluation_never_runs_the_ai_team(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_team(*_: object, **__: object) -> None:
        raise AssertionError("the rule evaluation must not run the AI team")

    real_assess = evaluate.assess
    teams: list[object] = []

    def spy(*args: object, **kwargs: object) -> object:
        teams.append(kwargs.get("team", "default"))
        return real_assess(*args, **kwargs)

    monkeypatch.setattr(pipeline, "run_team", no_team)
    monkeypatch.setattr(evaluate, "assess", spy)
    assert evaluate.evaluate(PACKS).recall == 1.0
    assert teams and all(team is None for team in teams)


def test_long_pack_names_do_not_run_into_the_next_column(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    packs = tmp_path / "packs"
    shutil.copytree(PACKS / "third_party_payment", packs / "third_party_payment")
    monkeypatch.setattr(evaluate, "PACKS_DIR", str(packs))

    assert evaluate.main([]) == 0

    lines = capsys.readouterr().out.splitlines()
    header = next(line for line in lines if line.startswith("pack"))
    row = next(line for line in lines if line.startswith("third_party_payment"))
    assert row.startswith("third_party_payment ")
    assert header.index("band") == row.index("Low/Low")
