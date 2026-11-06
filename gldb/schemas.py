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
from typing import Annotated, Any, ClassVar, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Money
# ---------------------------------------------------------------------------


def _strip_money_text(value: Any) -> Any:
    """Accept money as printed ("SGD 1,234.56", "$1,234.56"). Formatting is
    removed; the figure itself is never changed. "-" or "" means no amount."""
    if isinstance(value, str):
        text = value.replace(",", "").replace("SGD", "").replace("S$", "").replace("$", "").strip()
        if text.startswith("(") and text.endswith(")"):
            text = "-" + text[1:-1]   # accountants' brackets: a loss
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
    #: The buyer's purchase order the invoice names, if it names one
    po_number: str | None = None
    line_items: list[LineItem]
    subtotal: Money
    #: The GST rate the invoice prints, as a fraction: 0.09, or 0 for a zero-rated supply such as an export
    gst_rate: Decimal = Decimal("0.09")
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


class Shipment(BaseModel):
    """Trade record: goods sent from a shipper to a consignee for an invoice,
    which it names by its invoice reference. A bill of lading for goods
    shipped abroad, a delivery order for goods delivered in Singapore."""

    doc_id: str
    #: The day the goods were shipped on board, or delivered
    shipped_date: date
    shipper_name: str
    consignee_name: str
    invoice_reference: str
    goods: list[ShippedGoods]
    page: int = 1
    source_quotes: dict[str, str] = {}

    #: What the document is, as a sentence and as a label names it, and what it says happened to the goods
    noun: ClassVar[str] = ""
    short: ClassVar[str] = ""
    verb: ClassVar[str] = ""

    @property
    def number(self) -> str:
        raise NotImplementedError

    @property
    def title(self) -> str:
        """The document as a sentence names it: "Bill of lading BL-3001"."""
        return f"{self.noun.capitalize()} {self.number}"


class BillOfLading(Shipment):
    """The carrier's receipt for goods shipped on board."""

    noun: ClassVar[str] = "bill of lading"
    short: ClassVar[str] = "B/L"
    verb: ClassVar[str] = "shipped"
    bl_number: str
    vessel: str
    port_of_loading: str
    port_of_discharge: str

    @property
    def number(self) -> str:
        return self.bl_number


class DeliveryOrder(Shipment):
    """The supplier's note of goods it delivered by its own transport,
    signed by the buyer on receipt."""

    noun: ClassVar[str] = "delivery order"
    short: ClassVar[str] = "Delivery order"
    verb: ClassVar[str] = "delivered"
    do_number: str

    @property
    def number(self) -> str:
        return self.do_number


class Transaction(BaseModel):
    date: date
    description: str
    debit: OptionalMoney
    credit: OptionalMoney
    balance: Money


class BankStatement(BaseModel):
    doc_id: str
    bank_name: str | None = None   # a fillable statement carries its bank only as a logo
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
    #: The identity the statements state; a workbook states none
    company_name: str | None = None
    uen: str | None = None
    financial_year_end: date
    revenue: Money
    total_assets: Money
    total_liabilities: Money
    total_equity: Money
    #: Further figures, in dollars, where the statements give them (a workbook does)
    cash: OptionalMoney = None
    total_current_assets: OptionalMoney = None
    total_current_liabilities: OptionalMoney = None
    #: Interest-bearing debt: short-term borrowings, current and long-term debt
    borrowings: OptionalMoney = None
    retained_earnings: OptionalMoney = None
    ebit: OptionalMoney = None
    profit_before_tax: OptionalMoney = None
    profit_after_tax: OptionalMoney = None
    operating_cashflow: OptionalMoney = None
    investing_cashflow: OptionalMoney = None
    financing_cashflow: OptionalMoney = None
    page: int = 1
    #: Field -> the page (sheet) a figure is on, where it is not `page`
    figure_pages: dict[str, int] = {}
    source_quotes: dict[str, str] = {}


