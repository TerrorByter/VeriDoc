"""
tests/test_schemas.py
---------------------
Tests for gldb/config.py (money helper), gldb/privacy.py, and gldb/schemas.py.

Does NOT call AWS. Safe to run in CI without any env vars set.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from gldb.config import money
from gldb.privacy import mask_id
from gldb.schemas import (
    Address,
    ApplicationManifest,
    AuditEvent,
    BankStatement,
    CaseResult,
    ControllingPerson,
    DocMeta,
    EntitySelfCert,
    Evidence,
    ExtractedPack,
    FinancialStatementSummary,
    Guarantor,
    Invoice,
    LineItem,
    ReadinessResult,
    RiskResult,
    Signatory,
    TaxResidencyRow,
    Transaction,
)


# ---------------------------------------------------------------------------
# money() helper
# ---------------------------------------------------------------------------


def test_money_rounds_half_up():
    assert money("2.345") == Decimal("2.35")


def test_money_rounds_down():
    assert money("2.344") == Decimal("2.34")


def test_money_integer():
    assert money(10) == Decimal("10.00")


def test_money_already_decimal():
    assert money(Decimal("1.005")) == Decimal("1.01")


# ---------------------------------------------------------------------------
# mask_id()
# ---------------------------------------------------------------------------


def test_mask_id_nric():
    assert mask_id("S1234567D") == "S****567D"


def test_mask_id_short_all_stars():
    assert mask_id("A123") == "****"


def test_mask_id_exactly_five():
    assert mask_id("AB123") == "*****"


def test_mask_id_none():
    assert mask_id(None) is None


def test_mask_id_six_chars():
    # 6 chars: keep first + 1 star + last 4
    assert mask_id("AB1234") == "A*1234"


# ---------------------------------------------------------------------------
# Evidence validation
# ---------------------------------------------------------------------------


def _minimal_evidence(**overrides) -> dict:
    base = {
        "check_id": "CHK001",
        "severity": "medium",
        "document": "invoice_001",
        "page": 1,
        "method": "deterministic",
        "confidence": 0.9,
        "explanation": "Test finding.",
    }
    base.update(overrides)
    return base


def test_evidence_rejects_confidence_above_1():
    with pytest.raises(ValidationError):
        Evidence(**_minimal_evidence(confidence=1.5))


def test_evidence_rejects_negative_confidence():
    with pytest.raises(ValidationError):
        Evidence(**_minimal_evidence(confidence=-0.1))


def test_evidence_accepts_boundary_confidence():
    e0 = Evidence(**_minimal_evidence(confidence=0.0))
    e1 = Evidence(**_minimal_evidence(confidence=1.0))
    assert e0.confidence == 0.0
    assert e1.confidence == 1.0


def test_evidence_defaults_to_fraud_signal():
    e = Evidence(**_minimal_evidence())
    assert e.category == "fraud_signal"


def test_evidence_accepts_compliance_category():
    e = Evidence(**_minimal_evidence(category="compliance"))
    assert e.category == "compliance"


def test_evidence_accepts_completeness_category():
    e = Evidence(**_minimal_evidence(category="completeness"))
    assert e.category == "completeness"


# ---------------------------------------------------------------------------
# RiskResult validation
# ---------------------------------------------------------------------------


def _minimal_risk(**overrides) -> dict:
    base = {
        "score": 25,
        "band": "Medium",
        "manual_review_required": True,
        "reasons": ["High invoice mismatch"],
    }
    base.update(overrides)
    return base


def test_risk_rejects_score_101():
    with pytest.raises(ValidationError):
        RiskResult(**_minimal_risk(score=101))


def test_risk_rejects_negative_score():
    with pytest.raises(ValidationError):
        RiskResult(**_minimal_risk(score=-1))


def test_risk_accepts_boundary_scores():
    r0 = RiskResult(**_minimal_risk(score=0, band="Low"))
    r100 = RiskResult(**_minimal_risk(score=100, band="High"))
    assert r0.score == 0
    assert r100.score == 100


# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


def _roundtrip(model_instance):
    """Serialise to JSON then re-parse; return the re-parsed instance."""
    cls = type(model_instance)
    raw = model_instance.model_dump_json()
    return cls.model_validate_json(raw)


def _make_address(**kwargs) -> Address:
    defaults = {
        "block": "10",
        "street": "Anson Road",
        "floor": "20",
        "unit": "05",
        "building": "International Plaza",
        "city_state": "Singapore",
        "postal_code": "079903",
        "country": "SG",
    }
    defaults.update(kwargs)
    return Address(**defaults)


# ---------------------------------------------------------------------------
# Round-trip: existing financial models
# ---------------------------------------------------------------------------


def _make_line_item() -> LineItem:
    return LineItem(
        description="Consulting services",
        quantity=2,
        unit_price=Decimal("500.00"),
        amount=Decimal("1000.00"),
    )


def _make_invoice() -> Invoice:
    return Invoice(
        doc_id="invoice_001",
        invoice_number="INV-2026-001",
        invoice_date=date(2026, 1, 15),
        seller_name="Acme Pte Ltd",
        seller_uen="202312345A",
        buyer_name="Beta Corp Pte Ltd",
        line_items=[_make_line_item()],
        subtotal=Decimal("1000.00"),
        gst=Decimal("90.00"),
        total=Decimal("1090.00"),
    )


def _make_transaction() -> Transaction:
    return Transaction(
        date=date(2026, 1, 16),
        description="Payment from Beta Corp",
        debit=None,
        credit=Decimal("1090.00"),
        balance=Decimal("5090.00"),
    )


def _make_bank_statement() -> BankStatement:
    return BankStatement(
        doc_id="bank_stmt_001",
        bank_name="DBS Bank",
        account_holder="Acme Pte Ltd",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 1, 31),
        opening_balance=Decimal("4000.00"),
        closing_balance=Decimal("5090.00"),
        transactions=[_make_transaction()],
    )


def _make_financial_statement() -> FinancialStatementSummary:
    return FinancialStatementSummary(
        doc_id="fin_stmt_001",
        company_name="Acme Pte Ltd",
        uen="202312345A",
        financial_year_end=date(2025, 12, 31),
        revenue=Decimal("500000.00"),
        total_assets=Decimal("800000.00"),
        total_liabilities=Decimal("300000.00"),
        total_equity=Decimal("500000.00"),
    )


def _make_doc_meta(doc_id: str, doc_type: str) -> DocMeta:
    return DocMeta(
        doc_id=doc_id,
        filename=f"{doc_id}.pdf",
        sha256="a" * 64,
        doc_type=doc_type,
        page_count=1,
        creation_date=None,
        mod_date=None,
        producer=None,
    )


def _make_pack() -> ExtractedPack:
    return ExtractedPack(
        case_id="CASE-001",
        docs=[
            _make_doc_meta("invoice_001", "invoice"),
            _make_doc_meta("bank_stmt_001", "bank_statement"),
            _make_doc_meta("fin_stmt_001", "financial_statements"),
        ],
        invoices=[_make_invoice()],
        bank_statements=[_make_bank_statement()],
        financial_statements=[_make_financial_statement()],
        extraction_mode="stub",
    )


def _make_evidence() -> Evidence:
    return Evidence(
        evidence_id="E01",
        check_id="CHK_GST",
        severity="high",
        document="invoice_001",
        page=1,
        quote="Total: 1090.00",
        expected="1090.00",
        observed="1090.00",
        method="deterministic",
        confidence=1.0,
        explanation="GST calculation matches.",
    )


def _make_risk() -> RiskResult:
    return RiskResult(
        score=30,
        band="Medium",
        manual_review_required=True,
        reasons=["Invoice total mismatch"],
    )


def _make_case_result() -> CaseResult:
    return CaseResult(
        case_id="CASE-001",
        pack=_make_pack(),
        evidence=[_make_evidence()],
        risk=_make_risk(),
        summary=None,
    )


def test_line_item_roundtrip():
    original = _make_line_item()
    restored = _roundtrip(original)
    assert restored.unit_price == original.unit_price
    assert restored.amount == original.amount


def test_invoice_roundtrip_decimal():
    original = _make_invoice()
    restored = _roundtrip(original)
    assert restored.subtotal == Decimal("1000.00")
    assert restored.gst == Decimal("90.00")
    assert restored.total == Decimal("1090.00")
    assert restored.line_items[0].unit_price == Decimal("500.00")


def test_transaction_roundtrip():
    original = _make_transaction()
    restored = _roundtrip(original)
    assert restored.credit == Decimal("1090.00")
    assert restored.debit is None


def test_bank_statement_roundtrip():
    original = _make_bank_statement()
    restored = _roundtrip(original)
    assert restored.opening_balance == Decimal("4000.00")
    assert restored.closing_balance == Decimal("5090.00")


def test_financial_statement_roundtrip():
    original = _make_financial_statement()
    restored = _roundtrip(original)
    assert restored.revenue == Decimal("500000.00")
    assert restored.total_equity == Decimal("500000.00")


def test_evidence_roundtrip():
    original = _make_evidence()
    restored = _roundtrip(original)
    assert restored.evidence_id == "E01"
    assert restored.confidence == 1.0
    assert restored.category == "fraud_signal"


def test_risk_result_roundtrip():
    original = _make_risk()
    restored = _roundtrip(original)
    assert restored.score == 30
    assert restored.band == "Medium"


def test_case_result_roundtrip():
    original = _make_case_result()
    restored = _roundtrip(original)
    assert restored.case_id == "CASE-001"
    assert restored.pack.invoices[0].total == Decimal("1090.00")
    assert restored.evidence[0].evidence_id == "E01"
    assert restored.risk.score == 30


def test_audit_event_default_timestamp_is_utc():
    evt = AuditEvent(
        case_id="CASE-001",
        actor="officer@gldb.com",
        action="case_created",
    )
    assert evt.timestamp.tzinfo is not None
    assert evt.timestamp.tzinfo == timezone.utc


def test_audit_event_roundtrip():
    evt = AuditEvent(
        case_id="CASE-001",
        actor="officer@gldb.com",
        action="flag_confirmed",
        evidence_id="E01",
        decision="confirmed",
        reason="Amount clearly inflated",
        details={"note": "cross-checked with bank"},
    )
    restored = _roundtrip(evt)
    assert restored.action == "flag_confirmed"
    assert restored.evidence_id == "E01"
    assert restored.details == {"note": "cross-checked with bank"}


# ---------------------------------------------------------------------------
# Round-trip: ApplicationManifest
# ---------------------------------------------------------------------------


def _make_manifest() -> ApplicationManifest:
    return ApplicationManifest(
        application_id="APP-2026-001",
        application_date=date(2026, 9, 1),
        company_name="Acme Pte Ltd",
        uen="202312345A",
        country_of_incorporation="SG",
        date_of_incorporation=date(2023, 3, 15),
        registered_address=_make_address(),
        authorised_signatories=["Alice Tan", "Bob Lim"],
        guarantors=[
            Guarantor(
                name="Alice Tan",
                id_number="S1234567D",
                nationality="Singaporean",
                residential_address=_make_address(street="Orchard Road", postal_code="238841"),
            )
        ],
    )


def test_application_manifest_roundtrip():
    original = _make_manifest()
    restored = _roundtrip(original)
    assert restored.application_id == "APP-2026-001"
    assert restored.uen == "202312345A"
    assert restored.guarantors[0].name == "Alice Tan"
    assert restored.registered_address.postal_code == "079903"


# ---------------------------------------------------------------------------
# Round-trip: EntitySelfCert
# ---------------------------------------------------------------------------


def _make_entity_self_cert() -> EntitySelfCert:
    return EntitySelfCert(
        doc_id="fatca_entity_001",
        template_id="GLDB-FATCA-ENTITY-NOV2025",
        legal_name="Acme Pte Ltd",
        country_of_incorporation="SG",
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
                country="SG",
                tin="202312345A",
                reasons_ticked=["A"],
                explanation=None,
            )
        ],
        entity_types_ticked=["A"],
        giin_parts=[None, None, None],
        controlling_persons=[
            ControllingPerson(
                slot=1,
                name="Alice Tan",
                types_ticked=["Type A"],
            )
        ],
        signatories=[
            Signatory(
                slot=1,
                name="Alice Tan",
                designation="Director",
                date_raw="01/09/2026",
                date=date(2026, 9, 1),
                signature_field_signed=True,
            )
        ],
        raw_fields={"Part1_LegalName": "Acme Pte Ltd", "Part3_USTicked": "No"},
    )


def test_entity_self_cert_roundtrip():
    original = _make_entity_self_cert()
    restored = _roundtrip(original)
    assert restored.doc_id == "fatca_entity_001"
    assert restored.legal_name == "Acme Pte Ltd"
    assert restored.tax_rows[0].country == "SG"
    assert restored.signatories[0].signature_field_signed is True
    assert restored.raw_fields["Part3_USTicked"] == "No"


def test_entity_self_cert_in_pack_roundtrip():
    pack = _make_pack()
    cert = _make_entity_self_cert()
    pack_with_cert = ExtractedPack(
        case_id=pack.case_id,
        docs=pack.docs + [_make_doc_meta("fatca_entity_001", "fatca_crs_entity")],
        invoices=pack.invoices,
        bank_statements=pack.bank_statements,
        financial_statements=pack.financial_statements,
        entity_self_certs=[cert],
        manifest=_make_manifest(),
        extraction_mode="stub",
    )
    restored = _roundtrip(pack_with_cert)
    assert len(restored.entity_self_certs) == 1
    assert restored.entity_self_certs[0].legal_name == "Acme Pte Ltd"
    assert restored.manifest is not None
    assert restored.manifest.uen == "202312345A"
