"""
tests/test_checks.py
--------------------
Tests for gldb/checks.py — all 24 checks and the runner.

Pack fixture strategy:
- Module-scoped packs fixture generates clean / tampered / incomplete once.
- pack_from_folder (pipeline.py) turns a folder into an ExtractedPack.
- Hand-built EntitySelfCert / ApplicationManifest objects are used for
  unit-testing form checks (C and D groups).
- No AWS calls are made at any point.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gldb.checks import (
    AGENTS,
    normalise_name,
    run_agents,
    check_inv_arith,
    check_bank_running_bal,
    check_bank_continuity,
    check_fs_balance,
    check_xdoc_identity,
    check_xdoc_invoice_bank,
    check_meta_mod_after_create,
    check_meta_producer_mismatch,
    check_bank_coverage,
    check_checklist_missing_doc,
    check_form_ent_mandatory,
    check_form_ent_mailing,
    check_form_ent_branch,
    check_form_ent_us_status,
    check_form_ent_tin_or_reason,
    check_form_ent_type_selection,
    check_form_ent_declaration,
    check_form_template_match,
    check_form_ent_name_match,
    check_form_ent_address_match,
    check_form_ent_incorporation,
    check_form_ent_sg_tin,
    check_form_ent_start_up,
    check_form_ent_signatory,
)
from gldb.config import money
from gldb.data_factory import ENTITY_TEMPLATE, INDIVIDUAL_TEMPLATE, generate_packs
from gldb.pipeline import pack_from_folder
from gldb.schemas import (
    Address,
    ApplicationManifest,
    BankStatement,
    ControllingPerson,
    DocMeta,
    EntitySelfCert,
    Evidence,
    ExtractedPack,
    FinancialStatementSummary,
    Guarantor,
    Invoice,
    LineItem,
    Signatory,
    TaxResidencyRow,
    Transaction,
)

# ---------------------------------------------------------------------------
# Template / pack availability guard
# ---------------------------------------------------------------------------

_TEMPLATES_AVAILABLE = ENTITY_TEMPLATE.exists() and INDIVIDUAL_TEMPLATE.exists()
needs_templates = pytest.mark.skipif(
    not _TEMPLATES_AVAILABLE,
    reason="GLDB AcroForm templates not present in data/templates/",
)


@pytest.fixture(scope="module")
def packs(tmp_path_factory):
    if not _TEMPLATES_AVAILABLE:
        pytest.skip("GLDB AcroForm templates not present")
    base = tmp_path_factory.mktemp("packs")
    generate_packs(base)
    return base


@pytest.fixture(scope="module")
def clean_pack(packs):
    return pack_from_folder(packs / "clean")


@pytest.fixture(scope="module")
def tampered_pack(packs):
    return pack_from_folder(packs / "tampered")


@pytest.fixture(scope="module")
def incomplete_pack(packs):
    return pack_from_folder(packs / "incomplete")


@pytest.fixture(scope="module")
def clean_truth(packs):
    return json.loads((packs / "clean" / "ground_truth.json").read_text())


@pytest.fixture(scope="module")
def tampered_truth(packs):
    return json.loads((packs / "tampered" / "ground_truth.json").read_text())


@pytest.fixture(scope="module")
def incomplete_truth(packs):
    return json.loads((packs / "incomplete" / "ground_truth.json").read_text())


# ---------------------------------------------------------------------------
# Shared manifest / address helpers
# ---------------------------------------------------------------------------

def _make_address(**kwargs) -> Address:
    defaults = dict(
        block="12",
        street="Harbour Link Road",
        floor="08",
        unit="03",
        building="Lumen Harbour Centre",
        city_state="Singapore",
        postal_code="098765",
        country="Singapore",
    )
    defaults.update(kwargs)
    return Address(**defaults)


def _make_manifest(**kwargs) -> ApplicationManifest:
    defaults = dict(
        application_id="APP-2026-0001",
        application_date=date(2026, 8, 10),
        company_name="Lumen Harbour Trading Pte. Ltd.",
        uen="202301234K",
        country_of_incorporation="Singapore",
        date_of_incorporation=date(2023, 1, 16),
        registered_address=_make_address(),
        authorised_signatories=["Tan Wei Ming"],
        guarantors=[
            Guarantor(
                name="Tan Wei Ming",
                id_number="S1234567D",
                nationality="Singapore",
                residential_address=_make_address(),
            )
        ],
    )
    defaults.update(kwargs)
    return ApplicationManifest(**defaults)


def _make_cert(**kwargs) -> EntitySelfCert:
    """Build a fully valid EntitySelfCert by default; override any field."""
    defaults: dict[str, Any] = dict(
        doc_id="fatca_crs_entity",
        template_id="gldb_fatca_crs_entity_2025-11",
        legal_name="Lumen Harbour Trading Pte. Ltd.",
        country_of_incorporation="Singapore",
        registered_address=_make_address(),
        mailing_different=False,
        mailing_address=Address(),
        mailing_explanation=None,
        branch_ticked=["No"],
        hq_jurisdiction=None,
        us_person_ticked=["No"],
        us_tin=None,
        tax_rows=[
            TaxResidencyRow(
                row=1,
                country="Singapore",
                tin="202301234K",
                reasons_ticked=[],
                explanation=None,
            )
        ],
        entity_types_ticked=["G"],
        giin_parts=[None, None, None],
        controlling_persons=[],
        signatories=[
            Signatory(
                slot=1,
                name="Tan Wei Ming",
                designation="Director",
                date_raw="01/08/26",
                date=date(2026, 8, 1),
                signature_field_signed=False,
            )
        ],
        raw_fields={},
    )
    defaults.update(kwargs)
    return EntitySelfCert(**defaults)


def _make_pack(
    *,
    invoices=None,
    bank_statements=None,
    financial_statements=None,
    entity_self_certs=None,
    manifest=None,
    docs=None,
) -> ExtractedPack:
    return ExtractedPack(
        case_id="test",
        docs=docs or [],
        invoices=invoices or [],
        bank_statements=bank_statements or [],
        financial_statements=financial_statements or [],
        entity_self_certs=entity_self_certs or [],
        manifest=manifest,
        extraction_mode="stub",
    )


def _make_invoice(
    doc_id="invoice_INV-0001",
    *,
    items=None,
    subtotal=None,
    gst=None,
    total=None,
    seller_uen="202301234K",
    seller_name="Lumen Harbour Trading Pte. Ltd.",
    invoice_date=date(2026, 6, 10),
) -> Invoice:
    if items is None:
        items = [LineItem(description="Widget", quantity=10, unit_price=money("100.00"), amount=money("1000.00"))]
    if subtotal is None:
        subtotal = money(sum(i.amount for i in items))
    if gst is None:
        gst = money(subtotal * Decimal("0.09"))
    if total is None:
        total = money(subtotal + gst)
    return Invoice(
        doc_id=doc_id,
        invoice_number=doc_id,
        invoice_date=invoice_date,
        seller_name=seller_name,
        seller_uen=seller_uen,
        buyer_name="Buyer Co",
        line_items=items,
        subtotal=subtotal,
        gst=gst,
        total=total,
        page=1,
        source_quotes={},
    )


def _make_bank_stmt(
    doc_id="bank_statement_2026-07",
    *,
    opening=money("100000.00"),
    closing=None,
    transactions=None,
    period_start=date(2026, 7, 1),
    period_end=date(2026, 7, 31),
    account_holder="Lumen Harbour Trading Pte. Ltd.",
) -> BankStatement:
    if transactions is None:
        transactions = [
            Transaction(date=date(2026, 7, 5), description="credit", debit=None, credit=money("5000.00"), balance=money("105000.00")),
        ]
    if closing is None:
        closing = money("105000.00")
    return BankStatement(
        doc_id=doc_id,
        bank_name="Harbourfront Bank",
        account_holder=account_holder,
        period_start=period_start,
        period_end=period_end,
        opening_balance=opening,
        closing_balance=closing,
        transactions=transactions,
        page=1,
        source_quotes={},
    )


def _make_fs(
    doc_id="fs_summary",
    *,
    company_name="Lumen Harbour Trading Pte. Ltd.",
    uen="202301234K",
    total_assets=money("2400000.00"),
    total_liabilities=money("1350000.00"),
    total_equity=money("1050000.00"),
    revenue=money("1850000.00"),
) -> FinancialStatementSummary:
    return FinancialStatementSummary(
        doc_id=doc_id,
        company_name=company_name,
        uen=uen,
        financial_year_end=date(2025, 12, 31),
        revenue=revenue,
        total_assets=total_assets,
        total_liabilities=total_liabilities,
        total_equity=total_equity,
        page=1,
        source_quotes={},
    )


def _sgt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 10, 0, tzinfo=timezone(timedelta(hours=8)))


def _make_doc_meta(
    doc_id: str,
    doc_type: str = "invoice",
    producer: str | None = "ReportLab PDF Library",
    creation_date: datetime | None = None,
    mod_date: datetime | None = None,
) -> DocMeta:
    if creation_date is None:
        creation_date = _sgt(date(2026, 7, 5))
    if mod_date is None:
        mod_date = creation_date
    return DocMeta(
        doc_id=doc_id,
        filename=f"{doc_id}.pdf",
        sha256="a" * 64,
        doc_type=doc_type,   # type: ignore[arg-type]
        page_count=1,
        creation_date=creation_date,
        mod_date=mod_date,
        producer=producer,
    )


# ===========================================================================
# GROUP A — FRAUD SIGNAL CHECKS
# ===========================================================================

# ---------------------------------------------------------------------------
# 1. INV_ARITH
# ---------------------------------------------------------------------------

class TestInvArith:
    def test_clean_invoice_no_flags(self):
        inv = _make_invoice()
        pack = _make_pack(invoices=[inv])
        assert check_inv_arith(pack) == []

    def test_line_amount_mismatch(self):
        items = [LineItem(description="Widget", quantity=10, unit_price=money("100.00"), amount=money("990.00"))]
        inv = _make_invoice(items=items, subtotal=money("990.00"), gst=money("89.10"), total=money("1079.10"))
        pack = _make_pack(invoices=[inv])
        evs = check_inv_arith(pack)
        check_ids = {e.check_id for e in evs}
        assert "INV_ARITH" in check_ids
        assert any("Widget" in (e.quote or "") for e in evs)

    def test_subtotal_mismatch(self):
        items = [LineItem(description="X", quantity=1, unit_price=money("100.00"), amount=money("100.00"))]
        inv = _make_invoice(items=items, subtotal=money("200.00"), gst=money("18.00"), total=money("218.00"))
        pack = _make_pack(invoices=[inv])
        evs = check_inv_arith(pack)
        assert any(e.check_id == "INV_ARITH" for e in evs)

    def test_gst_mismatch(self):
        items = [LineItem(description="X", quantity=1, unit_price=money("100.00"), amount=money("100.00"))]
        inv = _make_invoice(items=items, subtotal=money("100.00"), gst=money("50.00"), total=money("150.00"))
        pack = _make_pack(invoices=[inv])
        evs = check_inv_arith(pack)
        assert any(e.check_id == "INV_ARITH" for e in evs)

    def test_total_mismatch(self):
        items = [LineItem(description="X", quantity=1, unit_price=money("100.00"), amount=money("100.00"))]
        inv = _make_invoice(items=items, subtotal=money("100.00"), gst=money("9.00"), total=money("200.00"))
        pack = _make_pack(invoices=[inv])
        evs = check_inv_arith(pack)
        assert any(e.check_id == "INV_ARITH" for e in evs)

    def test_within_tolerance_no_flag(self):
        """Difference of exactly 0.50 (< 1.00 tolerance) must not flag."""
        items = [LineItem(description="X", quantity=1, unit_price=money("100.00"), amount=money("100.00"))]
        inv = _make_invoice(items=items, subtotal=money("100.00"), gst=money("9.50"), total=money("109.50"))
        pack = _make_pack(invoices=[inv])
        evs = check_inv_arith(pack)
        assert evs == []

    def test_all_evidence_category_fraud(self):
        items = [LineItem(description="X", quantity=2, unit_price=money("100.00"), amount=money("150.00"))]
        inv = _make_invoice(items=items, subtotal=money("150.00"), gst=money("13.50"), total=money("163.50"))
        pack = _make_pack(invoices=[inv])
        evs = check_inv_arith(pack)
        for e in evs:
            assert e.category == "fraud_signal"
            assert e.severity == "high"
            assert e.confidence == 1.0


# ---------------------------------------------------------------------------
# 2. BANK_RUNNING_BAL
# ---------------------------------------------------------------------------

class TestBankRunningBal:
    def test_correct_balance_no_flags(self):
        pack = _make_pack(bank_statements=[_make_bank_stmt()])
        assert check_bank_running_bal(pack) == []

    def test_altered_printed_balance(self):
        txns = [
            Transaction(date=date(2026, 7, 5), description="credit", debit=None, credit=money("5000.00"), balance=money("999999.00")),
        ]
        stmt = _make_bank_stmt(transactions=txns, closing=money("999999.00"))
        pack = _make_pack(bank_statements=[stmt])
        evs = check_bank_running_bal(pack)
        assert any(e.check_id == "BANK_RUNNING_BAL" for e in evs)

    def test_altered_closing_balance(self):
        txns = [
            Transaction(date=date(2026, 7, 5), description="cr", debit=None, credit=money("5000.00"), balance=money("105000.00")),
        ]
        stmt = _make_bank_stmt(transactions=txns, closing=money("999999.00"))
        pack = _make_pack(bank_statements=[stmt])
        evs = check_bank_running_bal(pack)
        assert any(e.check_id == "BANK_RUNNING_BAL" for e in evs)

    def test_debit_transaction(self):
        txns = [
            Transaction(date=date(2026, 7, 5), description="rent", debit=money("8000.00"), credit=None, balance=money("92000.00")),
        ]
        stmt = _make_bank_stmt(opening=money("100000.00"), transactions=txns, closing=money("92000.00"))
        pack = _make_pack(bank_statements=[stmt])
        assert check_bank_running_bal(pack) == []

    def test_evidence_fields(self):
        txns = [
            Transaction(date=date(2026, 7, 5), description="cr", debit=None, credit=money("5000.00"), balance=money("200000.00")),
        ]
        stmt = _make_bank_stmt(transactions=txns, closing=money("200000.00"))
        pack = _make_pack(bank_statements=[stmt])
        evs = check_bank_running_bal(pack)
        assert evs
        e = evs[0]
        assert e.severity == "high"
        assert e.confidence == 1.0
        assert e.category == "fraud_signal"


# ---------------------------------------------------------------------------
# 3. BANK_CONTINUITY
# ---------------------------------------------------------------------------

class TestBankContinuity:
    def _two_stmts(self, *, prev_closing, next_opening):
        s1 = _make_bank_stmt(
            "bank_statement_2026-06",
            opening=money("100000.00"),
            closing=prev_closing,
            period_start=date(2026, 6, 1),
            period_end=date(2026, 6, 30),
            transactions=[],
        )
        s2 = _make_bank_stmt(
            "bank_statement_2026-07",
            opening=next_opening,
            closing=money(str(next_opening)),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            transactions=[],
        )
        return _make_pack(bank_statements=[s1, s2])

    def test_matching_balances_no_flags(self):
        pack = self._two_stmts(prev_closing=money("129449.50"), next_opening=money("129449.50"))
        assert check_bank_continuity(pack) == []

    def test_mismatch_fires(self):
        pack = self._two_stmts(prev_closing=money("159449.50"), next_opening=money("129449.50"))
        evs = check_bank_continuity(pack)
        assert any(e.check_id == "BANK_CONTINUITY" for e in evs)

    def test_non_adjacent_skipped(self):
        """Statements 2 months apart should not fire BANK_CONTINUITY."""
        s1 = _make_bank_stmt(
            "bank_statement_2026-05",
            opening=money("100000.00"),
            closing=money("110000.00"),
            period_start=date(2026, 5, 1),
            period_end=date(2026, 5, 31),
            transactions=[],
        )
        # Skips June — not adjacent to July
        s2 = _make_bank_stmt(
            "bank_statement_2026-07",
            opening=money("999.00"),
            closing=money("999.00"),
            period_start=date(2026, 7, 1),
            period_end=date(2026, 7, 31),
            transactions=[],
        )
        pack = _make_pack(bank_statements=[s1, s2])
        # gap exists so not adjacent → no continuity flag
        assert check_bank_continuity(pack) == []

    def test_evidence_severity(self):
        pack = self._two_stmts(prev_closing=money("159449.50"), next_opening=money("129449.50"))
        evs = check_bank_continuity(pack)
        assert evs[0].severity == "high"
        assert evs[0].confidence == 1.0


# ---------------------------------------------------------------------------
# 4. FS_BALANCE
# ---------------------------------------------------------------------------

class TestFsBalance:
    def test_balanced_no_flags(self):
        pack = _make_pack(financial_statements=[_make_fs()])
        assert check_fs_balance(pack) == []

    def test_imbalanced_fires(self):
        fs = _make_fs(total_assets=money("9999999.00"))
        pack = _make_pack(financial_statements=[fs])
        evs = check_fs_balance(pack)
        assert any(e.check_id == "FS_BALANCE" for e in evs)

    def test_evidence_fields(self):
        fs = _make_fs(total_assets=money("9999999.00"))
        pack = _make_pack(financial_statements=[fs])
        evs = check_fs_balance(pack)
        e = evs[0]
        assert e.severity == "high"
        assert e.confidence == 1.0
        assert e.category == "fraud_signal"


# ---------------------------------------------------------------------------
# 5. XDOC_IDENTITY
# ---------------------------------------------------------------------------

class TestXdocIdentity:
    def test_matching_no_flags(self):
        fs = _make_fs()
        inv = _make_invoice()
        pack = _make_pack(invoices=[inv], financial_statements=[fs])
        assert check_xdoc_identity(pack) == []

    def test_uen_mismatch_fires_high(self):
        fs = _make_fs(uen="202301234K")
        inv = _make_invoice(seller_uen="202301284K")
        pack = _make_pack(invoices=[inv], financial_statements=[fs])
        evs = check_xdoc_identity(pack)
        uen_evs = [e for e in evs if e.severity == "high"]
        assert uen_evs
        assert "202301284K" in uen_evs[0].observed

    def test_name_mismatch_fires_medium(self):
        fs = _make_fs(company_name="Lumen Harbour Trading Pte. Ltd.")
        inv = _make_invoice(seller_name="Different Company Pte. Ltd.")
        pack = _make_pack(invoices=[inv], financial_statements=[fs])
        evs = check_xdoc_identity(pack)
        name_evs = [e for e in evs if e.severity == "medium"]
        assert name_evs

    def test_name_normalisation_allows_suffix_variation(self):
        """'Lumen Harbour Trading' and 'Lumen Harbour Trading Pte Ltd' should match."""
        fs = _make_fs(company_name="Lumen Harbour Trading Pte. Ltd.")
        inv = _make_invoice(seller_name="Lumen Harbour Trading Pte. Ltd.")
        stmt = _make_bank_stmt(account_holder="Lumen Harbour Trading Pte. Ltd.")
        pack = _make_pack(invoices=[inv], bank_statements=[stmt], financial_statements=[fs])
        evs = check_xdoc_identity(pack)
        assert evs == []

    def test_no_fs_returns_empty(self):
        pack = _make_pack(invoices=[_make_invoice()])
        assert check_xdoc_identity(pack) == []


# ---------------------------------------------------------------------------
# 6. XDOC_INVOICE_BANK
# ---------------------------------------------------------------------------

class TestXdocInvoiceBank:
    def test_exact_match_no_flags(self):
        inv = _make_invoice(invoice_date=date(2026, 6, 10))
        txn = Transaction(
            date=date(2026, 6, 18),
            description="payment",
            debit=None,
            credit=money("1090.00"),
            balance=money("200000.00"),
        )
        stmt = _make_bank_stmt(transactions=[txn], closing=money("200000.00"))
        pack = _make_pack(invoices=[inv], bank_statements=[stmt])
        evs = check_xdoc_invoice_bank(pack)
        assert evs == []

    def test_within_tolerance_emits_info(self):
        """Payment within 1 SGD tolerance but not exact → info, not high."""
        inv = _make_invoice(invoice_date=date(2026, 6, 10))
        # exact total is 1090.00; pay 1089.50 (diff 0.50 < 1.00)
        txn = Transaction(
            date=date(2026, 6, 18),
            description="payment",
            debit=None,
            credit=money("1089.50"),
            balance=money("200000.00"),
        )
        stmt = _make_bank_stmt(transactions=[txn], closing=money("200000.00"))
        pack = _make_pack(invoices=[inv], bank_statements=[stmt])
        evs = check_xdoc_invoice_bank(pack)
        assert len(evs) == 1
        assert evs[0].severity == "info"
        assert evs[0].check_id == "XDOC_INVOICE_BANK"

    def test_no_match_emits_high(self):
        inv = _make_invoice(invoice_date=date(2026, 6, 10))
        stmt = _make_bank_stmt(transactions=[], closing=money("100000.00"))
        pack = _make_pack(invoices=[inv], bank_statements=[stmt])
        evs = check_xdoc_invoice_bank(pack)
        assert any(e.severity == "high" and e.check_id == "XDOC_INVOICE_BANK" for e in evs)

    def test_credit_outside_window_emits_high(self):
        inv = _make_invoice(invoice_date=date(2026, 6, 10))
        # credit 31 days after invoice — outside 30-day window
        txn = Transaction(
            date=date(2026, 7, 12),
            description="late payment",
            debit=None,
            credit=money("1090.00"),
            balance=money("200000.00"),
        )
        stmt = _make_bank_stmt(transactions=[txn], closing=money("200000.00"))
        pack = _make_pack(invoices=[inv], bank_statements=[stmt])
        evs = check_xdoc_invoice_bank(pack)
        assert any(e.severity == "high" for e in evs)

    def test_each_credit_matches_at_most_one_invoice(self):
        """Two invoices with identical amounts; only one can claim the credit."""
        inv1 = _make_invoice("INV-001", invoice_date=date(2026, 6, 1))
        inv2 = _make_invoice("INV-002", invoice_date=date(2026, 6, 1))
        txn = Transaction(
            date=date(2026, 6, 5),
            description="payment",
            debit=None,
            credit=money("1090.00"),
            balance=money("200000.00"),
        )
        stmt = _make_bank_stmt(transactions=[txn], closing=money("200000.00"))
        pack = _make_pack(invoices=[inv1, inv2], bank_statements=[stmt])
        evs = check_xdoc_invoice_bank(pack)
        # One invoice matched, one high flag
        high_evs = [e for e in evs if e.severity == "high"]
        assert len(high_evs) == 1

    def test_expected_observed_populated(self):
        inv = _make_invoice(invoice_date=date(2026, 6, 10))
        stmt = _make_bank_stmt(transactions=[], closing=money("100000.00"))
        pack = _make_pack(invoices=[inv], bank_statements=[stmt])
        evs = check_xdoc_invoice_bank(pack)
        e = next(e for e in evs if e.severity == "high")
        assert e.expected is not None
        assert "SGD" in e.expected


# ---------------------------------------------------------------------------
# 7. META_MOD_AFTER_CREATE
# ---------------------------------------------------------------------------

class TestMetaModAfterCreate:
    def test_same_dates_no_flag(self):
        d = _sgt(date(2026, 7, 5))
        doc = _make_doc_meta("invoice_INV-0001", creation_date=d, mod_date=d)
        pack = _make_pack(docs=[doc])
        assert check_meta_mod_after_create(pack) == []

    def test_small_gap_no_flag(self):
        """23-hour gap is under 24h threshold."""
        d = _sgt(date(2026, 7, 5))
        mod = d + timedelta(hours=23)
        doc = _make_doc_meta("invoice_INV-0001", creation_date=d, mod_date=mod)
        pack = _make_pack(docs=[doc])
        assert check_meta_mod_after_create(pack) == []

    def test_large_gap_fires(self):
        """21-day gap (504 hours) exceeds 24h."""
        creation = _sgt(date(2026, 7, 5))
        mod = _sgt(date(2026, 7, 26)) + timedelta(hours=12)
        doc = _make_doc_meta("invoice_INV-0002", creation_date=creation, mod_date=mod)
        pack = _make_pack(docs=[doc])
        evs = check_meta_mod_after_create(pack)
        assert any(e.check_id == "META_MOD_AFTER_CREATE" for e in evs)

    def test_form_doc_types_skipped(self):
        """FATCA/CRS forms with large mod gap must NOT be flagged."""
        creation = _sgt(date(2026, 2, 1))
        mod = _sgt(date(2026, 11, 1))   # ~9 months later
        doc = _make_doc_meta("fatca_crs_entity", doc_type="fatca_crs_entity",
                             creation_date=creation, mod_date=mod)
        pack = _make_pack(docs=[doc])
        assert check_meta_mod_after_create(pack) == []

    def test_missing_dates_skipped(self):
        doc = _make_doc_meta("invoice_INV-0001", creation_date=None, mod_date=None)
        pack = _make_pack(docs=[doc])
        assert check_meta_mod_after_create(pack) == []

    def test_evidence_severity_confidence(self):
        creation = _sgt(date(2026, 7, 5))
        mod = _sgt(date(2026, 7, 26)) + timedelta(hours=12)
        doc = _make_doc_meta("invoice_INV-0002", creation_date=creation, mod_date=mod)
        pack = _make_pack(docs=[doc])
        evs = check_meta_mod_after_create(pack)
        e = evs[0]
        assert e.severity == "high"
        assert e.confidence == 0.7


# ---------------------------------------------------------------------------
# 8. META_PRODUCER_MISMATCH
# ---------------------------------------------------------------------------

class TestMetaProducerMismatch:
    def test_all_same_no_flags(self):
        docs = [_make_doc_meta(f"inv_{i}") for i in range(3)]
        pack = _make_pack(docs=docs)
        assert check_meta_producer_mismatch(pack) == []

    def test_one_different_fires(self):
        docs = [
            _make_doc_meta("inv_1", producer="ReportLab PDF Library"),
            _make_doc_meta("inv_2", producer="ReportLab PDF Library"),
            _make_doc_meta("inv_3", producer="QuickPDF Editor 9.2"),
        ]
        pack = _make_pack(docs=docs)
        evs = check_meta_producer_mismatch(pack)
        assert any(e.check_id == "META_PRODUCER_MISMATCH" for e in evs)
        assert any("inv_3" in e.document for e in evs)

    def test_form_types_excluded(self):
        """FATCA/CRS form with different producer must not fire."""
        docs = [
            _make_doc_meta("invoice_INV-1", producer="ReportLab PDF Library"),
            _make_doc_meta("invoice_INV-2", producer="ReportLab PDF Library"),
            _make_doc_meta("fatca_crs_entity", doc_type="fatca_crs_entity", producer="AcroForms Generator"),
        ]
        pack = _make_pack(docs=docs)
        evs = check_meta_producer_mismatch(pack)
        assert all(e.document != "fatca_crs_entity" for e in evs)

    def test_evidence_fields(self):
        docs = [
            _make_doc_meta("inv_1"),
            _make_doc_meta("inv_2", producer="Weird Producer"),
        ]
        pack = _make_pack(docs=docs)
        evs = check_meta_producer_mismatch(pack)
        e = evs[0]
        assert e.severity == "medium"
        assert e.confidence == 0.5
        assert e.category == "fraud_signal"


# ===========================================================================
# GROUP B — CHECKLIST AND COVERAGE
# ===========================================================================

class TestBankCoverage:
    def test_full_coverage_no_flags(self):
        stmts = []
        for month in range(2, 8):   # Feb–Jul 2026
            stmts.append(_make_bank_stmt(
                f"bank_statement_2026-{month:02d}",
                period_start=date(2026, month, 1),
                period_end=date(2026, month, 28 if month == 2 else 30 if month in (4, 6) else 31),
                transactions=[],
                closing=money("100000.00"),
            ))
        manifest = _make_manifest(application_date=date(2026, 8, 10))
        pack = _make_pack(bank_statements=stmts, manifest=manifest)
        evs = check_bank_coverage(pack)
        assert evs == []

    def test_missing_month_fires(self):
        stmts = []
        for month in (2, 3, 5, 6, 7):  # April missing
            stmts.append(_make_bank_stmt(
                f"bank_statement_2026-{month:02d}",
                period_start=date(2026, month, 1),
                period_end=date(2026, month, 28 if month == 2 else 30 if month in (6,) else 31),
                transactions=[],
                closing=money("100000.00"),
            ))
        manifest = _make_manifest(application_date=date(2026, 8, 10))
        pack = _make_pack(bank_statements=stmts, manifest=manifest)
        evs = check_bank_coverage(pack)
        assert any(e.check_id == "BANK_COVERAGE" for e in evs)
        assert any("2026-04" in (e.observed or "") for e in evs)

    def test_stale_statement_fires(self):
        stmt = _make_bank_stmt(
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            transactions=[],
            closing=money("100000.00"),
        )
        manifest = _make_manifest(application_date=date(2026, 8, 10))
        pack = _make_pack(bank_statements=[stmt], manifest=manifest)
        evs = check_bank_coverage(pack)
        stale_evs = [e for e in evs if "days" in (e.observed or "")]
        assert stale_evs

    def test_no_manifest_skips_recency(self):
        stmts = []
        for month in range(2, 8):   # Feb–Jul 2026
            last_day = 28 if month == 2 else 30 if month in (4, 6) else 31
            stmts.append(_make_bank_stmt(
                f"bank_statement_2026-{month:02d}",
                period_start=date(2026, month, 1),
                period_end=date(2026, month, last_day),
                transactions=[],
                closing=money("100000.00"),
            ))
        pack = _make_pack(bank_statements=stmts, manifest=None)
        evs = check_bank_coverage(pack)
        assert evs == []

    def test_category_completeness(self):
        stmts = []
        for month in (2, 3, 5, 6, 7):
            last_day = 28 if month == 2 else 30 if month in (6,) else 31
            stmts.append(_make_bank_stmt(
                f"bank_statement_2026-{month:02d}",
                period_start=date(2026, month, 1),
                period_end=date(2026, month, last_day),
                transactions=[],
                closing=money("100000.00"),
            ))
        manifest = _make_manifest(application_date=date(2026, 8, 10))
        pack = _make_pack(bank_statements=stmts, manifest=manifest)
        for e in check_bank_coverage(pack):
            assert e.category == "completeness"


class TestChecklistMissingDoc:
    def test_all_present_no_flags(self):
        docs = [
            _make_doc_meta("fatca_crs_entity", doc_type="fatca_crs_entity"),
            _make_doc_meta("bank_statement_2026-07", doc_type="bank_statement"),
        ]
        pack = _make_pack(docs=docs)
        assert check_checklist_missing_doc(pack) == []

    def test_missing_fatca_fires_high(self):
        docs = [_make_doc_meta("bank_statement_2026-07", doc_type="bank_statement")]
        pack = _make_pack(docs=docs)
        evs = check_checklist_missing_doc(pack)
        assert any("FATCA_CRS_ENTITY" in e.explanation.upper() or "fatca" in e.explanation.lower() for e in evs)

    def test_missing_bank_fires_high(self):
        docs = [_make_doc_meta("fatca_crs_entity", doc_type="fatca_crs_entity")]
        pack = _make_pack(docs=docs)
        evs = check_checklist_missing_doc(pack)
        assert any(e.severity == "high" for e in evs)

    def test_category_completeness(self):
        pack = _make_pack(docs=[])
        for e in check_checklist_missing_doc(pack):
            assert e.category == "completeness"


# ===========================================================================
# GROUP C — ENTITY FORM VALIDITY
# ===========================================================================

class TestFormEntMandatory:
    def test_fully_populated_no_flags(self):
        cert = _make_cert()
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_mandatory(pack) == []

    def test_missing_legal_name(self):
        cert = _make_cert(legal_name=None)
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_mandatory(pack)
        assert any("legal_name" in e.observed for e in evs)

    def test_missing_street(self):
        addr = _make_address(street=None)
        cert = _make_cert(registered_address=addr)
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_mandatory(pack)
        assert any("street" in (e.observed or "") for e in evs)

    def test_category_severity(self):
        cert = _make_cert(legal_name=None)
        pack = _make_pack(entity_self_certs=[cert])
        for e in check_form_ent_mandatory(pack):
            assert e.category == "compliance"
            assert e.severity == "medium"


class TestFormEntMailing:
    def test_mailing_not_different_skipped(self):
        cert = _make_cert(mailing_different=False)
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_mailing(pack) == []

    def test_mailing_different_missing_fields(self):
        cert = _make_cert(mailing_different=True, mailing_address=Address())
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_mailing(pack)
        assert evs

    def test_cross_country_no_explanation(self):
        mail = _make_address(country="Malaysia", postal_code="50000")
        cert = _make_cert(mailing_different=True, mailing_address=mail, mailing_explanation=None)
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_mailing(pack)
        assert any("explanation" in (e.observed or "") or "explanation" in e.explanation for e in evs)

    def test_cross_country_with_explanation_ok(self):
        mail = _make_address(country="Malaysia", postal_code="50000")
        cert = _make_cert(mailing_different=True, mailing_address=mail, mailing_explanation="Operational office")
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_mailing(pack)
        # No cross-country error (explanation provided)
        cross_evs = [e for e in evs if "explanation" in e.explanation and "cross" not in e.explanation.lower()]
        assert not any("explanation absent" in (e.observed or "") for e in evs)


class TestFormEntBranch:
    def test_single_no_ok(self):
        cert = _make_cert(branch_ticked=["No"])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_branch(pack) == []

    def test_nothing_ticked_fires(self):
        cert = _make_cert(branch_ticked=[])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_branch(pack)
        assert evs

    def test_both_ticked_fires(self):
        cert = _make_cert(branch_ticked=["No", "Yes"])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_branch(pack)

    def test_yes_without_hq_fires(self):
        cert = _make_cert(branch_ticked=["Yes"], hq_jurisdiction=None)
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_branch(pack)
        assert any("hq_jurisdiction" in e.explanation for e in evs)

    def test_yes_with_hq_ok(self):
        cert = _make_cert(branch_ticked=["Yes"], hq_jurisdiction="Singapore")
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_branch(pack) == []


class TestFormEntUsStatus:
    def test_no_ticked_ok(self):
        cert = _make_cert(us_person_ticked=["No"])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_us_status(pack) == []

    def test_nothing_ticked_fires(self):
        cert = _make_cert(us_person_ticked=[])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_us_status(pack)
        assert evs
        assert evs[0].severity == "high"

    def test_yes_without_tin_fires(self):
        cert = _make_cert(us_person_ticked=["Yes"], us_tin=None)
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_us_status(pack)
        assert any("us_tin" in e.explanation for e in evs)

    def test_yes_with_tin_ok(self):
        cert = _make_cert(us_person_ticked=["Yes"], us_tin="12-3456789")
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_us_status(pack) == []


class TestFormEntTinOrReason:
    def test_tin_present_ok(self):
        cert = _make_cert()
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_tin_or_reason(pack) == []

    def test_no_tin_no_reason_fires(self):
        row = TaxResidencyRow(row=1, country="Singapore", tin=None, reasons_ticked=[], explanation=None)
        cert = _make_cert(tax_rows=[row])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_tin_or_reason(pack)
        assert any("no TIN" in e.explanation or "neither TIN" in e.explanation for e in evs)

    def test_reason_b_plus_no_explanation_fires(self):
        row = TaxResidencyRow(row=1, country="Singapore", tin=None, reasons_ticked=["B+"], explanation=None)
        cert = _make_cert(tax_rows=[row])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_tin_or_reason(pack)
        assert any("B+" in e.explanation for e in evs)

    def test_us_no_with_no_rows_fires(self):
        cert = _make_cert(us_person_ticked=["No"], tax_rows=[])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_tin_or_reason(pack)
        assert any("no tax residency rows" in e.explanation.lower() for e in evs)

    def test_multiple_reasons_fires(self):
        row = TaxResidencyRow(row=1, country="UK", tin=None, reasons_ticked=["A", "C"], explanation=None)
        cert = _make_cert(tax_rows=[row])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_tin_or_reason(pack)
        assert any("multiple reasons" in e.explanation.lower() for e in evs)


class TestFormEntTypeSelection:
    def test_single_type_ok(self):
        cert = _make_cert(entity_types_ticked=["G"])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_type_selection(pack) == []

    def test_nothing_ticked_fires(self):
        cert = _make_cert(entity_types_ticked=[])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_type_selection(pack)

    def test_multiple_ticked_fires(self):
        cert = _make_cert(entity_types_ticked=["G", "H"])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_type_selection(pack)

    def test_type_a_missing_giin_fires(self):
        cert = _make_cert(entity_types_ticked=["A"], giin_parts=[None, None, None])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_type_selection(pack)
        assert any("GIIN" in e.explanation for e in evs)

    def test_type_a_with_giin_ok(self):
        cert = _make_cert(entity_types_ticked=["A"], giin_parts=["ABC123", "00001", "SG"])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_type_selection(pack) == []

    def test_type_d_no_cp_fires(self):
        cert = _make_cert(entity_types_ticked=["D"], controlling_persons=[])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_type_selection(pack)
        assert any("controlling person" in e.explanation.lower() for e in evs)

    def test_type_d_with_valid_cp_ok(self):
        cp = ControllingPerson(slot=1, name="Alice", types_ticked=["legal_ownership"])
        cert = _make_cert(entity_types_ticked=["D"], controlling_persons=[cp])
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_ent_type_selection(pack) == []

    def test_severity_high(self):
        cert = _make_cert(entity_types_ticked=[])
        pack = _make_pack(entity_self_certs=[cert])
        for e in check_form_ent_type_selection(pack):
            assert e.severity == "high"


class TestFormEntDeclaration:
    def test_valid_signatory_ok(self):
        cert = _make_cert()
        pack = _make_pack(entity_self_certs=[cert], manifest=_make_manifest())
        assert check_form_ent_declaration(pack) == []

    def test_no_signatories_fires(self):
        cert = _make_cert(signatories=[])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_declaration(pack)
        assert evs

    def test_signatory_date_after_app_date_fires(self):
        sig = Signatory(slot=1, name="Alice", designation="Director",
                       date_raw="15/09/26", date=date(2026, 9, 15),
                       signature_field_signed=False)
        cert = _make_cert(signatories=[sig])
        manifest = _make_manifest(application_date=date(2026, 8, 10))
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_declaration(pack)
        assert any("after the application date" in e.explanation for e in evs)

    def test_unparseable_date_ignored(self):
        sig = Signatory(slot=1, name="Bob", designation="GM",
                       date_raw="garbage", date=None,
                       signature_field_signed=False)
        cert = _make_cert(signatories=[sig])
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_ent_declaration(pack)
        # No valid signatory found
        assert evs


class TestFormTemplateMatch:
    def test_known_template_ok(self):
        cert = _make_cert(template_id="gldb_fatca_crs_entity_2025-11")
        pack = _make_pack(entity_self_certs=[cert])
        assert check_form_template_match(pack) == []

    def test_none_template_fires(self):
        cert = _make_cert(template_id=None)
        pack = _make_pack(entity_self_certs=[cert])
        evs = check_form_template_match(pack)
        assert evs
        assert any("outdated or altered" in e.explanation for e in evs)


# ===========================================================================
# GROUP D — ENTITY FORM REASONABLENESS
# ===========================================================================

class TestFormEntNameMatch:
    def test_matching_names_ok(self):
        cert = _make_cert(legal_name="Lumen Harbour Trading Pte. Ltd.")
        manifest = _make_manifest(company_name="Lumen Harbour Trading Pte. Ltd.")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_name_match(pack) == []

    def test_suffix_variation_ok(self):
        cert = _make_cert(legal_name="Lumen Harbour Trading Pte Ltd")
        manifest = _make_manifest(company_name="Lumen Harbour Trading Pte. Ltd.")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_name_match(pack) == []

    def test_different_name_fires(self):
        cert = _make_cert(legal_name="Different Corp Pte. Ltd.")
        manifest = _make_manifest(company_name="Lumen Harbour Trading Pte. Ltd.")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_name_match(pack)
        assert evs
        assert evs[0].severity == "high"

    def test_no_manifest_returns_manifest_missing(self):
        cert = _make_cert()
        pack = _make_pack(entity_self_certs=[cert], manifest=None)
        evs = check_form_ent_name_match(pack)
        assert any(e.check_id == "MANIFEST_MISSING" for e in evs)


class TestFormEntAddressMatch:
    def test_matching_address_ok(self):
        cert = _make_cert()
        manifest = _make_manifest()
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_address_match(pack) == []

    def test_wrong_postal_fires(self):
        addr = _make_address(postal_code="999999")
        cert = _make_cert(registered_address=addr)
        manifest = _make_manifest()
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_address_match(pack)
        assert any("postal" in e.explanation.lower() for e in evs)

    def test_wrong_country_fires(self):
        addr = _make_address(country="Malaysia")
        cert = _make_cert(registered_address=addr)
        manifest = _make_manifest()
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_address_match(pack)
        assert any("country" in e.explanation.lower() for e in evs)


class TestFormEntIncorporation:
    def test_matching_ok(self):
        cert = _make_cert(country_of_incorporation="Singapore")
        manifest = _make_manifest(country_of_incorporation="Singapore")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_incorporation(pack) == []

    def test_case_insensitive_ok(self):
        cert = _make_cert(country_of_incorporation="singapore")
        manifest = _make_manifest(country_of_incorporation="Singapore")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_incorporation(pack) == []

    def test_different_fires(self):
        cert = _make_cert(country_of_incorporation="Malaysia")
        manifest = _make_manifest(country_of_incorporation="Singapore")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_incorporation(pack)
        assert evs


class TestFormEntSgTin:
    def test_correct_uen_ok(self):
        cert = _make_cert()
        manifest = _make_manifest(uen="202301234K")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_sg_tin(pack) == []

    def test_wrong_tin_fires(self):
        row = TaxResidencyRow(row=1, country="Singapore", tin="WRONG123", reasons_ticked=[], explanation=None)
        cert = _make_cert(tax_rows=[row])
        manifest = _make_manifest(uen="202301234K")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_sg_tin(pack)
        assert evs
        assert "WRONG123" in evs[0].observed

    def test_non_sg_row_skipped(self):
        row = TaxResidencyRow(row=1, country="Malaysia", tin="WRONGTOO", reasons_ticked=[], explanation=None)
        cert = _make_cert(tax_rows=[row])
        manifest = _make_manifest(uen="202301234K")
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_sg_tin(pack) == []


class TestFormEntStartUp:
    def test_type_g_skipped(self):
        cert = _make_cert(entity_types_ticked=["G"])
        manifest = _make_manifest()
        pack = _make_pack(entity_self_certs=[cert], financial_statements=[_make_fs()], manifest=manifest)
        assert check_form_ent_start_up(pack) == []

    def test_type_h_young_no_revenue_ok(self):
        # Entity incorporated 2024-01, form date 2025-01 → 12 months < 24
        sig = Signatory(slot=1, name="Alice", designation="Dir",
                       date_raw="01/01/25", date=date(2025, 1, 1),
                       signature_field_signed=False)
        cert = _make_cert(entity_types_ticked=["H"], signatories=[sig])
        manifest = _make_manifest(date_of_incorporation=date(2024, 1, 1))
        pack = _make_pack(entity_self_certs=[cert], financial_statements=[_make_fs(revenue=money("0.00"))], manifest=manifest)
        assert check_form_ent_start_up(pack) == []

    def test_type_h_too_old_fires(self):
        sig = Signatory(slot=1, name="Alice", designation="Dir",
                       date_raw="01/01/27", date=date(2027, 1, 1),
                       signature_field_signed=False)
        cert = _make_cert(entity_types_ticked=["H"], signatories=[sig])
        manifest = _make_manifest(date_of_incorporation=date(2023, 1, 1))
        pack = _make_pack(entity_self_certs=[cert], financial_statements=[], manifest=manifest)
        evs = check_form_ent_start_up(pack)
        assert any("start-up" in e.explanation.lower() and "month" in e.explanation for e in evs)

    def test_type_h_revenue_nonzero_fires(self):
        cert = _make_cert(entity_types_ticked=["H"])
        manifest = _make_manifest()
        fs = _make_fs(revenue=money("1850000.00"))
        pack = _make_pack(entity_self_certs=[cert], financial_statements=[fs], manifest=manifest)
        evs = check_form_ent_start_up(pack)
        assert any("revenue" in e.explanation.lower() for e in evs)


class TestFormEntSignatory:
    def test_authorised_signatory_ok(self):
        cert = _make_cert()
        manifest = _make_manifest(authorised_signatories=["Tan Wei Ming"])
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_signatory(pack) == []

    def test_unauthorised_signatory_fires(self):
        sig = Signatory(slot=1, name="Unknown Person", designation="Dir",
                       date_raw="01/08/26", date=date(2026, 8, 1),
                       signature_field_signed=False)
        cert = _make_cert(signatories=[sig])
        manifest = _make_manifest(authorised_signatories=["Tan Wei Ming"])
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs = check_form_ent_signatory(pack)
        assert evs

    def test_normalisation_used(self):
        """'Tan Wei Ming' and 'TAN WEI MING' should be considered the same."""
        sig = Signatory(slot=1, name="TAN WEI MING", designation="Dir",
                       date_raw="01/08/26", date=date(2026, 8, 1),
                       signature_field_signed=False)
        cert = _make_cert(signatories=[sig])
        manifest = _make_manifest(authorised_signatories=["Tan Wei Ming"])
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        assert check_form_ent_signatory(pack) == []


# ===========================================================================
# GROUP E — RUNNER + EVIDENCE IDs
# ===========================================================================

class TestRunAgents:
    def test_evidence_ids_sequential_no_gaps(self):
        pack = _make_pack(
            invoices=[_make_invoice()],
            bank_statements=[_make_bank_stmt()],
            financial_statements=[_make_fs()],
        )
        evs, _ = run_agents(pack)
        for i, e in enumerate(evs, start=1):
            assert e.evidence_id == f"E{i:02d}", f"Expected E{i:02d} got {e.evidence_id}"

    def test_crashing_check_produces_system_error(self):
        """Replace one check with a bomb; runner must catch it."""
        integrity = AGENTS[0]
        original = integrity.checks[:]

        def _bomb(pack):
            raise RuntimeError("Deliberate boom")

        integrity.checks.insert(0, _bomb)
        try:
            pack = _make_pack()
            evs, _ = run_agents(pack)
            sys_errs = [e for e in evs if e.check_id == "SYSTEM_CHECK_ERROR"]
            assert sys_errs, "Expected at least one SYSTEM_CHECK_ERROR"
            assert sys_errs[0].category == "compliance"
            assert sys_errs[0].severity == "medium"
            assert sys_errs[0].confidence == 1.0
            assert "manual verification required" in sys_errs[0].explanation
        finally:
            integrity.checks[:] = original

    def test_extra_evidence_appended(self):
        pack = _make_pack()
        extra = [_ev_stub("EXTRA_TEST")]
        evs, _ = run_agents(pack, extra_evidence=extra)
        assert any(e.check_id == "EXTRA_TEST" for e in evs)

    def test_ids_include_extra(self):
        pack = _make_pack()
        extra = [_ev_stub("X1"), _ev_stub("X2")]
        evs, _ = run_agents(pack, extra_evidence=extra)
        for i, e in enumerate(evs, start=1):
            assert e.evidence_id == f"E{i:02d}"


def _ev_stub(check_id: str) -> Evidence:
    return Evidence(
        evidence_id="",
        check_id=check_id,
        category="fraud_signal",
        severity="info",
        document="test",
        page=1,
        method="deterministic",
        confidence=1.0,
        explanation="stub",
    )


# ===========================================================================
# PRIVACY — no personal identifiers in evidence fields
# ===========================================================================

class TestPrivacyInEvidence:
    _REAL_IDS = ["S1234567D", "G7654321L"]

    def _scan(self, text: str | None) -> None:
        if text is None:
            return
        for real_id in self._REAL_IDS:
            assert real_id not in text, (
                f"Personal identifier {real_id!r} leaked into evidence field"
            )

    def _check_evidence_list(self, evs: list[Evidence]) -> None:
        for e in evs:
            self._scan(e.explanation)
            self._scan(e.expected)
            self._scan(e.observed)
            self._scan(e.quote)
            self._scan(e.document)

    def test_manifest_guarantors_not_in_evidence(self):
        """Manifest checks should never expose guarantor id_numbers."""
        cert = _make_cert()
        manifest = _make_manifest()
        pack = _make_pack(entity_self_certs=[cert], manifest=manifest)
        evs, _ = run_agents(pack)
        self._check_evidence_list(evs)


# ===========================================================================
# PACK INTEGRATION TESTS (require templates)
# ===========================================================================

class TestCleanPack:
    @needs_templates
    def test_fraud_set_has_only_info(self, clean_pack):
        """Clean pack: fraud_signal evidence is either empty or info only."""
        evs, _ = run_agents(clean_pack)
        fraud_evs = [
            e for e in evs
            if e.category == "fraud_signal" and e.severity not in ("info",)
        ]
        assert fraud_evs == [], (
            f"Clean pack raised unexpected fraud flags: "
            f"{[(e.check_id, e.severity) for e in fraud_evs]}"
        )

    @needs_templates
    def test_clean_info_invoice_bank(self, clean_pack):
        """Clean pack: exactly one XDOC_INVOICE_BANK info for INV-1001 rounding."""
        evs, _ = run_agents(clean_pack)
        info_evs = [
            e for e in evs
            if e.check_id == "XDOC_INVOICE_BANK" and e.severity == "info"
        ]
        assert len(info_evs) == 1, f"Expected 1 info XDOC_INVOICE_BANK, got {len(info_evs)}"
        assert "INV-1001" in info_evs[0].document

    @needs_templates
    def test_compliance_set_empty(self, clean_pack):
        evs, _ = run_agents(clean_pack)
        compliance_evs = [
            e for e in evs
            if e.category in ("compliance", "completeness") and e.severity != "info"
        ]
        assert compliance_evs == [], (
            f"Clean pack raised compliance/completeness flags: "
            f"{[(e.check_id, e.severity) for e in compliance_evs]}"
        )

    @needs_templates
    def test_evidence_ids_no_gaps(self, clean_pack):
        evs, _ = run_agents(clean_pack)
        for i, e in enumerate(evs, start=1):
            assert e.evidence_id == f"E{i:02d}"


class TestTamperedPack:
    @needs_templates
    def test_fraud_set_matches_ground_truth(self, tampered_pack, tampered_truth):
        evs, _ = run_agents(tampered_pack)
        fraud_check_ids = {
            e.check_id
            for e in evs
            if e.category == "fraud_signal" and e.severity not in ("info",)
        }
        expected = set(tampered_truth["expected_check_ids"])
        assert fraud_check_ids == expected, (
            f"Fraud set mismatch.\n"
            f"  Expected: {sorted(expected)}\n"
            f"  Got:      {sorted(fraud_check_ids)}"
        )

    @needs_templates
    def test_inv_arith_does_not_fire(self, tampered_pack):
        """Tampered INV-1002 is internally consistent; INV_ARITH must not fire."""
        evs = check_inv_arith(tampered_pack)
        assert evs == [], f"INV_ARITH fired on tampered pack: {evs}"

    @needs_templates
    def test_compliance_set_empty(self, tampered_pack):
        evs, _ = run_agents(tampered_pack)
        compliance_evs = [
            e for e in evs
            if e.category in ("compliance", "completeness") and e.severity != "info"
        ]
        assert compliance_evs == [], (
            f"Tampered pack raised compliance flags: "
            f"{[(e.check_id, e.severity) for e in compliance_evs]}"
        )

    @needs_templates
    def test_no_personal_ids_in_evidence(self, tampered_pack):
        evs, _ = run_agents(tampered_pack)
        for real_id in ["S1234567D", "G7654321L"]:
            for e in evs:
                for field in (e.explanation, e.expected, e.observed, e.quote, e.document):
                    assert real_id not in (field or ""), (
                        f"{real_id} leaked into {e.check_id}.{field}"
                    )


class TestIncompletePack:
    @needs_templates
    def test_fraud_set_empty(self, incomplete_pack):
        evs, _ = run_agents(incomplete_pack)
        fraud_evs = [
            e for e in evs
            if e.category == "fraud_signal" and e.severity not in ("info",)
        ]
        assert fraud_evs == [], (
            f"Incomplete pack raised fraud flags: "
            f"{[(e.check_id, e.severity) for e in fraud_evs]}"
        )

    @needs_templates
    def test_compliance_set_matches_ground_truth(self, incomplete_pack, incomplete_truth):
        evs, _ = run_agents(incomplete_pack)
        compliance_check_ids = {
            e.check_id
            for e in evs
            if e.category in ("compliance", "completeness")
            and e.severity not in ("info",)
        }
        expected = set(incomplete_truth["expected_compliance_check_ids"])
        assert compliance_check_ids == expected, (
            f"Compliance set mismatch.\n"
            f"  Expected: {sorted(expected)}\n"
            f"  Got:      {sorted(compliance_check_ids)}"
        )

    @needs_templates
    def test_bank_continuity_does_not_fire_across_gap(self, incomplete_pack):
        """April is missing; May→July are not adjacent to March; no BANK_CONTINUITY."""
        evs = check_bank_continuity(incomplete_pack)
        assert evs == [], (
            f"BANK_CONTINUITY should not fire across gap, but got: {evs}"
        )

    @needs_templates
    def test_no_personal_ids_in_evidence(self, incomplete_pack):
        evs, _ = run_agents(incomplete_pack)
        for real_id in ["S1234567D", "G7654321L"]:
            for e in evs:
                for field in (e.explanation, e.expected, e.observed, e.quote, e.document):
                    assert real_id not in (field or ""), (
                        f"{real_id} leaked into {e.check_id}.{field}"
                    )


# ===========================================================================
# NORMALISE_NAME
# ===========================================================================

class TestNormaliseName:
    def test_strips_pte_ltd(self):
        assert normalise_name("Lumen Harbour Trading Pte. Ltd.") == "LUMEN HARBOUR TRADING"

    def test_strips_private_limited(self):
        assert normalise_name("Acme Private Limited") == "ACME"

    def test_strips_ltd(self):
        assert normalise_name("Acme Ltd.") == "ACME"

    def test_strips_pte(self):
        assert normalise_name("Acme Pte.") == "ACME"

    def test_collapses_spaces(self):
        assert normalise_name("  Alpha   Beta  Pte  Ltd  ") == "ALPHA BETA"

    def test_removes_punctuation(self):
        assert normalise_name("Alpha-Beta, Corp.") == "ALPHABETA CORP"


class TestAgents:
    def test_every_check_belongs_to_exactly_one_agent(self):
        checks = [c for agent in AGENTS for c in agent.checks]
        assert len(checks) == len(set(checks)) == 36

    def test_evidence_is_tagged_with_its_agent_and_reports_match(self):
        pack = _make_pack(
            invoices=[_make_invoice()],
            bank_statements=[_make_bank_stmt()],
            financial_statements=[_make_fs()],
        )
        evs, reports = run_agents(pack)
        assert [r.name for r in reports] == [a.name for a in AGENTS]
        assert all(e.agent in {a.name for a in AGENTS} for e in evs)
        for r in reports:
            assert r.checks_run == len(next(a for a in AGENTS if a.name == r.name).checks)
            assert r.findings == sum(
                e.agent == r.name and e.severity != "info" for e in evs
            )
