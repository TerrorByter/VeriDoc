"""
tests/test_semantic_review.py
-----------------------------
The Semantic Review Agent: synthesis over verified facts only, every point
grounded in evidence. converse() is faked; no AWS calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gldb import semantic_review as sr
from gldb.intake import load_manifest
from gldb.llm_client import LLMResult
from gldb.pipeline import assess
from gldb.schemas import CaseResult

PACKS = Path(__file__).parent.parent / "data" / "packs"


def _assess(name: str) -> CaseResult:
    folder = PACKS / name
    return assess(name, sorted(folder.glob("*.pdf")), load_manifest(folder))


@pytest.fixture(scope="module")
def tampered() -> CaseResult:
    return _assess("tampered")


@pytest.fixture(scope="module")
def retro_po() -> CaseResult:
    return _assess("retro_po")


def _fake(output: dict[str, Any], calls: list[str] | None = None) -> Any:
    def fake(system: str, user: str, **kwargs: Any) -> LLMResult:
        if calls is not None:
            calls.append(user)
        return LLMResult(text="", data=output, input_tokens=1, output_tokens=1)
    return fake


def _review(result: CaseResult) -> sr.SemanticReview | None:
    return sr.review(result.case_id, result.evidence, result.chains, result.timeline)


def _id(result: CaseResult, check_id: str) -> str:
    return next(e.evidence_id for e in result.evidence if e.check_id == check_id and e.severity != "info")


def test_facts_hold_findings_chains_and_timeline_but_no_document_text(retro_po: CaseResult,
                                                                       tampered: CaseResult) -> None:
    facts = json.loads(sr.facts(tampered.evidence, tampered.chains, tampered.timeline))
    assert {"findings", "chain_breaks", "date_anomalies"} == set(facts)
    assert all("quote" not in f for f in facts["findings"])
    assert "SYSTEM NOTE" not in json.dumps(facts)  # the hidden text is a quote: never sent
    assert facts["chain_breaks"] and facts["chain_breaks"][0]["invoice"] == "INV-1002"
    retro = json.loads(sr.facts(retro_po.evidence, retro_po.chains, retro_po.timeline))
    assert {a["kind"] for a in retro["date_anomalies"]} == {"invoice", "shipped"}


def test_grounded_points_are_kept_and_ungrounded_ones_dropped(
    monkeypatch: pytest.MonkeyPatch, tampered: CaseResult
) -> None:
    bl, bank = _id(tampered, "XDOC_BL_INVOICE"), _id(tampered, "XDOC_INVOICE_BANK")
    output = {
        "observations": [
            {"text": "INV-1002 bills 300 x 'Marine rope 50m' but the bill of lading shows 100 shipped.",
             "evidence_ids": [bl]},
            {"text": "The invoice overstates the shipment by 245 units.", "evidence_ids": [bl]},  # derived number
            {"text": "No bank credit matches the invoice.", "evidence_ids": ["E99"]},     # unknown ID
            {"text": "This looks like fraud.", "evidence_ids": [bank]},                    # accusation
            {"text": "Documents disagree.", "evidence_ids": []},                           # no citation
        ],
        "questions": [
            {"text": "Can the buyer confirm how much rope was received?", "evidence_ids": [bl]},
            {"text": "Should we approve the loan?", "evidence_ids": [bl]},                 # decision
        ],
    }
    monkeypatch.setattr(sr, "converse", _fake(output))
    review = _review(tampered)
    assert review is not None and review.source == "llm"
    assert [p.text for p in review.observations] == [output["observations"][0]["text"]]
    assert [p.text for p in review.questions] == [output["questions"][0]["text"]]
    assert review.rejected == 5


def test_nothing_grounded_falls_back_to_the_template(
    monkeypatch: pytest.MonkeyPatch, tampered: CaseResult
) -> None:
    monkeypatch.setattr(sr, "converse", _fake({"observations": [], "questions": []}))
    review = _review(tampered)
    assert review is not None and review.source == "template" and review.questions == []
    known = {e.evidence_id for e in tampered.evidence}
    assert review.observations and all(set(p.evidence_ids) <= known for p in review.observations)
    assert any("INV-1002" in p.text for p in review.observations)


def test_timeline_anomalies_feed_the_template(monkeypatch: pytest.MonkeyPatch, retro_po: CaseResult) -> None:
    monkeypatch.setattr(sr, "converse", _fake({}))
    review = _review(retro_po)
    assert review is not None
    assert {eid for p in review.observations for eid in p.evidence_ids} == {
        e.evidence_id for e in retro_po.evidence if e.check_id == "TEMP_TRADE_SEQUENCE"
    }


def test_clean_case_is_not_sent_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(sr, "converse", _fake({}, calls))
    assert _review(_assess("clean")) is None and calls == []
