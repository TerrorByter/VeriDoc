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
    _edit_month,
    _read_metadata,
    ENTITY_TEMPLATE, INDIVIDUAL_TEMPLATE,
)
from gldb.form_maps import (
    ENTITY_FIELD_MAP,
    INDIVIDUAL_FIELD_MAP,
    all_pdf_field_names,
)
from gldb import official
from gldb.schemas import ApplicationManifest
from gldb.signal_library import BY_CHECK

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

def test_edit_month_rebalances():
    """An edited month's rows are rebalanced, later months re-chain, and the
    shared BS_MONTHS is left untouched."""
    before = [dict(bs, rows=list(bs["rows"])) for bs in BS_MONTHS]
    edited = _edit_month(BS_MONTHS, (2026, 7), [
        (3, "GIRO CR EXTRA", None, Decimal("15200.00")),
        (25, "PAYROLL JUL", Decimal("21400.00"), None),
    ])
    assert BS_MONTHS == before
    assert edited is not BS_MONTHS and edited[0] is not BS_MONTHS[0]
    prev_closing = edited[0]["opening"]
    for bs in edited:
        assert bs["opening"] == prev_closing
        balance = bs["opening"]
        for _day, _desc, debit, credit, stated in bs["rows"]:
            balance += (credit or 0) - (debit or 0)
            assert stated == balance
        assert bs["closing"] == balance
        prev_closing = bs["closing"]
    jun, jul = edited[4], edited[5]
    assert jul["opening"] == jun["closing"] == Decimal("129449.50")
    assert jul["rows"][0] == (3, "GIRO CR EXTRA", None, Decimal("15200.00"), Decimal("144649.50"))
    assert jul["closing"] == Decimal("144649.50") - Decimal("21400.00")

def test_edit_month_keeps_earlier_months_and_rechains_later_ones():
    edited = _edit_month(BS_MONTHS, (2026, 2), [(5, "OFFICE RENT FEB", Decimal("9000.00"), None)])
    assert edited[0]["closing"] == Decimal("91000.00")
    assert edited[1]["opening"] == Decimal("91000.00")
    assert edited[5]["closing"] == BS_MONTHS[5]["closing"] - Decimal("91600.00") + Decimal("91000.00")

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
    "notice_of_assessment_1.pdf",
    "notice_of_assessment_2.pdf",
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
        "REGISTRY_ENTITY_NOT_FOUND",
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
def test_missing_noa_pack_leaves_out_one_guarantors_notice(packs):
    names = {p.name for p in (packs / "missing_noa").iterdir()}
    assert names == (BASE_FILES - {"notice_of_assessment_2.pdf"}) | _bank_files()
    gt = json.loads((packs / "missing_noa" / "ground_truth.json").read_text())
    assert (gt["expected_band"], gt["expected_path"], gt["expected_readiness"]) == (
        "Low", "standard_review", "Incomplete")
    assert gt["expected_check_ids"] == [] and gt["expected_compliance_check_ids"] == ["CHECKLIST_MISSING_DOC"]

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

@needs_templates
def test_every_tamper_names_known_signals_and_an_expected_check(packs):
    for folder in packs.iterdir():
        truth = json.loads((folder / "ground_truth.json").read_text())
        expected = set(truth["expected_check_ids"]) | set(truth.get("expected_check_ids_ocr", []))
        for tamper in truth["tampers"]:
            assert tamper["check_ids"], (folder.name, tamper["type"])
            assert set(tamper["check_ids"]) <= set(BY_CHECK)
            assert set(tamper["check_ids"]) <= expected, (folder.name, tamper["type"])


@needs_templates
def test_retro_po_pack_dates_po_2002_after_its_invoice(packs):
    folder = packs / "retro_po"
    assert {p.name for p in folder.iterdir()} == BASE_FILES | _bank_files()
    text = pdfplumber.open(folder / "purchase_order_PO-2002.pdf").pages[0].extract_text()
    assert "PO Date: 2026-07-15" in text
    gt = json.loads((folder / "ground_truth.json").read_text())
    assert (gt["expected_band"], gt["expected_path"]) == ("Medium", "standard_review")
    assert gt["expected_check_ids"] == ["TEMP_TRADE_SEQUENCE"]


