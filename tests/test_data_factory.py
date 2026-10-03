"""
tests/test_data_factory.py
--------------------------
Tests for gldb/data_factory.py and gldb/form_maps.py.
Does NOT call AWS. Generates PDFs into a temp directory.

Template-dependent tests are skipped when the GLDB AcroForm templates are
absent from data/templates/ (normal in CI without the real forms).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path

import pdfplumber
import pypdf
import pytest

from gldb.data_factory import (
    PRODUCER_REPORTLAB,
    PRODUCER_TAMPERED,
    INV1001_SUBTOTAL, INV1001_GST, INV1001_TOTAL, INV1001_ITEMS,
    INV1002_SUBTOTAL_CLEAN, INV1002_GST_CLEAN, INV1002_TOTAL_CLEAN, INV1002_ITEMS_CLEAN,
    INV1002_SUBTOTAL_TAMPERED, INV1002_GST_TAMPERED, INV1002_TOTAL_TAMPERED, INV1002_ITEMS_TAMPERED,
    BS_MONTHS, BS_JUN_CLOSING_TAMPERED,
    FS_REVENUE, FS_TOTAL_ASSETS, FS_TOTAL_LIABILITIES, FS_TOTAL_EQUITY,
    APPLICATION_MANIFEST,
    ENTITY_FORM_VALUES_CLEAN,
    generate_packs,
    _read_metadata,
    ENTITY_TEMPLATE, INDIVIDUAL_TEMPLATE,
)
from gldb.form_maps import (
    ENTITY_FIELD_MAP,
    INDIVIDUAL_FIELD_MAP,
    all_pdf_field_names,
)
from gldb.schemas import ApplicationManifest

_SGT = timezone(timedelta(hours=8))
GST_RATE = Decimal("0.09")

# ---------------------------------------------------------------------------
# Template availability guard
# ---------------------------------------------------------------------------

_TEMPLATES_AVAILABLE = ENTITY_TEMPLATE.exists() and INDIVIDUAL_TEMPLATE.exists()
needs_templates = pytest.mark.skipif(
    not _TEMPLATES_AVAILABLE,
    reason="GLDB AcroForm templates not present in data/templates/",
)

# ---------------------------------------------------------------------------
# Fixture: generate all three packs once per session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    if not _TEMPLATES_AVAILABLE:
        pytest.skip("GLDB AcroForm templates not present")
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base

# ---------------------------------------------------------------------------
# Arithmetic correctness
# ---------------------------------------------------------------------------

def test_inv1001_line_item_arithmetic():
    for desc, qty, unit_price, amount in INV1001_ITEMS:
        assert unit_price * qty == amount, f"INV-1001 '{desc}': {qty}*{unit_price} != {amount}"

def test_inv1001_totals():
    items_sum = sum(amt for _, _, _, amt in INV1001_ITEMS)
    assert items_sum == INV1001_SUBTOTAL
    assert (INV1001_SUBTOTAL * GST_RATE).quantize(Decimal("0.01")) == INV1001_GST
    assert INV1001_SUBTOTAL + INV1001_GST == INV1001_TOTAL

def test_inv1002_clean_line_item_arithmetic():
    for desc, qty, unit_price, amount in INV1002_ITEMS_CLEAN:
        assert unit_price * qty == amount

def test_inv1002_clean_totals():
    items_sum = sum(amt for _, _, _, amt in INV1002_ITEMS_CLEAN)
    assert items_sum == INV1002_SUBTOTAL_CLEAN
    assert (INV1002_SUBTOTAL_CLEAN * GST_RATE).quantize(Decimal("0.01")) == INV1002_GST_CLEAN
    assert INV1002_SUBTOTAL_CLEAN + INV1002_GST_CLEAN == INV1002_TOTAL_CLEAN

def test_inv1002_tampered_line_item_arithmetic():
    for desc, qty, unit_price, amount in INV1002_ITEMS_TAMPERED:
        assert unit_price * qty == amount

def test_inv1002_tampered_totals():
    items_sum = sum(amt for _, _, _, amt in INV1002_ITEMS_TAMPERED)
    assert items_sum == INV1002_SUBTOTAL_TAMPERED
    assert (INV1002_SUBTOTAL_TAMPERED * GST_RATE).quantize(Decimal("0.01")) == INV1002_GST_TAMPERED
    assert INV1002_SUBTOTAL_TAMPERED + INV1002_GST_TAMPERED == INV1002_TOTAL_TAMPERED

def test_bank_running_balances_clean():
    """Every month's running balance reconciles, and months chain correctly."""
    prev_closing: Decimal | None = None
    for bs in BS_MONTHS:
        year, month = bs["ym"]
        opening = bs["opening"]
        if prev_closing is not None:
            assert opening == prev_closing, (
                f"{year}-{month:02d}: opening {opening} != previous closing {prev_closing}"
            )
        balance = opening
        for day, desc, debit, credit, stated in bs["rows"]:
            if debit is not None:
                balance -= debit
            if credit is not None:
                balance += credit
            assert balance == stated, (
                f"{year}-{month:02d} row '{desc}': computed {balance} != stated {stated}"
            )
        assert balance == bs["closing"], (
            f"{year}-{month:02d}: final balance {balance} != closing {bs['closing']}"
        )
        prev_closing = bs["closing"]

