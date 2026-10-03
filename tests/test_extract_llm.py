"""
tests/test_extract_llm.py
-------------------------
Tests for gldb/extract_llm.py, gldb/extract.py and gldb/ocr.py.
No AWS calls: converse() and the Textract client are faked.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from gldb import extract, extract_llm, extract_stub
from gldb.extract_stub import parse_pack as stub_parse_pack
from gldb.intake import intake, load_manifest
from gldb.llm_client import LLMResult
from gldb.ocr import ocr_files

CLEAN = Path(__file__).parent.parent / "data" / "packs" / "clean"


@pytest.fixture(scope="module")
def clean_inputs() -> tuple[list[Path], list[Any]]:
    paths = sorted(CLEAN.glob("*.pdf"))
    return paths, intake("t", paths)


def test_schema_drops_pipeline_fields_and_uses_string_money() -> None:
    schema = extract_llm._schema(extract_llm.Invoice)
    assert "doc_id" not in schema["properties"]
    assert "page" not in schema["properties"]
    assert "doc_id" not in schema["required"]
    assert schema["properties"]["total"]["type"] == "string"
    assert "anyOf" not in schema["properties"]["total"]
    assert schema["$defs"]["LineItem"]["properties"]["amount"]["type"] == "string"


def test_llm_pack_matches_stub_pack(
    monkeypatch: pytest.MonkeyPatch, clean_inputs: tuple[list[Path], list[Any]]
) -> None:
    paths, docs = clean_inputs
    manifest = load_manifest(CLEAN)
    stub_pack = stub_parse_pack("t", docs, paths, manifest)
    models = {
        m.doc_id: m
        for m in [*stub_pack.invoices, *stub_pack.bank_statements,
                  *stub_pack.financial_statements, *stub_pack.purchase_orders]
    }
    calls: list[dict[str, Any]] = []
    queue = [d for d in docs if d.doc_id in models]

    def fake(system: str, user: str, *, purpose: str, schema: dict[str, Any]) -> LLMResult:
        calls.append({"system": system, "user": user, "purpose": purpose})
        model = models[queue[len(calls) - 1].doc_id]
        data = json.loads(model.model_dump_json(exclude={"doc_id", "page"}))
        return LLMResult(text="", data=data, input_tokens=1, output_tokens=1)

    monkeypatch.setattr(extract_llm, "converse", fake)
    pack = extract_llm.parse_pack("t", docs, paths, manifest)

    assert pack.extraction_mode == "llm"
    assert pack.invoices == stub_pack.invoices
    assert pack.bank_statements == stub_pack.bank_statements
    assert pack.financial_statements == stub_pack.financial_statements
    assert pack.purchase_orders == stub_pack.purchase_orders != []
    assert len(pack.entity_self_certs) == 1
    # Document text is wrapped as data; no form contents reach the LLM.
    assert all(c["user"].startswith("<document>") for c in calls)
    assert all("FATCA" not in c["purpose"] for c in calls)


def test_unverifiable_quotes_are_dropped(
    monkeypatch: pytest.MonkeyPatch, clean_inputs: tuple[list[Path], list[Any]]
) -> None:
    paths, docs = clean_inputs
    inv_doc = next(d for d in docs if d.doc_type == "invoice")
    stub_inv = stub_parse_pack("t", docs, paths, None).invoices[0]
    data = json.loads(stub_inv.model_dump_json(exclude={"doc_id", "page"}))
    data["source_quotes"] = {**stub_inv.source_quotes, "Total": "Total: 999,999.00"}
    monkeypatch.setattr(
        extract_llm, "converse",
        lambda *a, **k: LLMResult(text="", data=data, input_tokens=1, output_tokens=1),
    )
    inv = extract_llm._extract_doc(inv_doc, extract_llm.read_text(CLEAN / inv_doc.filename))
    assert "Total" not in inv.source_quotes
    assert inv.source_quotes["Subtotal"] == stub_inv.source_quotes["Subtotal"]


def test_invalid_llm_output_raises_extraction_error(
    monkeypatch: pytest.MonkeyPatch, clean_inputs: tuple[list[Path], list[Any]]
) -> None:
    _, docs = clean_inputs
    inv_doc = next(d for d in docs if d.doc_type == "invoice")
    monkeypatch.setattr(
        extract_llm, "converse",
        lambda *a, **k: LLMResult(text="", data={"invoice_number": "X"}, input_tokens=1, output_tokens=1),
    )
    with pytest.raises(extract_llm.ExtractionError):
        extract_llm._extract_doc(inv_doc, ["text"])


@pytest.mark.parametrize(
    ("mode", "module"), [("llm", extract_llm), ("stub", extract_stub)]
)
def test_dispatcher_picks_extractor(
    monkeypatch: pytest.MonkeyPatch, mode: str, module: Any
) -> None:
    monkeypatch.setattr(extract, "EXTRACTION_MODE", mode)
    monkeypatch.setattr(module, "parse_pack", lambda *a: "called")
    assert extract.parse_pack("c", [], [], None) == "called"


class FakeTextract:
    def __init__(self) -> None:
        self.calls = 0

    def detect_document_text(self, Document: dict[str, bytes]) -> dict[str, Any]:
        assert Document["Bytes"].startswith(b"%PDF")
        self.calls += 1
        return {"Blocks": [
            {"BlockType": "PAGE", "Text": "ignored"},
            {"BlockType": "LINE", "Text": "TAX INVOICE", "Confidence": 99.0},
            {"BlockType": "LINE", "Text": f"call {self.calls}", "Confidence": 99.0},
        ]}


def test_ocr_files_one_call_per_page() -> None:
    pdf = next(CLEAN.glob("bank_statement_*.pdf"))
    fake = FakeTextract()
    pages = ocr_files([pdf], client=fake).text[pdf.stem]
    assert len(pages) == fake.calls >= 1
    assert pages[0].startswith("TAX INVOICE\ncall ")


def test_hidden_text_never_reaches_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    tampered = Path(__file__).parent.parent / "data" / "packs" / "tampered"
    paths = [tampered / "invoice_INV-1002.pdf"]
    docs = intake("t", paths)
    seen: list[str] = []

    def fake(system: str, user: str, **kwargs: Any) -> LLMResult:
        seen.append(user)
        raise extract_llm.ExtractionError("stop after capturing the prompt")

    monkeypatch.setattr(extract_llm, "converse", fake)
    with pytest.raises(extract_llm.ExtractionError):
        extract_llm.parse_pack("t", docs, paths, None)

    assert "Total:" in seen[0]
    assert "SYSTEM NOTE" not in seen[0] and "ignore all previous" not in seen[0]
    # The default reader still sees it: intake and classification are unchanged.
    assert "SYSTEM NOTE" in " ".join(extract_llm.read_text(paths[0]))


def test_schema_has_no_raw_number_alternatives() -> None:
    """Nullable money (debit/credit) must be string-only too, or the model may return floats."""
    schema = extract_llm._schema(extract_llm.BankStatement)
    assert '"number"' not in json.dumps(schema)


@pytest.mark.parametrize(
    ("printed", "expected"),
    [("92,000.00", "92000.00"), ("SGD 1,200.50", "1200.50"), ("$3.10", "3.10"), ("10", "10")],
)
def test_money_accepts_printed_formatting(printed: str, expected: str) -> None:
    from decimal import Decimal

    from gldb.schemas import Transaction

    txn = Transaction.model_validate(
        {"date": "2026-01-02", "description": "x", "debit": None, "credit": printed, "balance": printed}
    )
    assert txn.credit == txn.balance == Decimal(expected)


def test_dash_means_no_amount_only_for_optional_money() -> None:
    from gldb.schemas import Transaction

    txn = Transaction.model_validate(
        {"date": "2026-01-02", "description": "x", "debit": "-", "credit": "5.00", "balance": "5.00"}
    )
    assert txn.debit is None
    with pytest.raises(ValueError):
        Transaction.model_validate(
            {"date": "2026-01-02", "description": "x", "debit": None, "credit": None, "balance": "-"}
        )