class NoticeOfAssessment(BaseModel):
    """A guarantor's IRAS Notice of Assessment: who it is for and which year."""

    doc_id: str
    name: str
    id_number: str          # NRIC or FIN; masked with gldb.privacy.mask_id before display
    year_of_assessment: int
    #: The tax computation as printed; empty where the notice does not show it
    income: OptionalMoney = None
    deductions: OptionalMoney = None
    chargeable_income: OptionalMoney = None
    tax_payable: OptionalMoney = None
    #: Postal code of the address the notice is sent to, where it shows one
    postal_code: str | None = None
    page: int = 1
    source_quotes: dict[str, str] = {}


class AcraOfficer(BaseModel):
    name: str
    id_number: str          # NRIC or FIN; masked with gldb.privacy.mask_id before display
    position: str
    appointed: date
    #: Postal code of the address the profile gives the officer, if it does
    postal_code: str | None = None


class AcraShareholder(BaseModel):
    name: str
    shares: int


class AcraFinancials(BaseModel):
    """The latest financial period in an ACRA profile, as ACRA prints it. The
    profile states no unit (the sample's figures are SGD millions): see
    config.ACRA_FIGURES_UNIT. A figure ACRA marks unavailable is empty."""

    period_start: date
    period_end: date
    currency: str
    total_assets: OptionalMoney = None
    total_current_assets: OptionalMoney = None
    total_liabilities: OptionalMoney = None
    total_current_liabilities: OptionalMoney = None
    retained_earnings: OptionalMoney = None
    revenue: OptionalMoney = None
    profit_before_tax: OptionalMoney = None
    profit_after_tax: OptionalMoney = None
    ebit: OptionalMoney = None
    operating_cashflow: OptionalMoney = None
    investing_cashflow: OptionalMoney = None
    financing_cashflow: OptionalMoney = None


class AcraProfile(BaseModel):
    """An ACRA Corporate Compliance and Financial Profile: the register's own
    statement of the company, as of the day it was issued."""

    doc_id: str
    company_name: str
    uen: str
    incorporation_date: date
    status: str             # e.g. "LIVE COMPANY"
    registered_address: str  # as printed, with its postal code
    issued_on: date
    officers: list[AcraOfficer]
    shareholders: list[AcraShareholder]
    #: None where the profile has no financial information (no XBRL filing)
    financials: AcraFinancials | None = None
    page: int = 1
    source_quotes: dict[str, str] = {}


#: doc_id of a record fetched from ACRA or IRAS rather than uploaded (gldb.official)
ACRA_DOC = "ACRA"
IRAS_DOC = "IRAS"


class OfficialRecords(BaseModel):
    """The official records the pipeline fetched for a case (gldb.official):
    ACRA's record of each company the documents name and IRAS's latest Notice
    of Assessment for each person whose tax record the case relies on."""

    retrieved_at: datetime
    #: Data set -> version ("acra", "iras"), as the service reported them
    versions: dict[str, str] = {}
    #: The applicant's UEN as the documents give it; None if none does
    applicant_uen: str | None = None
    #: UEN -> ACRA's record; None where ACRA has no company with that UEN
    companies: dict[str, AcraProfile | None] = {}
    notices: list[NoticeOfAssessment] = []
    #: NRIC/FIN of each person IRAS returned no notice for (masked before display)
    no_notice: list[str] = []
    #: Why nothing was fetched (the service did not answer); None when it did
    error: str | None = None

    def applicant(self) -> AcraProfile | None:
        """ACRA's record of the applicant, if it has one."""
        return self.companies.get(self.applicant_uen) if self.applicant_uen else None


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


class Disclosure(BaseModel):
    """An interest or arrangement the applicant declares with its application.

    Rules do not read it: a disclosure is context for the analyst and the AI
    team, not evidence that changes a score or a route. A pack without one has
    an empty list.
    """

    kind: str
    counterparty: str
    detail: str = ""


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
    #: Declared interests and arrangements; empty for an application that declares none
    disclosures: list[Disclosure] = []


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
    #: The form page the row is on
    page: int = 1


class ControllingPerson(BaseModel):
    """One controlling-person slot (only slots with content stored)."""
    slot: int
    name: str | None
    types_ticked: list[str]
    #: The form page the slot is on
    page: int = 1