def test_bank_jun_tampered_closing_differs():
    jun = next(b for b in BS_MONTHS if b["ym"] == (2026, 6))
    assert BS_JUN_CLOSING_TAMPERED != jun["closing"]

# ---------------------------------------------------------------------------
# Application manifest schema validation
# ---------------------------------------------------------------------------

def test_application_manifest_validates():
    m = ApplicationManifest.model_validate(APPLICATION_MANIFEST)
    assert m.uen == "202301234K"
    assert len(m.guarantors) == 2

# ---------------------------------------------------------------------------
# File presence
# ---------------------------------------------------------------------------

BASE_FILES = {
    "invoice_INV-1001.pdf",
    "invoice_INV-1002.pdf",
    "fs_summary.pdf",
    "fatca_crs_entity.pdf",
    "purchase_order_PO-2001.pdf",
    "purchase_order_PO-2002.pdf",
    "bill_of_lading_BL-3001.pdf",
    "bill_of_lading_BL-3002.pdf",
    "application.json",
    "ground_truth.json",
}

def _bank_files(skip_month: int | None = None) -> set[str]:
    return {
        f"bank_statement_{y}-{m:02d}.pdf"
        for y, m in [b["ym"] for b in BS_MONTHS]
        if m != skip_month
    }

@needs_templates
def test_clean_pack_files(packs):
    names = {p.name for p in (packs / "clean").iterdir()}
    assert names == BASE_FILES | _bank_files()

@needs_templates
def test_tampered_pack_files(packs):
    names = {p.name for p in (packs / "tampered").iterdir()}
    assert names == BASE_FILES | _bank_files()

@needs_templates
def test_incomplete_pack_files(packs):
    names = {p.name for p in (packs / "incomplete").iterdir()}
    assert names == BASE_FILES | _bank_files(skip_month=4)

@needs_templates
def test_clean_has_6_bank_statements(packs):
    stmts = [p for p in (packs / "clean").iterdir() if p.name.startswith("bank_statement_")]
    assert len(stmts) == 6

@needs_templates
def test_tampered_has_6_bank_statements(packs):
    stmts = [p for p in (packs / "tampered").iterdir() if p.name.startswith("bank_statement_")]
    assert len(stmts) == 6

@needs_templates
def test_incomplete_has_5_bank_statements(packs):
    stmts = [p for p in (packs / "incomplete").iterdir() if p.name.startswith("bank_statement_")]
    assert len(stmts) == 5

# ---------------------------------------------------------------------------
# PDF titles
# ---------------------------------------------------------------------------

def _first_line(path: Path) -> str:
    with pdfplumber.open(str(path)) as pdf:
        text = pdf.pages[0].extract_text() or ""
    return text.splitlines()[0].strip()

INVOICE_TITLES = {
    "invoice_INV-1001.pdf": "TAX INVOICE",
    "invoice_INV-1002.pdf": "TAX INVOICE",
    "bank_statement_2026-06.pdf": "STATEMENT OF ACCOUNT",
    "fs_summary.pdf": "FINANCIAL STATEMENTS SUMMARY",
}

@pytest.mark.parametrize("fname,title", INVOICE_TITLES.items())
@needs_templates
def test_clean_pdf_title(packs, fname, title):
    assert _first_line(packs / "clean" / fname) == title

@pytest.mark.parametrize("fname,title", INVOICE_TITLES.items())
@needs_templates
def test_tampered_pdf_title(packs, fname, title):
    assert _first_line(packs / "tampered" / fname) == title

# ---------------------------------------------------------------------------
# Metadata: clean pack
# ---------------------------------------------------------------------------

@needs_templates
def test_clean_invoice_producer(packs):
    for fname in ("invoice_INV-1001.pdf", "invoice_INV-1002.pdf"):
        meta = _read_metadata(packs / "clean" / fname)
        assert meta.get("/Producer") == PRODUCER_REPORTLAB

@needs_templates
def test_clean_invoice_creation_eq_mod(packs):
    for fname in ("invoice_INV-1001.pdf", "invoice_INV-1002.pdf"):
        meta = _read_metadata(packs / "clean" / fname)
        assert meta.get("/CreationDate") == meta.get("/ModDate"), fname

@needs_templates
def test_clean_bank_statements_metadata(packs):
    for bs in BS_MONTHS:
        year, month = bs["ym"]
        fname = f"bank_statement_{year}-{month:02d}.pdf"
        meta = _read_metadata(packs / "clean" / fname)
        assert meta.get("/Producer") == PRODUCER_REPORTLAB, fname
        assert meta.get("/CreationDate") == meta.get("/ModDate"), fname

# ---------------------------------------------------------------------------
# Metadata: tampered INV-1002
# ---------------------------------------------------------------------------

@needs_templates
def test_tampered_inv1002_producer(packs):
    meta = _read_metadata(packs / "tampered" / "invoice_INV-1002.pdf")
    assert meta.get("/Producer") == PRODUCER_TAMPERED

