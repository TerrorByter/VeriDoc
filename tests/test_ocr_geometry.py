"""
tests/test_ocr_geometry.py
--------------------------
Textract line geometry: kept from OCR, stored with the case on the server,
and used to highlight quotes on scanned pages. Fake Textract; no AWS calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gldb import api, pipeline
from gldb.ocr import ocr_files
from gldb.schemas import Box, OcrLine
from gldb.viewer import locate

PACKS = Path(__file__).parent.parent / "data" / "packs"
SCANNED = PACKS / "scanned" / "invoice_INV-1001.pdf"


def _line(text: str, left: float, top: float, width: float = 0.4) -> dict[str, Any]:
    return {"BlockType": "LINE", "Text": text, "Confidence": 99.0,
            "Geometry": {"BoundingBox": {"Left": left, "Top": top, "Width": width, "Height": 0.02}}}


class FakeTextract:
    def detect_document_text(self, **_: Any) -> dict[str, Any]:
        return {"Blocks": [_line("Invoice No: INV-1001", 0.1, 0.10), _line("Total: SGD 32,700.00", 0.1, 0.30)]}


@pytest.fixture(autouse=True)
def _usage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))


def test_ocr_keeps_each_line_with_its_box() -> None:
    result = ocr_files([SCANNED], client=FakeTextract())
    (page,) = result.lines["invoice_INV-1001"]
    assert page[1] == OcrLine(text="Total: SGD 32,700.00", box=Box(x0=0.1, top=0.3, x1=0.5, bottom=0.32))


LINES = [
    OcrLine(text="Invoice No: INV-1001", box=Box(x0=0.1, top=0.10, x1=0.5, bottom=0.12)),
    OcrLine(text="Total: SGD 32,700.00", box=Box(x0=0.1, top=0.30, x1=0.5, bottom=0.32)),
    OcrLine(text="Payment due in", box=Box(x0=0.1, top=0.40, x1=0.3, bottom=0.42)),
    OcrLine(text="30 days", box=Box(x0=0.1, top=0.43, x1=0.2, bottom=0.45)),
]


def test_scanned_page_quote_is_placed_from_ocr_lines() -> None:
    (box,) = locate(SCANNED, 1, "Total: SGD 32,700.00", LINES)
    assert (box.top, box.bottom) == (0.30, 0.32)


def test_part_of_a_line_is_narrowed_in_proportion() -> None:
    (box,) = locate(SCANNED, 1, "INV-1001", LINES)
    assert 0.1 < box.x0 < box.x1 <= 0.5 and box.top == 0.10


def test_quote_across_two_lines_gets_both() -> None:
    boxes = locate(SCANNED, 1, "Payment due in 30 days", LINES)
    assert [b.top for b in boxes] == [0.40, 0.43]


def test_text_layer_wins_when_the_page_has_one() -> None:
    digital = PACKS / "clean" / "invoice_INV-1001.pdf"
    assert locate(digital, 1, "Total: SGD 32,700.00", []) == locate(digital, 1, "Total: SGD 32,700.00")


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    for module in (api, pipeline):
        monkeypatch.setattr(module, "CASES_DIR", str(tmp_path / "cases"))
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    return TestClient(api.app)


def test_ocr_lines_are_stored_with_the_case_not_shown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: TestClient
) -> None:
    monkeypatch.setattr(pipeline, "OCR_ENABLED", True)
    monkeypatch.setattr("gldb.ocr._default_client", lambda: FakeTextract())
    cid = client.post("/api/cases/from-pack/clean").json()["case_id"]
    case = client.get(f"/api/cases/{cid}").json()
    stored = json.loads((tmp_path / "cases" / case["case_id"] / "ocr_lines.json").read_text())
    assert stored["invoice_INV-1001"][0][1]["text"] == "Total: SGD 32,700.00"
    assert "Invoice No: INV-1001" not in json.dumps(case)


def test_viewer_places_quotes_on_scanned_pages_from_stored_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, client: TestClient
) -> None:
    cid = client.post("/api/cases/from-pack/clean").json()["case_id"]
    # The case's invoice is now the scanned copy: no text layer to search
    (tmp_path / "uploads" / cid / "invoice_INV-1001.pdf").write_bytes(SCANNED.read_bytes())
    (tmp_path / "cases" / cid / "ocr_lines.json").write_text(
        json.dumps({"invoice_INV-1001": [[line.model_dump() for line in LINES]]})
    )
    result = api._load(cid)
    finding = api.Evidence(evidence_id="E90", check_id="INV_ARITH", severity="high",
                           document="invoice_INV-1001", page=1, quote="Total: SGD 32,700.00",
                           method="deterministic", confidence=1.0, explanation="x")
    monkeypatch.setattr(api, "_load", lambda _: result.model_copy(update={"evidence": [finding]}))
    loc = client.get(f"/api/cases/{cid}/evidence/E90/location").json()
    assert loc["boxes"] == [{"x0": 0.1, "top": 0.3, "x1": 0.5, "bottom": 0.32}]