class Signatory(BaseModel):
    """One signatory slot on the self-cert form."""
    slot: int
    name: str | None
    designation: str | None
    date_raw: str | None     # raw string as it appears on the form
    date: date | None        # parsed date (None if unparseable)
    signature_field_signed: bool
    #: The form page the slot is on
    page: int = 1


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


class IndividualSelfCert(BaseModel):
    """Extracted fields from a GLDB FATCA/CRS Individual Self-Certification AcroForm."""
    doc_id: str
    template_id: str | None

    # Part 1 — identification
    full_name: str | None
    date_of_birth: date | None   # None if blank or unparseable
    place_of_birth: str | None
    residential_address: Address
    mailing_different: bool
    mailing_address: Address
    mailing_explanation: str | None

    # Part 2 — US status
    us_status_ticked: list[Literal["not_us_person", "us_person", "no_longer_us_person"]]
    us_tin_parts: list[str | None]   # masked with gldb.privacy.mask_id before logging

    # Part 3 — tax residency (only rows with content)
    tax_rows: list[TaxResidencyRow]

    # Declaration
    declaration_name: str | None
    declaration_id_number: str | None   # NRIC or FIN; masked with gldb.privacy.mask_id before display
    declaration_date: date | None
    #: The signature field carries a signature
    signature_field_signed: bool

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
    "delivery_order",
    "fatca_crs_entity",
    "fatca_crs_individual",
    "notice_of_assessment",
    "acra_profile",
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
    delivery_orders: list[DeliveryOrder] = []
    entity_self_certs: list[EntitySelfCert] = []
    individual_self_certs: list[IndividualSelfCert] = []
    notices_of_assessment: list[NoticeOfAssessment] = []
    acra_profiles: list[AcraProfile] = []
    manifest: ApplicationManifest | None = None
    #: What ACRA and IRAS hold for the case; None when the records stage did not run
    official: OfficialRecords | None = None
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

    def shipments(self) -> list[Shipment]:
        """Every bill of lading and delivery order: what shows the goods of an invoice left the seller."""
        return [*self.bills_of_lading, *self.delivery_orders]


# ---------------------------------------------------------------------------
# Evidence — the only container for a fraud finding
# ---------------------------------------------------------------------------

EvidenceCategory = Literal["fraud_signal", "compliance", "completeness"]
Severity = Literal["info", "low", "medium", "high", "critical"]
SignalStrength = Literal["definite", "indicative"]
RiskBand = Literal["Low", "Medium", "High"]


class EvidenceRow(BaseModel):
    """One of the figures behind a finding that one cause explains (every
    figure a statement misstates), each traceable to its own page and quote."""

    label: str
    page: int
    quote: str | None = None
    expected: str | None = None
    observed: str | None = None
    #: observed over expected, as printed (e.g. "2.00×"); None when it means nothing
    ratio: str | None = None


class Evidence(BaseModel):
    evidence_id: str = ""          # assigned later as E01, E02, …
    rule_group: str = ""           # name of the rule group that produced this finding
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
    explanation: str
    #: The figures behind it, when one finding stands for several (its page,
    #: quote, expected and observed are then the first row's)
    rows: list[EvidenceRow] = []

    def facts(self) -> list[str]:
        """What the finding states, rows included, without its quotes (document text)."""
        return [s for s in (self.explanation, self.expected, self.observed,
                            *(v for r in self.rows for v in (r.label, r.expected, r.observed, r.ratio)))
                if s]


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
    #: How much weight a finding can bear: "definite" when it is a fact on the
    #: documents (a contradiction, a sum that fails, a field left empty);
    #: "indicative" when it is a pattern that ordinary business can also produce
    strength: SignalStrength
    #: For an indicative signal, the innocent reasons to rule out before acting on it
    innocent: str = ""
    #: Fixed points; None uses the severity weight the check reported
    points: int | None = None

    @model_validator(mode="after")
    def _innocent_only_when_indicative(self) -> Signal:
        if (self.strength == "indicative") != bool(self.innocent):
            raise ValueError(f"{self.signal_id}: an indicative signal names its innocent reasons; a definite one has none")
        return self


