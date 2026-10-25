"""
tests/test_fillable_statement.py
--------------------------------
The team's bank statement is a fillable form: its figures are values in form
fields, not page text. So the text layer is only labels; OCR must still read
the page (only the FATCA/CRS forms, which carry tax identifiers, are kept
from Textract), and what a model is shown must include the field values. The
OCR text in tests/fixtures/ocr is what Textract really returned for the
sample statement. No AWS calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gldb.checks import check_forensic_ocr_mismatch
from gldb.forensics import form_values
from gldb.intake import intake, model_view, read_text
from gldb.ocr import ocr_files
from tests.acma_records import pack

SAMPLE = Path("data/templates/sample")
STATEMENT = SAMPLE / "ACMA BANK 1.pdf"
ENTITY, INDIVIDUAL = SAMPLE / "ACMA ENTITY.pdf", SAMPLE / "ACMA INDIV.pdf"
WORKBOOK = SAMPLE / "Submitted financials.xlsx"
SEEN = (Path(__file__).parent / "fixtures" / "ocr" / "acma_bank_1.txt").read_text(encoding="utf-8")
TEXT_PDF = Path(__file__).parent / "fixtures" / "packs" / "clean" / "bank_statement_2026-02.pdf"


class CountingTextract:
    """A Textract that records each page it is asked to read."""

    def __init__(self) -> None:
        self.pages = 0

    def detect_document_text(self, **_: Any) -> dict[str, Any]:
        self.pages += 1
        return {"Blocks": [{"BlockType": "LINE", "Text": "STATEMENT OF ACCOUNT", "Confidence": 99.0}]}


@pytest.fixture(autouse=True)
def _usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))


def _statement_pack(seen: str) -> Any:
    (doc,) = intake("t", [STATEMENT])
    return pack(docs=[doc], paths={doc.doc_id: STATEMENT}, ocr={doc.doc_id: [seen]})


# ---------------------------------------------------------------------------
# The field values
# ---------------------------------------------------------------------------

def test_the_text_layer_of_the_statement_is_only_its_labels() -> None:
    assert "STATEMENT OF ACCOUNT" in read_text(STATEMENT)[0]
    assert "862148.58" not in read_text(STATEMENT)[0].replace(",", "")


def test_form_values_come_back_in_reading_order() -> None:
    lines = form_values(STATEMENT)[0].splitlines()
    assert any(line.startswith("02/01/2026 Electronic Supplies - ABC electronics Ltd.") and "283932.40" in line for line in lines)
    assert "111-234-567-890" in "\n".join(lines)


def test_a_pdf_without_a_form_has_no_values() -> None:
    assert set(form_values(TEXT_PDF)) == {""}


def test_what_a_model_is_given_includes_the_values_of_a_fillable_form() -> None:
    view = model_view(STATEMENT)[0]
    assert "STATEMENT OF ACCOUNT" in view and "111-234-567-890" in view and "Electronic Supplies" in view
    assert model_view(TEXT_PDF) == read_text(TEXT_PDF, visible_only=True)  # a PDF without a form is as before


# ---------------------------------------------------------------------------
# Which documents are OCR'd
# ---------------------------------------------------------------------------

def test_ocr_reads_the_fillable_statement_but_not_the_tax_forms_or_the_workbook() -> None:
    textract = CountingTextract()
    result = ocr_files([STATEMENT, ENTITY, INDIVIDUAL, WORKBOOK], client=textract)
    assert set(result.text) == {"ACMA BANK 1"} and textract.pages == 1


# ---------------------------------------------------------------------------
# What OCR sees against what the file stores
# ---------------------------------------------------------------------------

def test_what_textract_reads_of_the_statement_agrees_with_what_the_file_stores() -> None:
    assert check_forensic_ocr_mismatch(_statement_pack(SEEN)) == []


def test_a_figure_on_the_page_that_the_form_does_not_store_is_flagged() -> None:
    (found,) = check_forensic_ocr_mismatch(_statement_pack(SEEN + "\n999,999.99\n"))
    assert (found.check_id, found.document, found.page) == ("FORENSIC_OCR_MISMATCH", "ACMA BANK 1", 1)
    assert found.observed == "999,999.99"


def test_a_stored_figure_the_page_does_not_show_is_flagged() -> None:
    (found,) = check_forensic_ocr_mismatch(_statement_pack(SEEN.replace("862,148.58", "")))
    assert found.expected == "862148.58" and found.observed is None


def test_a_case_where_nothing_was_read_says_so_rather_than_that_ocr_is_off() -> None:
    (found,) = check_forensic_ocr_mismatch(pack())
    assert found.check_id == "FORENSIC_OCR_SKIPPED" and "no page was read" in found.explanation
