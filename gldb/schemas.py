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
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def _strip_money_text(value: Any) -> Any:
    """Accept money as printed ("SGD 1,234.56", "$1,234.56"). Formatting is
    removed; the figure itself is never changed. "-" or "" means no amount."""
    if isinstance(value, str):
        text = value.replace(",", "").replace("SGD", "").replace("S$", "").replace("$", "").strip()
        return None if text in ("", "-") else text
    return value


Money = Annotated[Decimal, BeforeValidator(_strip_money_text)]
OptionalMoney = Annotated[Decimal | None, BeforeValidator(_strip_money_text)]


# ---------------------------------------------------------------------------
# Document content models
# ---------------------------------------------------------------------------


class LineItem(BaseModel):
    description: str
    quantity: int
    unit_price: Money
    amount: Money


class Invoice(BaseModel):
    doc_id: str
    invoice_number: str
    invoice_date: date
    seller_name: str
    seller_uen: str
    buyer_name: str
    line_items: list[LineItem]
    subtotal: Money
    gst: Money
    total: Money
    page: int = 1
    source_quotes: dict[str, str] = {}


class PurchaseOrder(BaseModel):
    """Trade record: the buyer's order that an invoice should be consistent with."""

    doc_id: str
    po_number: str
    po_date: date
    buyer_name: str
    seller_name: str
    seller_uen: str
    line_items: list[LineItem]
    total: Money
    page: int = 1
    source_quotes: dict[str, str] = {}


class ShippedGoods(BaseModel):
    description: str
    quantity: int


class BillOfLading(BaseModel):
    """Trade record: the carrier's receipt for goods shipped. Ties to an
    invoice by its invoice reference."""

    doc_id: str
    bl_number: str
    shipped_date: date
    shipper_name: str
    consignee_name: str
    vessel: str
    port_of_loading: str
    port_of_discharge: str
    invoice_reference: str
    goods: list[ShippedGoods]
    page: int = 1
    source_quotes: dict[str, str] = {}


class Transaction(BaseModel):
    date: date
    description: str
    debit: OptionalMoney
    credit: OptionalMoney
    balance: Money


class BankStatement(BaseModel):
    doc_id: str
    bank_name: str
    account_holder: str
    period_start: date
    period_end: date
    opening_balance: Money
    closing_balance: Money
    transactions: list[Transaction]
    page: int = 1
    source_quotes: dict[str, str] = {}


class FinancialStatementSummary(BaseModel):
    doc_id: str
    company_name: str
    uen: str
    financial_year_end: date
    revenue: Money
    total_assets: Money
    total_liabilities: Money
    total_equity: Money
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
    "purchase_order",
    "bill_of_lading",
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


class Box(BaseModel):
    """A region of a page, as fractions (0-1) of its width and height."""

    x0: float
    top: float
    x1: float
    bottom: float


class OcrLine(BaseModel):
    """One line Textract read, with where it is on the page."""

    text: str
    box: Box


class PromptAttackHit(BaseModel):
    """A page the prompt attack screen flagged (gldb.prompt_screen)."""

    page: int
    #: The guardrail's confidence: "LOW", "MEDIUM" or "HIGH"
    confidence: str


