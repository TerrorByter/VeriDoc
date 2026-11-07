"""
tests/test_heldout.py
---------------------
Tests for gldb/heldout.py: the blind held-out test renderer, seal/reveal and
per-pack scoring. No AWS calls.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gldb import heldout

PILOT = Path(__file__).parent / "fixtures" / "heldout" / "pilot.json"


def _generate(tmp_path: Path, seed: str = "pilot-seed") -> tuple[Path, Path, Path]:
    out = tmp_path / "set"
    key = tmp_path / "key.json"
    heldout.generate(PILOT, out, key, seed)
    return out, key, out / "seal.json"


def test_the_pilot_sheet_renders_packs_records_and_a_key(tmp_path: Path) -> None:
    out, key, seal = _generate(tmp_path)
    packs = out / "packs"
    assert seal.read_text(encoding="utf-8")
    key_data = json.loads(key.read_text(encoding="utf-8"))
    assert key_data["test_set"] == "pilot-v0" and len(key_data["packs"]) == 12
    assert len(list(packs.iterdir())) == 12
    # No pack carries its own label: the ground truth lives only in the key
    for folder in packs.iterdir():
        assert not (folder / "ground_truth.json").exists()
    # Official records were written for the held-out companies
    acra = json.loads((out / "records" / "acra.json").read_text(encoding="utf-8"))
    assert len(acra["companies"]) >= 12


def test_the_key_is_sealed_and_revealed(tmp_path: Path) -> None:
    out, key, seal = _generate(tmp_path)
    key_data = json.loads(key.read_text(encoding="utf-8"))
    heldout.verify_seal(key_data, json.loads(seal.read_text(encoding="utf-8")))
    tampered = json.loads(key.read_text(encoding="utf-8"))
    tampered["packs"][0]["label"] = "legitimate"
    with pytest.raises(ValueError):
        heldout.verify_seal(tampered, json.loads(seal.read_text(encoding="utf-8")))


def test_pack_names_do_not_leak_the_label(tmp_path: Path) -> None:
    out, _, _ = _generate(tmp_path)
    names = {folder.name for folder in (out / "packs").iterdir()}
    assert all(name.startswith("P") and len(name) == 11 for name in names)
    assert "fraud" not in " ".join(sorted(names)).lower()


def test_an_official_set_needs_forty_packs_balanced() -> None:
    data = json.loads(PILOT.read_text(encoding="utf-8"))
    data["kind"] = "official"
    with pytest.raises(ValidationError, match="at least 40"):
        heldout.ScenarioSet.model_validate(data)
    # Forty packs but all legitimate: the balance check fails
    data["scenarios"] = [
        {"scenario_id": f"C-{i}", "label": "legitimate", "quirk": "clean",
         "company": {"profile": "water_treatment"}}
        for i in range(40)
    ]
    with pytest.raises(ValidationError, match="at least 20"):
        heldout.ScenarioSet.model_validate(data)


def test_a_fraudulent_pack_needs_a_typology_and_no_quirk() -> None:
    with pytest.raises(ValidationError, match="needs a typology"):
        heldout.Scenario(
            scenario_id="X", label="fraud", quirk="clean",
            company=heldout.CompanySpec(profile="water_treatment"),
        )


def test_a_placement_is_required_for_a_trade_typology() -> None:
    with pytest.raises(ValidationError, match="needs a placement"):
        heldout.Scenario(
            scenario_id="X", label="fraud", typology="invoice_overbilled",
            company=heldout.CompanySpec(profile="water_treatment"),
        )


def test_the_disclosed_directorship_names_the_renamed_supplier(tmp_path: Path) -> None:
    out, key, _ = _generate(tmp_path)
    key_data = json.loads(key.read_text(encoding="utf-8"))
    disclosed = next(p for p in key_data["packs"] if p["quirk"] == "disclosed_directorship")
    app = json.loads((out / "packs" / disclosed["pack"] / "application.json").read_text(encoding="utf-8"))
    assert app["disclosures"][0]["kind"] == "directorship"
    # The disclosure names the supplier as it appears in this pack's paperwork
    counter = app["disclosures"][0]["counterparty"]
    acra = json.loads((out / "records" / "acra.json").read_text(encoding="utf-8"))
    assert any(c["company_name"].upper() == counter.upper() for c in acra["companies"])


def test_scoring_rejects_a_run_that_does_not_match_the_key(tmp_path: Path) -> None:
    out, key, seal = _generate(tmp_path)
    results = tmp_path / "results.json"
    results.write_text(json.dumps({
        "evaluated_at": "2026-11-03T00:00:00+00:00", "extraction_mode": "stub",
        "capabilities": {}, "runs": 1, "packs": [],
    }), encoding="utf-8")
    with pytest.raises(SystemExit, match="different packs"):
        heldout.score(results, key, seal, tmp_path / "report.json")


def test_scoring_refuses_to_overwrite_an_existing_report(tmp_path: Path) -> None:
    out, key, seal = _generate(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text("{}", encoding="utf-8")
    results = tmp_path / "results.json"
    results.write_text(json.dumps({"evaluated_at": "x", "extraction_mode": "stub",
                                   "capabilities": {}, "runs": 1, "packs": []}), encoding="utf-8")
    with pytest.raises(SystemExit, match="runs once"):
        heldout.score(results, key, seal, report_path)


def test_the_pilot_can_be_run_and_scored_end_to_end(tmp_path: Path) -> None:
    """A full generate -> blind run -> score round trip, using the offline
    pipeline (stub extraction, no OCR, no records fetch). The route here is
    what the rules produce on the rendered documents."""
    out, key, seal = _generate(tmp_path)
    results = tmp_path / "results.json"
    from gldb.pipeline import assess
    from gldb.intake import document_paths, load_manifest

    entries = []
    for folder in sorted((out / "packs").iterdir()):
        result = assess(folder.name, document_paths(folder), load_manifest(folder), team=None)
        entries.append({"pack": folder.name, "run": 1, "band": result.risk.band,
                        "readiness": result.readiness.status if result.readiness else "-",
                        "path": result.routing.path if result.routing else "-",
                        "qa_sampled": bool(result.routing and result.routing.qa_sampled),
                        "risk_findings": sum(e.category == "fraud_signal" and e.severity != "info"
                                             for e in result.evidence),
                        "verified_leads": [], "gate_route": None, "shadow_route": None, "error": None})
    results.write_text(json.dumps({"evaluated_at": "2026-11-03T00:00:00+00:00", "extraction_mode": "stub",
                                   "capabilities": {}, "runs": 1, "packs": entries}), encoding="utf-8")

    report = heldout.score(results, key, seal, tmp_path / "report.json")
    assert report.kind == "pilot"
    assert report.fraud_packs == 6 and report.legitimate_packs == 6
    assert report.caught + report.missed == 6
    assert report.clear + report.false_alarms == 6
    assert report.packs and all(p.outcome in {"caught", "missed", "clear", "false_alarm"} for p in report.packs)
    assert report.remarks == {}  # the team's analysis is added after the labels are revealed, never by scoring


def test_the_published_report_remarks_only_on_its_own_packs(monkeypatch: pytest.MonkeyPatch) -> None:
    published = Path(__file__).parent.parent / "data" / "heldout" / "report.json"
    monkeypatch.setenv("HELDOUT_REPORT_PATH", str(published))
    report = heldout.load_saved()
    assert report is not None and report.remarks
    assert set(report.remarks) <= {p.pack for p in report.packs}
