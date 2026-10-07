"""
tests/test_intake_extract.py
----------------------------
Intake and extraction stage tests.
Part 1 covers: validate_file, sha256_file, parse_pdf_date, classify,
intake(), and load_manifest.

Parts 2-6 tests (extract_stub, extract_llm, checks, scoring, pipeline)
will be added below as implementation proceeds.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from gldb.data_factory import ENTITY_TEMPLATE, INDIVIDUAL_TEMPLATE, generate_packs
from gldb.intake import (
    IntakeError,
    classify,
    intake,
    load_manifest,
    parse_pdf_date,
    read_text,
    sha256_file,
    validate_file,
)
from gldb.schemas import DocType

# ---------------------------------------------------------------------------
# Template availability guard
# ---------------------------------------------------------------------------

_TEMPLATES_AVAILABLE = ENTITY_TEMPLATE.exists() and INDIVIDUAL_TEMPLATE.exists()
needs_templates = pytest.mark.skipif(
    not _TEMPLATES_AVAILABLE,
    reason="GLDB AcroForm templates not present in data/templates/",
)


# ---------------------------------------------------------------------------
# Module-scoped fixture: generate packs once per test session
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    if not _TEMPLATES_AVAILABLE:
        pytest.skip("GLDB AcroForm templates not present")
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base


# ---------------------------------------------------------------------------
# Part 1 — validate_file
# ---------------------------------------------------------------------------


def test_validate_file_rejects_txt(tmp_path):
    """A .txt file (even with valid PDF bytes) must be rejected."""
    f = tmp_path / "document.txt"
    f.write_bytes(b"%PDF-1.4\n%EOF")
    with pytest.raises(IntakeError, match=r"\.txt"):
        validate_file(f)


def test_validate_file_rejects_fake_pdf(tmp_path):
    """A .pdf file whose content does not start with %PDF must be rejected."""
    f = tmp_path / "fake.pdf"
    f.write_bytes(b"NOTPDF-content-here")
    with pytest.raises(IntakeError):
        validate_file(f)


def test_validate_file_accepts_minimal_pdf(tmp_path):
    """A .pdf file starting with %PDF bytes should pass (no size issue)."""
    f = tmp_path / "minimal.pdf"
    f.write_bytes(b"%PDF-1.4\n%EOF")
    # Should not raise
    validate_file(f)


# ---------------------------------------------------------------------------
# Part 1 — parse_pdf_date
# ---------------------------------------------------------------------------


def test_parse_pdf_date_valid():
    """Standard PDF date with +08'00' offset parses correctly."""
    dt = parse_pdf_date("D:20260705100000+08'00'")
    assert dt is not None
    assert dt.year == 2026
    assert dt.month == 7
    assert dt.day == 5
    assert dt.hour == 10
    # timezone offset must be +08:00
    assert dt.tzinfo is not None
    assert dt.utcoffset() == timedelta(hours=8)


def test_parse_pdf_date_utc_z():
    """Z suffix is parsed as UTC."""
    dt = parse_pdf_date("D:20260101120000Z")
    assert dt is not None
    assert dt.tzinfo == timezone.utc
    assert dt.hour == 12


def test_parse_pdf_date_negative_offset():
    """Negative UTC offset is parsed correctly."""
    dt = parse_pdf_date("D:20260101120000-05'00'")
    assert dt is not None
    assert dt.utcoffset() == timedelta(hours=-5)


def test_parse_pdf_date_no_tz():
    """Date string with no timezone info is treated as UTC and does not raise."""
    dt = parse_pdf_date("D:20260101120000")
    # Should return a timezone-aware datetime (UTC)
    assert dt is not None
    assert dt.tzinfo is not None


def test_parse_pdf_date_garbage():
    """`garbage` input returns None without raising."""
    assert parse_pdf_date("garbage") is None


def test_parse_pdf_date_none():
    """`None` input returns None without raising."""
    assert parse_pdf_date(None) is None


def test_parse_pdf_date_empty_string():
    """Empty string returns None without raising."""
    assert parse_pdf_date("") is None


# ---------------------------------------------------------------------------
# Part 1 — sha256_file
# ---------------------------------------------------------------------------


def test_sha256_length(tmp_path):
    """sha256_file returns a 64-character hex string."""
    f = tmp_path / "minimal.pdf"
    f.write_bytes(b"%PDF-1.4\n%EOF")
    digest = sha256_file(f)
    assert isinstance(digest, str)
    assert len(digest) == 64
    # Must be valid hex
    int(digest, 16)


def test_sha256_correct_value(tmp_path):
    """sha256_file returns the correct digest for known content."""
    content = b"%PDF-1.4\n%EOF"
    f = tmp_path / "known.pdf"
    f.write_bytes(content)
    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(f) == expected


# ---------------------------------------------------------------------------
# Part 1 — classify (pack-dependent)
# ---------------------------------------------------------------------------


@needs_templates
def test_classify_clean_pack(packs):
    """Every document type in the clean pack is classified correctly."""
    clean = packs / "clean"

    # Invoices
    for fname in ("invoice_INV-1001.pdf", "invoice_INV-1002.pdf"):
        doc_type = classify(read_text(clean / fname))
        assert doc_type == "invoice", f"{fname} → {doc_type}"

    # Bank statements (all 6 months Feb–Jul)
    for month in range(2, 8):
        fname = f"bank_statement_2026-{month:02d}.pdf"
        doc_type = classify(read_text(clean / fname))
        assert doc_type == "bank_statement", f"{fname} → {doc_type}"

    # Financial statements summary
    doc_type = classify(read_text(clean / "fs_summary.pdf"))
    assert doc_type == "financial_statements"

    # FATCA/CRS entity form
    doc_type = classify(read_text(clean / "fatca_crs_entity.pdf"))
    assert doc_type == "fatca_crs_entity"

    # A Notice of Assessment for each guarantor
    for fname in ("notice_of_assessment_1.pdf", "notice_of_assessment_2.pdf"):
        assert classify(read_text(clean / fname)) == "notice_of_assessment", fname


# ---------------------------------------------------------------------------
# Part 1 — sha256 (pack-dependent)
# ---------------------------------------------------------------------------


@needs_templates
def test_sha256_64_chars_pack(packs):
    """sha256_file on a generated pack PDF returns a 64-char hex string."""
    digest = sha256_file(packs / "clean" / "invoice_INV-1001.pdf")
    assert isinstance(digest, str)
    assert len(digest) == 64
    int(digest, 16)  # valid hex


# ---------------------------------------------------------------------------
# Part 1 — tampered metadata (pack-dependent)
# ---------------------------------------------------------------------------


@needs_templates
def test_tampered_inv1002_metadata(packs):
    """Tampered INV-1002 has the edited producer and mod > creation by >24 h."""
    import pypdf

    reader = pypdf.PdfReader(str(packs / "tampered" / "invoice_INV-1002.pdf"))
    meta = reader.metadata or {}

    producer = str(meta.get("/Producer", ""))
    assert producer == "QuickPDF Editor 9.2", f"Producer: {producer!r}"

    creation_dt = parse_pdf_date(str(meta.get("/CreationDate", "")) or None)
    mod_dt = parse_pdf_date(str(meta.get("/ModDate", "")) or None)

    assert creation_dt is not None, "CreationDate missing or unparseable"
    assert mod_dt is not None, "ModDate missing or unparseable"
    assert mod_dt - creation_dt > timedelta(hours=24), (
        f"mod_date - creation_date = {mod_dt - creation_dt} (expected > 24h)"
    )


# ---------------------------------------------------------------------------
# Part 1 — load_manifest (pack-dependent)
# ---------------------------------------------------------------------------


@needs_templates
def test_load_manifest_clean(packs):
    """load_manifest parses application.json from the clean pack correctly."""
    manifest = load_manifest(packs / "clean")
    assert manifest is not None
    assert manifest.uen == "202301234K"
    assert manifest.company_name == "Lumen Harbour Trading Pte. Ltd."
    assert len(manifest.guarantors) == 2


@needs_templates
def test_load_manifest_missing_returns_none(tmp_path):
    """load_manifest returns None when application.json is absent."""
    assert load_manifest(tmp_path) is None


# ---------------------------------------------------------------------------
# TODO: Part 2 — extract_stub tests
# TODO: Part 3 — extract_llm tests (skip unless RUN_LIVE=1)
# TODO: Part 4 — checks tests
# TODO: Part 5 — scoring tests
# TODO: Part 6 — pipeline integration tests
# ---------------------------------------------------------------------------
