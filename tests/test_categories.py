"""
tests/test_categories.py
------------------------
The case categories code sets from what was found (gldb.categories): every
check is mapped, a check added later still lands in "Other", info findings
and unverified leads say nothing, and each demo pack shows what was planted.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import get_args

import pytest

from gldb import categories
from gldb.categories import CHECK_CATEGORY, PATTERN_CATEGORY, categorise
from gldb.config import PACKS_DIR
from gldb.intake import document_paths, load_manifest
from gldb.pipeline import assess
from gldb.schemas import AgentTeam, CaseResult, Evidence, Lead, LeadTest, Pattern
from gldb.signal_library import SIGNALS

PACKS = Path(PACKS_DIR)


@cache
def _assessed(name: str) -> CaseResult:
    folder = PACKS / name
    return assess(f"categories-{name}", document_paths(folder), load_manifest(folder), team=None)


def _labels(result: CaseResult) -> list[str]:
    return [c.label for c in categorise(result)]


def _finding(check_id: str, severity: str = "high") -> Evidence:
    return Evidence(evidence_id="E99", check_id=check_id, severity=severity, document="d", page=1,
                    method="deterministic", explanation="x")


def _lead(pattern: Pattern, status: str = "verified", lead_id: str = "L01") -> Lead:
    return Lead(lead_id=lead_id, agent="transaction", kind="pattern", pattern=pattern, hypothesis="h",
                tests=[LeadTest(cites=[], expected="", observed="")], investigation_status="supported",
                evidence_status=status)


def test_every_check_and_pattern_has_a_category() -> None:
    checks = {s.check_id for s in SIGNALS}
    assert checks - set(CHECK_CATEGORY) == set()
    assert set(CHECK_CATEGORY) - checks == set(), "a mapped check no longer exists"
    assert set(PATTERN_CATEGORY) == set(get_args(Pattern))


def test_a_clean_case_has_no_category() -> None:
    assert categorise(_assessed("clean")) == []


@pytest.mark.parametrize(("pack", "expected"), [
    ("tampered_statement", "Tampered document"),
    ("inflated_financials", "Financial misstatement"),
    ("invoice_mismatch", "Trade document mismatch"),
    ("identity_mismatch", "Identity / registry"),
    ("sanctioned", "Sanctions / AML"),
    ("struck_off", "Identity / registry"),
    ("incomplete", "Incomplete application"),
    ("third_party_payment", "Third-party payment"),
])
def test_each_pack_shows_what_was_planted(pack: str, expected: str) -> None:
    found = categorise(_assessed(pack))
    assert expected in [c.label for c in found]
    assert all(c.source == "rules" and c.ids for c in found)


def test_a_category_names_the_findings_behind_it() -> None:
    result = _assessed("tampered_statement")
    tampered = next(c for c in categorise(result) if c.category == "tampered_document")
    by_id = {e.evidence_id: e for e in result.evidence}
    assert all(CHECK_CATEGORY[by_id[i].check_id] == "tampered_document" for i in tampered.ids)


@pytest.mark.parametrize("pack", ["related_party", "round_trip"])
def test_an_ai_only_fraud_has_no_category_from_the_rules(pack: str) -> None:
    """The rules cannot see an undisclosed related party or a round trip:
    without the AI team the case shows none."""
    assert categorise(_assessed(pack)) == []


def test_info_findings_say_nothing_is_wrong() -> None:
    result = _assessed("clean").model_copy(update={"evidence": [_finding("SANCTIONS_MATCH", "info")]})
    assert categorise(result) == []


def test_a_check_added_later_falls_back_to_its_family(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(categories.CHECK_CATEGORY, "XDOC_BL_INVOICE")
    result = _assessed("clean").model_copy(update={"evidence": [_finding("XDOC_BL_INVOICE")]})
    [found] = categorise(result)
    assert (found.category, found.label) == ("other", "Other: transactions")


def test_a_check_outside_the_library_is_still_labelled() -> None:
    result = _assessed("clean").model_copy(update={"evidence": [_finding("SOMETHING_NEW")]})
    assert _labels(result) == ["Other"]


def test_only_leads_code_verified_or_corroborated_count() -> None:
    team = AgentTeam(status="ran", leads=[
        _lead("third_party_payment", "verified", "L01"),
        _lead("round_trip", "corroborated", "L02"),
        _lead("related_party", "unverified", "L03"),
        _lead("business_mismatch", "contradicted", "L04"),
    ])
    found = categorise(_assessed("clean").model_copy(update={"ai_team": team}))
    assert [(c.label, c.source, c.ids) for c in found] == [
        ("Third-party payment", "ai_team", ["L01"]),
        ("Round trip", "ai_team", ["L02"]),
    ]


def test_findings_of_one_category_are_one_entry_most_serious_first() -> None:
    evidence = [_finding("CHECKLIST_MISSING_DOC"), _finding("SANCTIONS_MATCH"), _finding("FS_BALANCE"),
                _finding("FS_ACRA_MISMATCH")]
    evidence = [e.model_copy(update={"evidence_id": f"E0{i}"}) for i, e in enumerate(evidence, 1)]
    found = categorise(_assessed("clean").model_copy(update={"evidence": evidence}))
    assert [(c.label, c.ids) for c in found] == [
        ("Sanctions / AML", ["E02"]),
        ("Financial misstatement", ["E03", "E04"]),
        ("Incomplete application", ["E01"]),
    ]