class ExtractedPack(BaseModel):
    case_id: str
    docs: list[DocMeta]
    invoices: list[Invoice]
    bank_statements: list[BankStatement]
    financial_statements: list[FinancialStatementSummary]
    purchase_orders: list[PurchaseOrder] = []
    bills_of_lading: list[BillOfLading] = []
    entity_self_certs: list[EntitySelfCert] = []
    manifest: ApplicationManifest | None = None
    extraction_mode: Literal["llm", "stub", "mixed"]
    #: doc_id -> file on disk, for checks that inspect the raw file (forensics).
    #: Never serialised: it would leak local paths into results and the API.
    paths: dict[str, Path] = Field(default_factory=dict, exclude=True)
    #: doc_id -> Textract text per page, when the OCR stage ran. Never
    #: serialised: raw document text would bypass identifier masking.
    ocr: dict[str, list[str]] = Field(default_factory=dict, exclude=True)
    #: doc_id -> per page, the lines Textract read with their positions. Kept
    #: on the server with the case (for highlighting on scanned pages); never
    #: serialised, as it holds raw document text.
    ocr_lines: dict[str, list[list[OcrLine]]] = Field(default_factory=dict, exclude=True)
    #: doc_id -> mean Textract confidence (0-100) per page, when OCR ran
    ocr_confidence: dict[str, list[float]] = {}
    #: doc_id -> pages flagged by the prompt attack screen; None when the
    #: screen did not run, {} when it ran and flagged nothing
    prompt_screen: dict[str, list[PromptAttackHit]] | None = None


# ---------------------------------------------------------------------------
# Evidence — the only container for a fraud finding
# ---------------------------------------------------------------------------

EvidenceCategory = Literal["fraud_signal", "compliance", "completeness"]
Severity = Literal["info", "low", "medium", "high", "critical"]
RiskBand = Literal["Low", "Medium", "High"]


class Evidence(BaseModel):
    evidence_id: str = ""          # assigned later as E01, E02, …
    agent: str = ""                # name of the agent that produced this finding
    check_id: str
    #: fraud_signal feeds the risk score; compliance/completeness feed ReadinessResult only
    category: EvidenceCategory = "fraud_signal"
    severity: Severity
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


SignalFamily = Literal["DOC", "FIN", "TXN", "REG", "ENT", "TEMP", "COMP", "SYS"]


class Signal(BaseModel, frozen=True):
    """A named, versioned risk signal (gldb.signal_library). Maps one check
    to the name an analyst sees and to what would resolve it."""

    signal_id: str              # e.g. "DOC-001"; family prefix as below
    check_id: str
    family: SignalFamily
    name: str
    #: What would resolve or explain the signal ("what would change it?")
    remedy: str
    #: Fixed points; None uses the severity weight the check reported
    points: int | None = None


class Contribution(BaseModel):
    """One line of the itemised risk score, traced to its evidence."""

    signal_id: str
    check_id: str
    name: str
    evidence_id: str
    document: str
    page: int
    points: int


class RiskResult(BaseModel):
    score: int = Field(..., ge=0, le=100)
    band: RiskBand
    manual_review_required: bool
    reasons: list[str]
    #: The score itemised by signal; points may sum above the 100 cap
    contributions: list[Contribution] = []
    #: Signal library version that produced the contributions
    rule_version: str = ""

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


class AgentReport(BaseModel):
    """What one agent did in a run: its scope, findings and time taken."""

    name: str
    description: str
    checks_run: int
    findings: int              # non-info findings
    duration_ms: int


#: Pipeline stages in run order; "record" (persist + audit) runs outside assess()
StageId = Literal[
    "ocr", "intake", "prompt_screen", "extract", "agents", "ai_team", "score", "readiness", "route",
    "projections", "record",
]
#: "running" is only ever reported live; a finished stage is "ran", "failed", or "off"
StageState = Literal["running", "ran", "failed", "off"]


class StageReport(BaseModel):
    """What one pipeline stage did in a run: whether it ran or was switched
    off, how long it took, and the version (model, guardrail, rule set,
    policy) that ran."""

    stage_id: StageId
    status: Literal["ran", "failed", "off"]
    duration_ms: int
    version: str = ""


class ClaimRef(BaseModel):
    """A fact as one document states it, with where it is stated."""

    doc_id: str
    page: int
    label: str
    value: str


#: How an invoice relates to one counterpart record
ChainStatus = Literal["matched", "contradicts", "missing", "not_provided"]


class ChainStep(BaseModel):
    step: Literal["purchase_order", "bill_of_lading", "payment"]
    status: ChainStatus
    invoice_claim: ClaimRef
    #: The counterpart's claim (None when missing or not provided)
    counterpart: ClaimRef | None = None
    #: Findings behind this status (none for a clean match)
    evidence_ids: list[str] = []
    note: str = ""


