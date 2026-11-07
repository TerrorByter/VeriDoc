"""
tests/test_rfi.py
-----------------
The request for information drafted from a case: only applicant-safe rule
findings and verified AI questions, never screening or suspicion (no
tipping-off), one item per problem. No AWS calls.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

import pytest

from gldb import rfi
from gldb.config import PACKS_DIR
from gldb.intake import document_paths, load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, CaseResult, Memo, ReviewPoint
from gldb.signal_library import BY_CHECK

FIXTURES = Path(__file__).parent / "fixtures" / "packs"
DEMO = Path(PACKS_DIR)


@cache
def _case(folder: Path) -> CaseResult:
    return assess(f"rfi-{folder.name}", document_paths(folder), load_manifest(folder), team=None)


def _items(result: CaseResult) -> list[str]:
    return [item.text for item in rfi.draft_request(result).items]


def test_every_request_line_is_for_a_real_check_and_names_no_suspicion() -> None:
    assert set(rfi.REQUESTS) <= set(BY_CHECK)
    for request, detail in rfi.REQUESTS.values():
        for text in (request, detail or ""):
            assert not rfi.UNSAFE.search(text), text


@pytest.mark.parametrize("check_id", ["SANCTIONS_MATCH", "HIGH_RISK_COUNTRY", "FORENSIC_HIDDEN_TEXT",
                                      "PROMPT_INJECTION", "BANK_PASS_THROUGH", "XDOC_OFFICIAL_RECORD",
                                      "META_MOD_AFTER_CREATE", "FORENSIC_INCREMENTAL_UPDATE"])
def test_screening_forensic_and_aml_findings_are_never_asked_about(check_id: str) -> None:
    assert check_id not in rfi.REQUESTS


def test_a_broken_balance_is_asked_about_with_its_own_figures() -> None:
    items = _items(_case(DEMO / "tampered_statement"))
    (balance,) = [t for t in items if "running balance" in t]
    assert "SGD 2249842.26" in balance and "SGD 1999842.26" in balance and "directly by your bank" in balance
    assert any("credit advice" in t for t in items)


def test_a_sanctioned_case_is_not_told_about_its_screening() -> None:
    draft = rfi.draft_request(_case(DEMO / "sanctioned"))
    assert not rfi.UNSAFE.search(draft.text)
    assert draft.withheld >= 1


def test_one_item_per_problem_on_a_document() -> None:
    """Form errors on one form ask for one corrected form, not one item each."""
    items = rfi.draft_request(_case(DEMO / "identity_mismatch")).items
    texts = [(i.document, i.text) for i in items]
    assert len(texts) == len(set(texts))
    assert sum("FATCA/CRS" in t for _, t in texts) == len({d for d, t in texts if "FATCA/CRS" in t})


@pytest.mark.parametrize("folder", [*sorted(p for p in DEMO.iterdir() if p.is_dir()),
                                    *sorted(p for p in FIXTURES.iterdir() if p.is_dir())],
                         ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_draft_reads_cleanly(folder: Path) -> None:
    draft = rfi.draft_request(_case(folder))
    assert "{" not in draft.text and "None" not in draft.text
    assert not rfi.UNSAFE.search(draft.text)
    assert not re.search(r"\b[EL]\d{2}\b", draft.text)  # no internal finding or lead ids


def test_repeats_of_a_check_on_a_document_are_one_item_quoting_the_first() -> None:
    """A case scored before the balance check stopped cascading still asks once."""
    result = _case(DEMO / "tampered_statement")
    first = next(e for e in result.evidence if e.check_id == "BANK_RUNNING_BAL")
    repeats = [first.model_copy(update={"evidence_id": f"E9{i}", "expected": f"SGD {i}.00"}) for i in range(3)]
    many = result.model_copy(update={"evidence": [*result.evidence, *repeats]})
    (item,) = [i for i in rfi.draft_request(many).items if "running balance" in i.text]
    assert first.expected in item.text and "SGD 0.00" not in item.text
    assert "3 more entries" in item.text and len(item.evidence_ids) == 4


def test_ai_questions_join_only_when_applicant_safe() -> None:
    result = _case(DEMO / "clean").model_copy(update={"ai_team": AgentTeam(status="ran", memo=Memo(questions=[
        ReviewPoint(text="Please explain who Harbourline Logistics is and why it paid invoice INV-1002.",
                    evidence_ids=["L04"]),
        ReviewPoint(text="Findings E28 and E29 carry additional risk signals; please explain.", evidence_ids=["E28"]),
        ReviewPoint(text="Is the director on the sanctions list related to you?", evidence_ids=["L01"]),
    ]))})
    draft = rfi.draft_request(result)
    assert [q for q in draft.questions] == [
        "Please explain who Harbourline Logistics is and why it paid invoice INV-1002."]
    assert "Harbourline" in draft.text and "sanctions" not in draft.text and "E28" not in draft.text


def test_a_case_with_no_open_findings_has_nothing_to_ask() -> None:
    draft = rfi.draft_request(_case(FIXTURES / "clean"))
    assert (draft.items, draft.questions, draft.text) == ([], [], "")


def test_a_signatory_outside_acra_is_asked_for_a_mandate() -> None:
    """A demo pack whose form is signed by someone who is not an ACRA officer."""
    texts = _items(_case(DEMO / "identity_mismatch"))
    assert any("mandate authorising" in t for t in texts)


def test_an_ai_question_about_what_the_request_already_asks_is_left_out() -> None:
    """The incomplete pack's request already asks for the missing statements: an AI question resting only on
    that finding would ask the applicant twice."""
    result = _case(DEMO / "incomplete")
    asked = rfi.asked_findings(result)
    coverage = next(e.evidence_id for e in result.evidence if e.check_id == "BANK_COVERAGE")
    assert coverage in asked
    again = ReviewPoint(text="Can you provide the bank statements for April to June?", evidence_ids=[coverage])
    new = ReviewPoint(text="Who is Harbourline Logistics to your buyer?", evidence_ids=[coverage, "L01"])
    with_ai = result.model_copy(update={"ai_team": AgentTeam(status="ran", memo=Memo(questions=[again, new]))})
    assert rfi.draft_request(with_ai).questions == [new.text]
    assert not rfi.adds_to_request(again, asked) and rfi.adds_to_request(new, asked)
