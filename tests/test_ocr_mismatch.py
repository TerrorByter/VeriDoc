"""
tests/test_ocr_mismatch.py
--------------------------
Tests for the text-layer vs OCR comparison: the pure comparison in
gldb/forensics.py, OCR fill-in in intake.read_text, the OCR stage in
gldb/ocr.py and gldb/pipeline.py, the Forensics check, the pasted-image
tamper in the generated packs, and OCR-only ground truth in evaluate.
No AWS calls: Textract is faked.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber
import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen.canvas import Canvas

from gldb import evaluate, extract_stub, forensics, pipeline
from gldb.checks import AGENTS, check_forensic_ocr_mismatch
from gldb.data_factory import ENTITY_TEMPLATE, generate_packs
from gldb.intake import read_text
from gldb.ocr import OcrResult, ocr_files
from gldb.schemas import DocMeta, ExtractedPack

CLEAN = Path(__file__).parent.parent / "data" / "packs" / "clean"


def _pdf(tmp_path: Path, pages: list[str], name: str = "doc.pdf") -> Path:
    path = tmp_path / name
    canvas = Canvas(str(path), pagesize=A4)
    for text in pages:
        if text:
            canvas.setFont("Helvetica", 10)
            canvas.drawString(72, 700, text)
        canvas.showPage()
    canvas.save()
    return path


def _pack(path: Path, ocr: dict[str, list[str]] | None = None) -> ExtractedPack:
    doc = DocMeta(
        doc_id=path.stem, filename=path.name, sha256="0" * 64, doc_type="financial_statements",
        page_count=1, creation_date=None, mod_date=None, producer=None,
    )
    return ExtractedPack(
        case_id="t", docs=[doc], invoices=[], bank_statements=[],
        financial_statements=[], extraction_mode="stub",
        paths={path.stem: path}, ocr=ocr or {},
    )


# ---------------------------------------------------------------------------
# Pure comparison
# ---------------------------------------------------------------------------


def test_matching_layers_have_no_mismatch() -> None:
    text = ["Revenue: SGD 1,850,000.00\nTotal: 2,400,000.00"]
    ocr = ["Total: 2,400,000.00\nRevenue: SGD 1,850,000.00"]
    assert forensics.amount_mismatches(text, ocr) == []


def test_covered_and_pasted_amounts_are_reported_per_page() -> None:
    text = ["Revenue: SGD 1,850,000.00", "Total: 10.00"]
    ocr = ["Revenue: SGD 3,850,000.00", "Total: 10.00"]
    assert forensics.amount_mismatches(text, ocr) == [
        forensics.AmountMismatch(page=1, covered=("1,850,000.00",), pasted=("3,850,000.00",))
    ]


def test_formatting_and_non_money_numbers_are_ignored() -> None:
    text = ["Date 2026-07-05 UEN 202301234K page 2 Amount 1,850,000.00"]
    ocr = ["Date 2026-07-06 UEN 202301284K page 3 Amount 1850000.00"]
    assert forensics.amount_mismatches(text, ocr) == []


def test_page_without_text_layer_is_not_compared() -> None:
    assert forensics.amount_mismatches([""], ["Total: 99.00"]) == []


# ---------------------------------------------------------------------------
# read_text fills blank pages from OCR
# ---------------------------------------------------------------------------


def test_read_text_fills_only_blank_pages_from_ocr(tmp_path: Path) -> None:
    path = _pdf(tmp_path, ["TAX INVOICE", ""])
    assert read_text(path) == ["TAX INVOICE", ""]
    assert read_text(path, ocr=["OCR one", "OCR two"]) == ["TAX INVOICE", "OCR two"]


# ---------------------------------------------------------------------------
# Parsing OCR text (Textract reads the "|" cell divider as "I")
# ---------------------------------------------------------------------------

_OCR_STATEMENT = """STATEMENT OF ACCOUNT
Bank: Harbourfront Bank
Account Holder: Lumen Harbour Trading Pte. Ltd.
Period: 2026-03-01 to 2026-03-31
Opening Balance: SGD 91,600.00
Date I Description I Debit I Credit I Balance
2026-03-05 I OFFICE RENT MAR I 8,000.00 I - I 83,600.00
2026-03-18 I GIRO CR CUSTOMER RECEIPTS I - I 38,250.00 I 121,850.00
Closing Balance: SGD 121,850.00"""


def test_stub_parser_reads_ocr_table_dividers() -> None:
    stmt = extract_stub._parse_bank_statement("s", [_OCR_STATEMENT])
    assert [t.description for t in stmt.transactions] == [
        "OFFICE RENT MAR", "GIRO CR CUSTOMER RECEIPTS",
    ]
    inv = extract_stub._parse_invoice("i", [
        "TAX INVOICE\nSeller: S\nSeller UEN: 1\nBuyer: B\nInvoice No: X\n"
        "Invoice Date: 2026-06-10\nDescription I Qty I Unit Price I Amount\n"
        "LED panel lights I 200 I 90.00 I 18,000.00\n"
        "Subtotal: SGD 18,000.00\nGST (9%): SGD 1,620.00\nTotal: SGD 19,620.00"
    ])
    assert [(i.description, i.quantity, i.amount) for i in inv.line_items] == [
        ("LED panel lights", 200, Decimal("18000.00")),
    ]


def test_unparseable_table_row_raises_instead_of_vanishing() -> None:
    garbled = _OCR_STATEMENT.replace("2026-03-05 I OFFICE", "2026-O3-O5 I OFFICE")
    with pytest.raises(ValueError, match="s: cannot parse table row"):
        extract_stub._parse_bank_statement("s", [garbled])


def test_ambiguous_ocr_row_raises() -> None:
    text = _OCR_STATEMENT.replace("OFFICE RENT MAR", "PHASE I RENT")
    with pytest.raises(ValueError, match="cannot parse table row"):
        extract_stub._parse_bank_statement("s", [text])


# ---------------------------------------------------------------------------
# OCR stage
# ---------------------------------------------------------------------------


class FakeTextract:
    def __init__(self) -> None:
        self.calls = 0

    def detect_document_text(self, Document: dict[str, bytes]) -> dict[str, Any]:
        assert Document["Bytes"].startswith(b"%PDF")
        self.calls += 1
        return {"Blocks": [
            {"BlockType": "PAGE", "Confidence": 1.0},
            {"BlockType": "LINE", "Text": "seen", "Confidence": 90.0},
            {"BlockType": "LINE", "Text": "too", "Confidence": 70.0},
        ]}


@pytest.mark.skipif(not ENTITY_TEMPLATE.exists(), reason="AcroForm templates not present")
def test_ocr_files_ocrs_every_page_but_skips_fillable_forms() -> None:
    paths = [CLEAN / "fs_summary.pdf", CLEAN / "fatca_crs_entity.pdf"]
    fake = FakeTextract()
    result = ocr_files(paths, client=fake)
    assert result.text == {"fs_summary": ["seen\ntoo"]}
    assert result.confidence == {"fs_summary": [80.0]}  # mean of LINE confidences
    assert fake.calls == 1


def test_pipeline_runs_ocr_once_and_keeps_it_on_the_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = _pdf(tmp_path, ["MEMO"], "memo.pdf")
    calls: list[list[Path]] = []

    def fake_ocr(paths: list[Path]) -> OcrResult:
        calls.append(paths)
        return OcrResult(text={"memo": ["MEMO"]}, confidence={"memo": [55.0]})

    monkeypatch.setattr(pipeline, "OCR_ENABLED", True)
    monkeypatch.setattr(pipeline, "ocr_files", fake_ocr)
    result = pipeline.assess("t", [path], None)
    assert calls == [[path]]
    assert result.pack.ocr == {"memo": ["MEMO"]}
    assert "ocr" not in result.model_dump()["pack"]
    assert result.pack.ocr_confidence == {"memo": [55.0]}
    assert result.routing is not None
    assert "memo page 1: OCR confidence 55% is below 80%" in result.routing.low_confidence


def test_pipeline_skips_ocr_when_disabled(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = _pdf(tmp_path, ["MEMO"], "memo.pdf")
    monkeypatch.setattr(pipeline, "OCR_ENABLED", False)
    monkeypatch.setattr(pipeline, "ocr_files", lambda paths: pytest.fail("OCR called"))
    assert pipeline.assess("t", [path], None).pack.ocr == {}


# ---------------------------------------------------------------------------
# Forensics check
# ---------------------------------------------------------------------------


def test_check_flags_amount_the_reader_sees_but_the_file_does_not_hold(tmp_path: Path) -> None:
    path = _pdf(tmp_path, ["Revenue: SGD 1,850,000.00"])
    (ev,) = check_forensic_ocr_mismatch(_pack(path, {path.stem: ["Revenue: SGD 3,850,000.00"]}))
    assert (ev.check_id, ev.category, ev.severity, ev.page) == (
        "FORENSIC_OCR_MISMATCH", "fraud_signal", "medium", 1,
    )
    assert ev.expected == "1,850,000.00"
    assert ev.observed == "3,850,000.00"


def test_check_is_silent_when_layers_agree(tmp_path: Path) -> None:
    path = _pdf(tmp_path, ["Revenue: SGD 1,850,000.00"])
    assert check_forensic_ocr_mismatch(_pack(path, {path.stem: ["Revenue: SGD 1,850,000.00"]})) == []


def test_check_reports_itself_skipped_without_ocr(tmp_path: Path) -> None:
    (ev,) = check_forensic_ocr_mismatch(_pack(_pdf(tmp_path, ["Total: 1.00"])))
    assert (ev.check_id, ev.severity) == ("FORENSIC_OCR_SKIPPED", "info")


def test_ocr_text_never_leaks_into_serialised_pack(tmp_path: Path) -> None:
    pack = _pack(_pdf(tmp_path, ["x"]), {"doc": ["secret OCR text"]})
    assert "ocr" not in pack.model_dump()
    assert "secret OCR text" not in pack.model_dump_json()


def test_forensics_agent_runs_the_ocr_check() -> None:
    agent = next(a for a in AGENTS if a.name == "Forensics")
    assert check_forensic_ocr_mismatch in agent.checks


# ---------------------------------------------------------------------------
# Generated packs and evaluation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def packs(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if not ENTITY_TEMPLATE.exists():
        pytest.skip("AcroForm templates not present")
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base


def test_tampered_revenue_is_pasted_as_an_image_over_the_original(packs: Path) -> None:
    with pdfplumber.open(str(packs / "tampered" / "fs_summary.pdf")) as pdf:
        page = pdf.pages[0]
        assert "Revenue: SGD 1,850,000.00" in (page.extract_text() or "")
        assert len(page.images) == 1
    with pdfplumber.open(str(packs / "clean" / "fs_summary.pdf")) as pdf:
        assert pdf.pages[0].images == []


def test_ground_truth_states_the_expected_route(packs: Path) -> None:
    paths = {
        p: json.loads((packs / p / "ground_truth.json").read_text())["expected_path"]
        for p in ("clean", "tampered", "incomplete", "scanned")
    }
    assert paths == {
        "clean": "fast_track", "tampered": "enhanced_review",
        "incomplete": "standard_review", "scanned": "fast_track",
    }


def test_ground_truth_lists_ocr_only_checks_separately(packs: Path) -> None:
    truth = {p: json.loads((packs / p / "ground_truth.json").read_text()) for p in ("clean", "tampered")}
    assert truth["tampered"]["expected_check_ids_ocr"] == ["FORENSIC_OCR_MISMATCH"]
    assert "FORENSIC_OCR_MISMATCH" not in truth["tampered"]["expected_check_ids"]
    assert truth["clean"]["expected_check_ids_ocr"] == []


def test_scanned_pack_is_image_only_copy_of_clean(packs: Path) -> None:
    scanned, clean = packs / "scanned", packs / "clean"
    assert sorted(p.name for p in scanned.glob("*.pdf")) == sorted(p.name for p in clean.glob("*.pdf"))
    assert read_text(scanned / "invoice_INV-1001.pdf") == [""]
    with pdfplumber.open(str(scanned / "invoice_INV-1001.pdf")) as pdf:
        assert len(pdf.pages[0].images) == 1
    truth = json.loads((scanned / "ground_truth.json").read_text())
    assert truth["requires_ocr"] is True
    assert (truth["expected_band"], truth["expected_check_ids"]) == ("Low", [])


def test_evaluate_skips_ocr_only_packs_when_ocr_is_off(packs: Path) -> None:
    report = evaluate.evaluate(packs)
    assert report.skipped == ["scanned"]
    assert "scanned" not in [r.pack for r in report.packs]


def test_evaluate_expects_ocr_checks_only_when_ocr_is_on() -> None:
    truth = {
        "expected_check_ids": ["A"],
        "expected_compliance_check_ids": ["B"],
        "expected_check_ids_ocr": ["C"],
    }
    assert evaluate.expected_ids(truth, ocr_enabled=False) == {("A", True), ("B", False)}
    assert evaluate.expected_ids(truth, ocr_enabled=True) == {("A", True), ("B", False), ("C", True)}