class TransactionChain(BaseModel):
    """One invoice traced through order, shipment and payment, showing
    where the chain breaks (gldb.reconciliation)."""

    invoice_number: str
    doc_id: str
    steps: list[ChainStep]
    broken: bool


TimelineKind = Literal[
    "incorporated", "financial_year_end", "bank_statement", "purchase_order",
    "shipped", "invoice", "payment", "application",
]


class TimelineEvent(BaseModel):
    """One dated fact of the case, with the document and page it comes from
    (gldb.timeline)."""

    on: date
    kind: TimelineKind
    label: str
    doc_id: str
    page: int
    #: The invoice this fact belongs to ("" when it belongs to no trade)
    ref: str = ""
    #: Temporal findings about this fact
    evidence_ids: list[str] = []


#: Review paths, least to most cautious
ReviewPath = Literal["fast_track", "standard_review", "enhanced_review"]


TaskPriority = Literal["urgent", "high", "normal"]


class InvestigationTask(BaseModel):
    """One thing for the analyst to resolve: the findings of one signal on
    one document, with the signal's remedy and what resolving them would
    change (gldb.investigation). Guidance only: people decide."""

    task_id: str
    priority: TaskPriority
    signal_id: str
    check_id: str
    name: str
    category: EvidenceCategory
    document: str
    evidence_ids: list[str]
    remedy: str
    #: Score points these findings add (0 for readiness items)
    points: int
    #: The case if these findings were resolved, all else unchanged
    score_if_resolved: int
    band_if_resolved: RiskBand
    path_if_resolved: ReviewPath


class Routing(BaseModel):
    """Where a scored case goes next. Produced by gldb.routing from rules and
    versioned policy; never by an LLM. The system only routes: every outcome
    is decided by a person."""

    path: ReviewPath
    #: True when a person must review before funding (always, unless fast track
    #: and not drawn for the QA sample)
    analyst_review: bool
    qa_sampled: bool
    #: Enhanced due diligence
    edd_required: bool
    hard_flags: list[str]
    low_confidence: list[str]
    reasons: list[str]
    policy_version: str


class ReviewPoint(BaseModel):
    text: str
    #: Finding IDs (E…) and lead IDs (L…) the point relies on; never empty
    evidence_ids: list[str]


#: Agent roles: supervisor orchestrates, specialists investigate
AgentName = Literal["supervisor", "forensics", "transaction", "kyc", "timeline"]
#: Specialists that investigate: not the supervisor
SpecialistName = Literal["forensics", "transaction", "kyc", "timeline"]
#: Verdict on a lead
Verdict = Literal["corroborated", "possibly_innocent", "unclear"]
#: Fraud pattern families
Pattern = Literal[
    "third_party_payment",
    "round_trip",
    "split_payment",
    "related_party",
    "business_mismatch",
    "document_inconsistency",
    "sequence_anomaly",
]


class ToolCall(BaseModel):
    #: R01…
    call_id: str
    #: Agent that made the call
    agent: AgentName
    #: Tool name
    tool: str
    #: Tool arguments
    args: dict[str, Any]
    #: Short, masked; never page text
    summary: str
    #: Milliseconds taken
    duration_ms: int
    #: Whether the call raised an error
    error: bool = False


class Lead(BaseModel):
    #: L01…, assigned when recorded
    lead_id: str = ""
    #: Specialist that surfaced this lead
    agent: SpecialistName
    #: Triage (routine finding) or pattern (one we're watching)
    kind: Literal["triage", "pattern"]
    #: Evidence IDs (E…) supporting this lead
    finding_ids: list[str] = []
    #: Verdict on the lead (None until verified)
    verdict: Verdict | None = None
    #: Pattern, if kind is "pattern"
    pattern: Pattern | None = None
    #: Brief title
    title: str
    #: Detailed explanation
    detail: str
    #: R ids (tool call IDs) that support this lead
    cites: list[str]
    #: Whether the lead is kept or withheld in the final output
    outcome: Literal["kept", "withheld"] = "kept"
    #: Reason for withholding (None if kept)
    withheld_reason: str | None = None


