"""
tests/test_workbook_intake.py
-----------------------------
A case accepts .xlsx workbooks beside PDFs: they are validated, hashed,
classified by their sheets and read as text per sheet. Stages that only make
sense for PDFs (OCR, PDF forensics) leave them alone. Workbooks are built on
the fly. No AWS calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from gldb.checks import run_rule_checks
from gldb.intake import IntakeError, document_paths, intake, model_view, read_text, validate_file
from gldb.ocr import ocr_files
from gldb.schemas import ExtractedPack
from tests.text_pdfs import text_pdf
from tests.workbooks import STATEMENTS, workbook


def test_a_workbook_is_accepted(tmp_path: Path) -> None:
    validate_file(workbook(tmp_path, STATEMENTS))


@pytest.mark.parametrize("name", ["old.xls", "letter.docx", "data.csv"])
def test_other_spreadsheet_and_office_formats_are_refused(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"PK\x03\x04 anything")
    with pytest.raises(IntakeError, match=r"Unsupported file type"):
        validate_file(path)


def test_a_file_named_xlsx_that_is_not_a_zip_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "fake.xlsx"
    path.write_bytes(b"%PDF-1.4 hello")
    with pytest.raises(IntakeError, match="fake.xlsx"):
        validate_file(path)


def test_a_pdf_named_xlsx_and_an_xlsx_named_pdf_are_refused(tmp_path: Path) -> None:
    pdf = text_pdf(tmp_path, "a.pdf", [["hello"]])
    renamed = tmp_path / "renamed.xlsx"
    renamed.write_bytes(pdf.read_bytes())
    with pytest.raises(IntakeError, match="renamed.xlsx"):
        validate_file(renamed)
    book = workbook(tmp_path, STATEMENTS)
    other = tmp_path / "book.pdf"
    other.write_bytes(book.read_bytes())
    with pytest.raises(IntakeError, match="book.pdf"):
        validate_file(other)


def test_the_statements_workbook_is_classified_by_its_sheets(tmp_path: Path) -> None:
    (doc,) = intake("t", [workbook(tmp_path, STATEMENTS)])
    assert (doc.doc_id, doc.filename, doc.doc_type) == ("Submitted financials", "Submitted financials.xlsx", "financial_statements")
    assert len(doc.sha256) == 64


def test_any_other_workbook_is_unknown(tmp_path: Path) -> None:
    (doc,) = intake("t", [workbook(tmp_path, {"Sheet1": [["hello"]]})])
    assert doc.doc_type == "unknown"


def test_a_workbooks_sheets_count_as_pages_and_its_properties_as_metadata(tmp_path: Path) -> None:
    (doc,) = intake("t", [workbook(tmp_path, STATEMENTS)])
    assert doc.page_count == 3
    assert doc.creation_date == datetime(2026, 10, 8, 4, 50, 8, tzinfo=timezone.utc)
    assert doc.mod_date == datetime(2026, 10, 14, 8, 48, 23, tzinfo=timezone.utc)
    assert doc.producer == "Microsoft Excel"


def test_an_unreadable_workbook_is_an_intake_error(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    path.write_bytes(path.read_bytes()[:200])
    with pytest.raises(IntakeError, match="Submitted financials.xlsx"):
        intake("t", [path])


def test_text_is_read_per_sheet_and_a_model_sees_the_same(tmp_path: Path) -> None:
    path = workbook(tmp_path, STATEMENTS)
    pages = read_text(path)
    assert len(pages) == 3 and "Total Assets\t6.749" in pages[1]
    assert model_view(path) == pages


def test_a_case_folder_yields_its_documents_in_order(tmp_path: Path) -> None:
    text_pdf(tmp_path, "b.pdf", [["x"]])
    workbook(tmp_path, STATEMENTS, name="a.xlsx")
    (tmp_path / "application.json").write_text("{}")
    (tmp_path / "notes.txt").write_text("hi")
    assert [p.name for p in document_paths(tmp_path)] == ["a.xlsx", "b.pdf"]


def test_ocr_leaves_workbooks_alone(tmp_path: Path) -> None:
    class Refuses:
        def detect_document_text(self, **_kwargs: Any) -> None:
            raise AssertionError("a workbook is not sent to Textract")

    assert ocr_files([workbook(tmp_path, STATEMENTS)], client=Refuses()).text == {}


def test_pdf_forensics_and_metadata_checks_leave_workbooks_alone(tmp_path: Path) -> None:
    """Spreadsheets are edited after they are created as a matter of course."""
    path = workbook(tmp_path, STATEMENTS)
    docs = intake("t", [path])
    pack = ExtractedPack(case_id="t", docs=docs, invoices=[], bank_statements=[], financial_statements=[],
                         extraction_mode="stub", paths={docs[0].doc_id: path})
    evidence, _ = run_rule_checks(pack)
    assert {e.check_id for e in evidence if e.category == "fraud_signal"} <= {
        "FORENSIC_OCR_SKIPPED", "PROMPT_SCREEN_SKIPPED",
    }
