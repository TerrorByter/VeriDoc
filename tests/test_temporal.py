"""
tests/test_temporal.py
----------------------
Temporal consistency: the TEMP_* checks and the case timeline. Built from
the real clean pack, changing one date per test. No AWS calls.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from gldb.checks import (
    check_temp_before_incorporation,
    check_temp_trade_sequence,
    check_xdoc_po_invoice,
)
from gldb.pipeline import pack_from_folder
from gldb.schemas import ExtractedPack
from gldb.timeline import build_timeline

PACKS = Path(__file__).parent.parent / "data" / "packs"


@pytest.fixture
def clean() -> ExtractedPack:
    return pack_from_folder(PACKS / "clean")


def _invoice_dated(pack: ExtractedPack, on: date) -> ExtractedPack:
    invoices = [pack.invoices[0].model_copy(update={"invoice_date": on}), *pack.invoices[1:]]
    return pack.model_copy(update={"invoices": invoices})


def _shipped(pack: ExtractedPack, on: date) -> ExtractedPack:
    bls = [pack.bills_of_lading[0].model_copy(update={"shipped_date": on}), *pack.bills_of_lading[1:]]
    return pack.model_copy(update={"bills_of_lading": bls})


def test_clean_pack_has_no_temporal_findings(clean: ExtractedPack) -> None:
    assert check_temp_before_incorporation(clean) == []
    assert check_temp_trade_sequence(clean) == []


def test_invoice_before_its_po_is_a_sequence_break_only(clean: ExtractedPack) -> None:
    pack = _invoice_dated(clean, date(2026, 5, 1))
    (ev,) = check_temp_trade_sequence(pack)
    assert (ev.check_id, ev.category, ev.severity) == ("TEMP_TRADE_SEQUENCE", "fraud_signal", "medium")
    assert ev.document == "invoice_INV-1001"
    assert "before its purchase order PO-2001" in ev.explanation
    assert ev.observed == "2026-05-01"
    assert check_xdoc_po_invoice(pack) == []  # one fact, counted once


def test_goods_shipped_before_they_were_ordered(clean: ExtractedPack) -> None:
    pack = _shipped(clean, date(2026, 5, 20))
    (ev,) = check_temp_trade_sequence(pack)
    assert ev.document == "bill_of_lading_BL-3001"
    assert "shipped on 2026-05-20, before purchase order PO-2001" in ev.explanation


def test_document_dated_before_incorporation_is_high(clean: ExtractedPack) -> None:
    pack = _invoice_dated(clean, date(2022, 12, 1))
    (ev,) = check_temp_before_incorporation(pack)
    assert (ev.check_id, ev.severity, ev.document) == (
        "TEMP_BEFORE_INCORPORATION", "high", "invoice_INV-1001",
    )
    assert "2023-01-16" in ev.explanation and "ACRA-SYN" in ev.explanation
    assert ev.observed == "2022-12-01"


def test_unregistered_company_uses_the_application_date(clean: ExtractedPack) -> None:
    assert clean.manifest is not None
    manifest = clean.manifest.model_copy(update={
        "uen": "209999999Z", "date_of_incorporation": date(2026, 6, 15),
    })
    found = check_temp_before_incorporation(clean.model_copy(update={"manifest": manifest}))
    assert "invoice_INV-1001" in {e.document for e in found}
    assert "invoice_INV-1002" not in {e.document for e in found}
    assert all("application" in e.explanation for e in found)


def test_no_manifest_means_no_incorporation_check(clean: ExtractedPack) -> None:
    assert check_temp_before_incorporation(clean.model_copy(update={"manifest": None})) == []


def test_timeline_traces_each_trade_in_order(clean: ExtractedPack) -> None:
    events = build_timeline(clean, [])
    assert [e.on for e in events] == sorted(e.on for e in events)
    assert (events[0].kind, events[0].on) == ("incorporated", date(2023, 1, 16))
    assert events[-1].kind == "application"
    trade = [e.kind for e in events if e.ref == "INV-1002"]
    assert trade == ["purchase_order", "shipped", "invoice", "payment"]


def test_timeline_marks_the_event_a_finding_is_about(clean: ExtractedPack) -> None:
    pack = _shipped(clean, date(2026, 5, 20))
    evidence = check_temp_trade_sequence(pack)
    evidence[0].evidence_id = "E07"
    events = build_timeline(pack, evidence)
    flagged = [e for e in events if e.evidence_ids]
    assert [(e.kind, e.doc_id, e.evidence_ids) for e in flagged] == [
        ("shipped", "bill_of_lading_BL-3001", ["E07"]),
    ]
