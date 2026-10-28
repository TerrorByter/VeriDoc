"""
tests/test_signal_library.py
----------------------------
Tests for gldb/signal_library.py and the itemised risk score: every check
maps to a named, versioned signal, and every point of the score traces to a
piece of evidence.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Literal

from gldb import checks, signal_library
from gldb.scoring import score
from gldb.schemas import Evidence

CHECKS_SOURCE = Path(checks.__file__)
_EVIDENCE_HELPERS = {"_ev", "_cert_ev"}


def _emitted_check_ids() -> set[str]:
    """Every literal check ID passed to the evidence helpers in checks.py."""
    tree = ast.parse(CHECKS_SOURCE.read_text(encoding="utf-8-sig"))  # file has a BOM
    return {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _EVIDENCE_HELPERS
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }


def _ev(
    check_id: str,
    severity: Literal["info", "low", "medium", "high", "critical"] = "medium",
    evidence_id: str = "E01",
    category: Literal["fraud_signal", "compliance"] = "fraud_signal",
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id, check_id=check_id, category=category, severity=severity,
        document="invoice_INV-1002", page=2, method="deterministic", confidence=1.0,
        explanation="x",
    )


def test_every_check_the_agents_can_emit_has_a_signal() -> None:
    emitted = _emitted_check_ids()
    assert len(emitted) >= 30  # the AST scan really found the checks
    assert emitted - signal_library.BY_CHECK.keys() == set()


def test_signal_ids_are_unique_and_in_known_families() -> None:
    ids = [s.signal_id for s in signal_library.SIGNALS]
    assert len(ids) == len(set(ids))
    assert {i.split("-")[0] for i in ids} <= {"DOC", "FIN", "TXN", "REG", "ENT", "TEMP", "COMP", "SYS"}
    assert all(s.name and s.remedy for s in signal_library.SIGNALS)


def test_score_is_an_itemised_sum_traced_to_evidence() -> None:
    risk = score([
        _ev("FORENSIC_HIDDEN_TEXT", "high", "E01"),
        _ev("XDOC_INVOICE_BANK", "medium", "E02"),
        _ev("BANK_COVERAGE", "high", "E03", category="compliance"),  # never scores
        _ev("FORENSIC_OCR_SKIPPED", "info", "E04"),                   # zero points: omitted
    ])
    assert [(c.signal_id, c.evidence_id, c.points) for c in risk.contributions] == [
        ("DOC-001", "E01", 30), ("TXN-001", "E02", 15),
    ]
    assert risk.score == sum(c.points for c in risk.contributions) == 45
    first = risk.contributions[0]
    assert (first.check_id, first.document, first.page) == ("FORENSIC_HIDDEN_TEXT", "invoice_INV-1002", 2)
    assert first.name == signal_library.BY_CHECK["FORENSIC_HIDDEN_TEXT"].name
    assert risk.rule_version == signal_library.RULE_VERSION


def test_capped_score_still_lists_every_contribution() -> None:
    risk = score([_ev(check, "critical", f"E0{i}") for i, check in enumerate(("INV_ARITH", "BANK_RUNNING_BAL", "XDOC_IDENTITY"), 1)])
    assert risk.score == 100
    assert sum(c.points for c in risk.contributions) == 150


def test_unmapped_check_still_scores_and_is_labelled() -> None:
    (c,) = score([_ev("BRAND_NEW_CHECK", "low")]).contributions
    assert (c.signal_id, c.points) == ("UNMAPPED", 5)