BEYOND_RULES_PACKS = ["third_party_payment", "related_party", "round_trip", "innocent_split"]
EXPECTED_AI_TRUTH = {
    "third_party_payment": (
        [{"agent": "transaction", "pattern": "third_party_payment"}], []),
    "related_party": (
        [{"agent": "kyc", "pattern": "related_party"}], []),
    "round_trip": (
        [{"agent": "transaction", "pattern": "round_trip"}], []),
    "innocent_split": (
        [{"agent": "transaction", "pattern": "split_payment"}],
        [{"check_id": "XDOC_INVOICE_BANK", "status": "refuted"}]),
}


@needs_templates
@pytest.mark.parametrize("name", BEYOND_RULES_PACKS)
def test_beyond_rules_pack_files_and_ground_truth(packs, name):
    folder = packs / name
    assert {p.name for p in folder.iterdir()} == BASE_FILES | _bank_files()
    gt = json.loads((folder / "ground_truth.json").read_text())
    assert gt["pack"] == name
    assert "requires" not in gt
    leads, triage = EXPECTED_AI_TRUTH[name]
    assert gt["expected_leads"] == leads
    assert gt["expected_triage"] == triage
    assert gt["expected_readiness"] == "Complete"


@needs_templates
def test_only_the_innocent_split_pack_expects_a_rule_finding(packs):
    for name in BEYOND_RULES_PACKS:
        gt = json.loads((packs / name / "ground_truth.json").read_text())
        silent = name != "innocent_split"
        assert (gt["expected_check_ids"] == []) is silent
        assert (gt["expected_band"], gt["expected_path"]) == (
            ("Low", "fast_track") if silent else ("Medium", "standard_review")
        )
        assert gt["tampers"] == []


@needs_templates
def test_every_pack_declares_expected_leads_and_triage(packs):
    for folder in packs.iterdir():
        truth = json.loads((folder / "ground_truth.json").read_text())
        assert "expected_leads" in truth and "expected_triage" in truth, folder.name
        if folder.name not in BEYOND_RULES_PACKS:
            assert truth["expected_leads"] == truth["expected_triage"] == [], folder.name


@needs_templates
def test_beyond_rules_pack_documents(packs):
    def text(pack: str, fname: str) -> str:
        with pdfplumber.open(packs / pack / fname) as pdf:
            return "\n".join(page.extract_text() or "" for page in pdf.pages)

    assert "GIRO CR HARBOURLINE LOGISTICS INV-1002" in text("third_party_payment", "bank_statement_2026-07.pdf")
    for fname in ("invoice_INV-1002.pdf", "purchase_order_PO-2002.pdf", "bill_of_lading_BL-3002.pdf"):
        assert "Seabright Provisions Pte. Ltd." in text("related_party", fname)
    assert "SEABRIGHT PROVISIONS INV-1002" in text("related_party", "bank_statement_2026-07.pdf")
    assert "SUPPLIER PAYMENT HARBOURLINE LOGISTICS" in text("round_trip", "bank_statement_2026-06.pdf")
    assert "GIRO CR CUSTOMER RECEIPTS HARBOURLINE LOGISTICS" in text("round_trip", "bank_statement_2026-07.pdf")
    july = text("innocent_split", "bank_statement_2026-07.pdf")
    assert "INV-1002 PART 1" in july and "INV-1002 PART 2" in july


def test_the_related_party_counterparty_is_registered_with_acra():
    entity = official.company("202205551D")
    assert entity is not None
    assert entity.company_name == "Seabright Provisions Pte. Ltd."
    assert entity.status == "LIVE COMPANY"
    assert entity.incorporation_date == date(2022, 2, 14)
    assert [(o.name, o.position) for o in entity.officers] == [("Daniel Brooks", "DIRECTOR")]


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