class Memo(BaseModel):
    #: Observations made by the supervisor
    observations: list[ReviewPoint] = []
    #: Questions for the applicant
    questions: list[ReviewPoint] = []
    #: Memo points dropped by the verifier
    withheld: int = 0


class AgentTeam(BaseModel):
    #: Whether the team's run completed ("ran") or hit an error ("failed")
    status: Literal["ran", "failed"]
    #: Brief per specialist (specialist -> brief)
    plan: dict[str, str] = {}
    #: Tool calls made during the run
    tool_calls: list[ToolCall] = []
    #: Leads surfaced and verified
    leads: list[Lead] = []
    #: Supervisor's reconciliation
    memo: Memo | None = None
    #: Model IDs used: {"supervisor": id, "specialist": id}
    models: dict[str, str] = {}
    #: Prompt versions used
    prompt_versions: dict[str, str] = {}
    #: Notes (e.g. "transaction: step limit reached", "budget reached")
    notes: list[str] = []
    #: Error message if status is "failed"
    error: str | None = None
    #: call_id -> full tool result text; server-only (agent_trace.json), never serialised
    trace: dict[str, str] = Field(default_factory=dict, exclude=True)


#: Stages that no longer exist; results stored with their reports still load
_RETIRED_STAGES = frozenset({"semantic_review"})


class CaseResult(BaseModel):
    case_id: str
    pack: ExtractedPack
    evidence: list[Evidence]
    agent_reports: list[AgentReport] = []
    risk: RiskResult
    readiness: ReadinessResult | None = None
    #: None only for results stored before routing existed
    routing: Routing | None = None
    chains: list[TransactionChain] = []
    timeline: list[TimelineEvent] = []
    investigation: list[InvestigationTask] = []
    #: None when the team is off
    ai_team: AgentTeam | None = None
    #: Empty only for results stored before stages were recorded
    stages: list[StageReport] = []

    @field_validator("stages", mode="before")
    @classmethod
    def _drop_retired_stages(cls, stages: Any) -> Any:
        """Results stored before a stage was retired (e.g. "semantic_review")
        still load: reports of the retired stages are dropped. Any other
        unknown stage_id still fails validation."""
        if not isinstance(stages, list):
            return stages
        return [s for s in stages if not isinstance(s, dict) or s.get("stage_id") not in _RETIRED_STAGES]


# ---------------------------------------------------------------------------
# Audit event — append-only; written by gldb/audit.py
# ---------------------------------------------------------------------------


AuditAction = Literal[
    "case_created",
    "pipeline_completed",
    "flag_confirmed",
    "flag_dismissed",
    "documents_added",
    "analyst_decision",
    "mlro_decision",
    "final_decision",  # legacy: written before the case lifecycle existed
]

#: Case status, derived by replaying audit events (gldb.lifecycle)
CaseStatus = Literal[
    "assessed", "info_requested", "escalated", "mlro_reviewed", "approved", "declined",
]

#: Controlled vocabulary for dismissing a flag: lets override patterns be
#: counted (gldb.feedback) to tune checks and spot unfair outcomes.
ReasonCode = Literal[
    "ocr_misread",
    "extraction_error",
    "legitimate_explanation",
    "duplicate_finding",
    "threshold_too_strict",
    "other",
]


class AuditEvent(BaseModel):
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    case_id: str
    actor: str
    #: "analyst" or "mlro" for human actions; None for the system
    role: str | None = None
    action: AuditAction
    evidence_id: str | None = None
    decision: str | None = None
    reason_code: ReasonCode | None = None
    reason: str | None = None
    details: dict = {}