class Contribution(BaseModel):
    """One line of the itemised risk score, traced to its evidence."""

    signal_id: str
    check_id: str
    name: str
    evidence_id: str
    document: str
    page: int
    points: int
    #: Findings of this check on this document the line covers: repeats are one problem, scored once
    hits: int = 1


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


class RuleGroupReport(BaseModel):
    """What one rule group did in a run: its scope, findings and time taken."""

    name: str
    description: str
    checks_run: int
    findings: int              # non-info findings
    duration_ms: int


#: Pipeline stages in run order; "record" (persist + audit) runs outside assess()
StageId = Literal[
    "ocr", "intake", "prompt_screen", "extract", "records", "rule_checks", "ai_team", "shadow_score", "score", "readiness", "route",
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
#: What a lead's tests show, as its specialist proposes it. Triage: the finding
#: stands / has an ordinary explanation / is not settled. Pattern: found /
#: checked and clear / not settled. It never decides anything.
InvestigationStatus = Literal["supported", "refuted", "unresolved"]
#: What code establishes about a kept lead's pattern from the case data
#: (gldb.ai_team.checks), never a model: only verified and corroborated
#: leads may become flags, memo points or questions
EvidenceStatus = Literal["verified", "corroborated", "unverified", "contradicted"]
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
    agent: AgentName
    tool: str
    args: dict[str, Any]
    #: Short, masked; never page text
    summary: str
    duration_ms: int
    #: The call came back as an error result
    error: bool = False


class LeadTest(BaseModel):
    """One test behind a lead: what the documents say should hold, and what
    the cited tool results show."""

    #: The tool results (R…) the test rests on: its agent's own recorded calls
    cites: list[str]
    expected: str
    observed: str


#: A stored lead's verdict (before the lead contract) -> its investigation status
_LEGACY_STATUS: dict[str | None, InvestigationStatus] = {
    "corroborated": "supported", "possibly_innocent": "refuted", "unclear": "unresolved", None: "supported",
}


class Lead(BaseModel):
    #: L01…, assigned when recorded
    lead_id: str = ""
    agent: SpecialistName
    #: triage: a judgement on risky findings; pattern: a gap-hunt for one of the specialist's patterns
    kind: Literal["triage", "pattern"]
    #: The findings (E…) a triage lead judges
    finding_ids: list[str] = []
    #: Set for pattern leads
    pattern: Pattern | None = None
    #: The subject: what a code check reads (gldb.ai_team.verify.PATTERN_SUBJECTS)
    invoice_number: str | None = None
    #: Bank rows, T01…
    transaction_ids: list[str] = []
    #: A counterparty, P01…
    counterparty: str | None = None
    #: The claim tested, e.g. "INV-1002 was paid by someone other than its buyer"
    hypothesis: str
    tests: list[LeadTest]
    investigation_status: InvestigationStatus
    #: What would settle an unresolved lead
    missing_evidence: list[str] = []
    #: Set by the verifier; withheld leads stay on the record with the reason
    outcome: Literal["kept", "withheld"] = "kept"
    withheld_reason: str | None = None
    #: Set by code for a kept lead, with the facts its check read and why
    evidence_status: EvidenceStatus | None = None
    check_facts: dict[str, str] = {}
    check_reason: str | None = None

    @property
    def cites(self) -> list[str]:
        """Every tool result (R…) the lead's tests rest on, in order."""
        return list(dict.fromkeys(c for test in self.tests for c in test.cites))

    @model_validator(mode="before")
    @classmethod
    def _stored_earlier(cls, data: Any) -> Any:
        """A lead saved before the lead contract (title, detail, cites, verdict:
        its detail becomes its one test's observation) or before the status
        split (status, strength) still loads, unverified if kept."""
        if not isinstance(data, dict) or "investigation_status" in data:
            return data
        if "hypothesis" not in data and "title" in data:
            data = {**data, "hypothesis": data["title"], "status": _LEGACY_STATUS.get(data.get("verdict"), "unresolved"),
                    "tests": [{"cites": data.get("cites", []), "expected": "", "observed": data.get("detail", "")}]}
        if "status" not in data:
            return data
        return {**data, "investigation_status": data["status"],
                "evidence_status": "unverified" if data.get("outcome", "kept") == "kept" else None}


class ShadowReason(BaseModel):
    #: A rule's check id, a verified pattern, or "no_issue": from the case's own lists
    code: str
    #: Findings (E…) and verified leads (L…) the reason rests on
    evidence_ids: list[str] = []


class ShadowScore(BaseModel):
    """What a model would route the case to, from the case context and the
    leads code verified; logged beside the champion, never used by it."""

    status: Literal["ran", "failed"]
    route: ReviewPath | None = None
    reasons: list[ShadowReason] = []
    model: str
    #: Content hash of the scorer's prompt and tool
    prompt_version: str
    #: Set after the fact, never shown to the scorer
    agrees_with_champion: bool | None = None
    error: str | None = None


class GateDecision(BaseModel):
    """The route the disposition gate would take: logged, never applied."""

    would_route: ReviewPath
    reasons: list[str]


class WithheldPoint(BaseModel):
    """A memo point the verifier dropped, kept on the record with why."""

    section: Literal["observation", "question"]
    text: str
    evidence_ids: list[str] = []
    reason: str


class Memo(BaseModel):
    observations: list[ReviewPoint] = []
    #: For the applicant or a counterparty
    questions: list[ReviewPoint] = []
    #: Memo points dropped by the verifier, malformed ones included
    withheld: int = 0
    #: The well-formed points among them, with the reason; empty in memos stored before they were kept
    withheld_points: list[WithheldPoint] = []


class DocRef(BaseModel):
    """A page of a case document: where the analyst is taken to see a subject."""

    doc_id: str
    page: int = 1


class AgentTeam(BaseModel):
    status: Literal["ran", "failed"]
    #: Specialist -> its brief
    plan: dict[str, str] = {}
    tool_calls: list[ToolCall] = []
    #: Every lead recorded, kept or withheld
    leads: list[Lead] = []
    #: The Supervisor's reconciliation
    memo: Memo | None = None
    #: {"supervisor": id, "specialist": id}
    models: dict[str, str] = {}
    prompt_versions: dict[str, str] = {}
    #: E.g. "transaction: step limit reached", "kyc: budget reached"
    notes: list[str] = []
    #: Rule findings code explained (finding id -> why): not raised in the memo, still on the record
    resolved: dict[str, str] = {}
    #: Why the team failed, when status is "failed"
    error: str | None = None
    #: call_id -> full tool result text; server-only (agent_trace.json), never serialised
    trace: dict[str, str] = Field(default_factory=dict, exclude=True)


#: Stages that no longer exist; results stored with their reports still load
_RETIRED_STAGES = frozenset({"semantic_review"})


class CaseResult(BaseModel):
    case_id: str
    pack: ExtractedPack
    evidence: list[Evidence]
    rule_group_reports: list[RuleGroupReport] = []
    risk: RiskResult
    readiness: ReadinessResult | None = None
    #: None only for results stored before routing existed
    routing: Routing | None = None
    chains: list[TransactionChain] = []
    timeline: list[TimelineEvent] = []
    investigation: list[InvestigationTask] = []
    #: None when the team is off
    ai_team: AgentTeam | None = None
    #: None when the team or the scorer is off; the champion never reads either
    shadow: ShadowScore | None = None
    gate: GateDecision | None = None
    #: Empty only for results stored before stages were recorded
    stages: list[StageReport] = []

    @model_validator(mode="before")
    @classmethod
    def _stored_earlier(cls, data: Any) -> Any:
        """A result stored when the rule groups were called agents still loads:
        agent_reports, each finding's agent and the "agents" stage take their
        new names."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "agent_reports" in data:
            data["rule_group_reports"] = data.pop("agent_reports")
        if isinstance(data.get("evidence"), list):
            data["evidence"] = [{**{k: v for k, v in e.items() if k != "agent"}, "rule_group": e["agent"]}
                                if isinstance(e, dict) and "agent" in e else e for e in data["evidence"]]
        if isinstance(data.get("stages"), list):
            data["stages"] = [{**s, "stage_id": "rule_checks"} if isinstance(s, dict) and s.get("stage_id") == "agents"
                              else s for s in data["stages"]]
        return data

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