@needs_templates
def test_tampered_inv1002_creation_date(packs):
    meta = _read_metadata(packs / "tampered" / "invoice_INV-1002.pdf")
    assert meta.get("/CreationDate", "").startswith("D:20260705")

@needs_templates
def test_tampered_inv1002_mod_date(packs):
    meta = _read_metadata(packs / "tampered" / "invoice_INV-1002.pdf")
    assert meta.get("/ModDate", "").startswith("D:20260726")

@needs_templates
def test_tampered_inv1002_mod_after_creation(packs):
    meta = _read_metadata(packs / "tampered" / "invoice_INV-1002.pdf")
    assert meta.get("/ModDate", "") > meta.get("/CreationDate", "")

# ---------------------------------------------------------------------------
# ground_truth.json
# ---------------------------------------------------------------------------

@needs_templates
def test_clean_ground_truth(packs):
    gt = json.loads((packs / "clean" / "ground_truth.json").read_text())
    assert gt["pack"] == "clean"
    assert gt["expected_band"] == "Low"
    assert gt["expected_readiness"] == "Complete"
    assert gt["expected_check_ids"] == []
    assert gt["expected_compliance_check_ids"] == []
    assert gt["tampers"] == []

@needs_templates
def test_tampered_ground_truth(packs):
    gt = json.loads((packs / "tampered" / "ground_truth.json").read_text())
    assert gt["pack"] == "tampered"
    assert gt["expected_band"] == "High"
    assert gt["expected_readiness"] == "Complete"
    expected_ids = {
        "XDOC_INVOICE_BANK", "XDOC_IDENTITY", "META_MOD_AFTER_CREATE",
        "META_PRODUCER_MISMATCH", "BANK_RUNNING_BAL", "BANK_CONTINUITY",
        "FORENSIC_HIDDEN_TEXT", "XDOC_PO_INVOICE", "XDOC_BL_INVOICE",
    }
    assert set(gt["expected_check_ids"]) == expected_ids
    assert gt["expected_compliance_check_ids"] == []
    tamper_types = {t["type"] for t in gt["tampers"]}
    assert tamper_types == {
        "amount_inflated", "uen_mismatch", "metadata_edited",
        "closing_balance_altered", "prompt_injection", "figure_pasted_as_image",
        "over_invoiced_vs_shipment",
    }
    # New tamper references bank_statement_2026-06, not bank_statement
    docs = {t["doc"] for t in gt["tampers"] if t["type"] == "closing_balance_altered"}
    assert docs == {"bank_statement_2026-06"}

@needs_templates
def test_incomplete_ground_truth(packs):
    gt = json.loads((packs / "incomplete" / "ground_truth.json").read_text())
    assert gt["pack"] == "incomplete"
    assert gt["expected_band"] == "Low"
    assert gt["expected_readiness"] == "Incomplete"
    assert gt["expected_check_ids"] == []
    expected_compliance = {
        "BANK_COVERAGE", "FORM_ENT_US_STATUS",
        "FORM_ENT_TIN_OR_REASON", "FORM_ENT_TYPE_SELECTION",
    }
    assert set(gt["expected_compliance_check_ids"]) == expected_compliance

# ---------------------------------------------------------------------------
# application.json validates as ApplicationManifest
# ---------------------------------------------------------------------------

@needs_templates
def test_clean_application_json(packs):
    raw = json.loads((packs / "clean" / "application.json").read_text())
    m = ApplicationManifest.model_validate(raw)
    assert m.uen == "202301234K"
    assert len(m.guarantors) == 2

# ---------------------------------------------------------------------------
# Entity form field values round-trip
# ---------------------------------------------------------------------------

@needs_templates
def test_entity_form_clean_values(packs):
    """Reading back the filled entity form returns the expected values."""
    reader = pypdf.PdfReader(str(packs / "clean" / "fatca_crs_entity.pdf"))
    fields = reader.get_fields() or {}
    for field_name, expected_val in ENTITY_FORM_VALUES_CLEAN.items():
        actual = fields.get(field_name)
        if actual is None:
            continue  # field may be on a different page
        # pypdf returns field value as string or pypdf object
        actual_str = str(actual.get("/V", "")) if hasattr(actual, "get") else str(actual)
        assert actual_str == expected_val, (
            f"Field {field_name!r}: expected {expected_val!r}, got {actual_str!r}"
        )

@needs_templates
def test_entity_form_static_text_unchanged(packs):
    """Static page text should be identical between template and filled form."""
    with pdfplumber.open(str(ENTITY_TEMPLATE)) as tpl:
        tpl_texts = [p.extract_text() or "" for p in tpl.pages]
    with pdfplumber.open(str(packs / "clean" / "fatca_crs_entity.pdf")) as filled:
        filled_texts = [p.extract_text() or "" for p in filled.pages]
    assert len(tpl_texts) == len(filled_texts)
    for i, (t, f) in enumerate(zip(tpl_texts, filled_texts)):
        assert t == f, f"Page {i+1} static text changed after fill_form"
