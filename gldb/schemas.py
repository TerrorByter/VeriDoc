"""
gldb/schemas.py
---------------
All Pydantic v2 models for the gldb-prototype.

Rules:
- Every monetary field is decimal.Decimal (never float).
- Evidence is the only container for a fraud finding.
- RiskResult score is produced exclusively by gldb/scoring.py.
- Only fraud_signal Evidence feeds the risk score.
- Compliance and completeness Evidence feed ReadinessResult only.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Document content models
# ---------------------------------------------------------------------------


class LineItem(BaseModel):
    description: str
    quantity: int
    unit_price: Decimal
    amount: Decimal


class Invoice(BaseModel):
    doc_id: str
    invoice_number: str
    invoice_date: date
    seller_name: str
    seller_uen: str
    buyer_name: str
    line_items: list[LineItem]
    subtotal: Decimal
    gst: Decimal
    total: Decimal
    page: int = 1
    source_quotes: dict[str, str] = {}


class Transaction(BaseModel):
    date: date
    description: str
    debit: Decimal | None
    credit: Decimal | None
    balance: Decimal


class BankStatement(BaseModel):
    doc_id: str
    bank_name: str
    account_holder: str
    period_start: date
    period_end: date
    opening_balance: Decimal
    closing_balance: Decimal
    transactions: list[Transaction]
    page: int = 1
    source_quotes: dict[str, str] = {}


class FinancialStatementSummary(BaseModel):
    doc_id: str
    company_name: str
    uen: str
    financial_year_end: date
    revenue: Decimal
    total_assets: Decimal
    total_liabilities: Decimal
    total_equity: Decimal
    page: int = 1
    source_quotes: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Address (shared by application manifest and self-cert forms)
# ---------------------------------------------------------------------------


class Address(BaseModel):
    block: str | None = None
    street: str | None = None
    floor: str | None = None
    unit: str | None = None
    building: str | None = None
    city_state: str | None = None
    postal_code: str | None = None
    country: str | None = None


# ---------------------------------------------------------------------------
# Application manifest — the customer's online application data.
# This is the reference for reasonableness checks; treat as trusted input.
# ---------------------------------------------------------------------------


class Guarantor(BaseModel):
    name: str
    id_number: str          # masked with gldb.privacy.mask_id before logging
    nationality: str
    residential_address: Address


class ApplicationManifest(BaseModel):
    application_id: str
    application_date: date
    company_name: str
    uen: str
    country_of_incorporation: str
    date_of_incorporation: date
    registered_address: Address
    authorised_signatories: list[str]
    guarantors: list[Guarantor]


# ---------------------------------------------------------------------------
# FATCA / CRS Entity Self-Certification sub-models
# ---------------------------------------------------------------------------


class TaxResidencyRow(BaseModel):
    """One row in the tax residency table (only rows with any content stored)."""
    row: int
    country: str | None
    tin: str | None          # masked with gldb.privacy.mask_id before logging
    reasons_ticked: list[Literal["A", "B+", "C"]]
    explanation: str | None


class ControllingPerson(BaseModel):
    """One controlling-person slot (only slots with content stored)."""
    slot: int
    name: str | None
    types_ticked: list[str]


class Signatory(BaseModel):
    """One signatory slot on the self-cert form."""
    slot: int
    name: str | None
    designation: str | None
    date_raw: str | None     # raw string as it appears on the form
    date: date | None        # parsed date (None if unparseable)
    signature_field_signed: bool


class EntitySelfCert(BaseModel):
    """Extracted fields from a GLDB FATCA/CRS Entity Self-Certification AcroForm."""
    doc_id: str
    template_id: str | None

    # Part 1 — identification
    legal_name: str | None
    country_of_incorporation: str | None
    registered_address: Address
    mailing_different: bool
    mailing_address: Address
    mailing_explanation: str | None

    # Part 2 — branch
    branch_ticked: list[Literal["No", "Yes"]]
    hq_jurisdiction: str | None

    # Part 3 — US person
    us_person_ticked: list[Literal["No", "Yes"]]
    us_tin: str | None       # masked with gldb.privacy.mask_id before logging

    # Part 4 — tax residency (only rows with content)
    tax_rows: list[TaxResidencyRow]

    # Part 5 — entity type (letters A–M ticked)
    entity_types_ticked: list[str]
    giin_parts: list[str | None]

    # Part 6 — controlling persons (only slots with content)
    controlling_persons: list[ControllingPerson]

    # Part 7 — signatories (only slots with content)
    signatories: list[Signatory]

    # Raw field dump from pypdf get_fields() — never sent to an LLM
    raw_fields: dict[str, str]


# ---------------------------------------------------------------------------
# Document metadata
# ---------------------------------------------------------------------------

DocType = Literal[
    "invoice",
    "bank_statement",
    "financial_statements",
    "fatca_crs_entity",
    "fatca_crs_individual",
    "unknown",
]


class DocMeta(BaseModel):
    doc_id: str          # filename stem (no extension)
    filename: str
    sha256: str
    doc_type: DocType
    page_count: int
    creation_date: datetime | None
    mod_date: datetime | None
    producer: str | None


# ---------------------------------------------------------------------------
# Extracted pack (output of the intake + extract stages)
# ---------------------------------------------------------------------------


class ExtractedPack(BaseModel):
    case_id: str
    docs: list[DocMeta]
    invoices: list[Invoice]
    bank_statements: list[BankStatement]
    financial_statements: list[FinancialStatementSummary]
    entity_self_certs: list[EntitySelfCert] = []
    manifest: ApplicationManifest | None = None
    extraction_mode: Literal["llm", "stub", "mixed"]


# ---------------------------------------------------------------------------
# Evidence — the only container for a fraud finding
# ---------------------------------------------------------------------------


class Evidence(BaseModel):
    evidence_id: str = ""          # assigned later as E01, E02, …
    check_id: str
    #: fraud_signal feeds the risk score; compliance/completeness feed ReadinessResult only
    category: Literal["fraud_signal", "compliance", "completeness"] = "fraud_signal"
    severity: Literal["info", "low", "medium", "high", "critical"]
    document: str                  # doc_id
    page: int
    quote: str | None = None
    expected: str | None = None
    observed: str | None = None
    method: Literal["deterministic", "llm"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    explanation: str

    @field_validator("confidence")
    @classmethod
    def _confidence_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be between 0 and 1 inclusive, got {v}")
        return v


# ---------------------------------------------------------------------------
# Risk result — produced exclusively by gldb/scoring.py
# ---------------------------------------------------------------------------


class RiskResult(BaseModel):
    score: int = Field(..., ge=0, le=100)
    band: Literal["Low", "Medium", "High"]
    manual_review_required: bool
    reasons: list[str]

    @field_validator("score")
    @classmethod
    def _score_range(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError(f"score must be 0-100 inclusive, got {v}")
        return v


# ---------------------------------------------------------------------------
# Application readiness — driven by compliance/completeness Evidence only
# ---------------------------------------------------------------------------


class ReadinessResult(BaseModel):
    status: Literal["Complete", "Incomplete"]
    open_items: list[str]
    #: Each entry has keys: id, label, status
    #: status is one of: "Provided", "Missing", "Incomplete", "Not checked in prototype"
    checklist: list[dict]


# ---------------------------------------------------------------------------
# Case result — the final output of the pipeline
# ---------------------------------------------------------------------------


class CaseResult(BaseModel):
    case_id: str
    pack: ExtractedPack
    evidence: list[Evidence]
    risk: RiskResult
    readiness: ReadinessResult | None = None
    summary: str | None = None


# ---------------------------------------------------------------------------
# Audit event — append-only; written by gldb/audit.py
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    case_id: str
    actor: str
    action: Literal[
        "case_created",
        "pipeline_completed",
        "flag_confirmed",
        "flag_dismissed",
        "final_decision",
    ]
    evidence_id: str | None = None
    decision: str | None = None
    reason: str | None = None
    details: dict = {}
