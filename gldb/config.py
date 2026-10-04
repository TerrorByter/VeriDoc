"""
gldb/config.py
--------------
All configuration and constants for the gldb-prototype.
Loads environment variables from .env via python-dotenv.
No secrets are hardcoded here.
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Money helper
# ---------------------------------------------------------------------------

def money(x: int | float | str | Decimal) -> Decimal:
    """Quantize *x* to SGD 0.01 using ROUND_HALF_UP. Always use this instead
    of bare Decimal() for any monetary value that originates from arithmetic
    or user/document input."""
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Domain constants
# ---------------------------------------------------------------------------

#: Singapore GST rate (9 %)
GST_RATE: Decimal = Decimal("0.09")

#: Maximum acceptable difference between two amounts before flagging
TOLERANCE_SGD: Decimal = Decimal("1.00")

#: How many calendar days either side to look for a matching bank transaction
MATCH_WINDOW_DAYS: int = 30

#: Minimum gap (hours) between PDF creation date and modification date that
#: triggers the metadata-tampering check
META_MOD_GAP_HOURS: int = 24

#: Maximum allowed upload size in megabytes
MAX_FILE_MB: int = 10

# ---------------------------------------------------------------------------
# Checklist / application readiness constants
# ---------------------------------------------------------------------------

#: Minimum months of bank statements required
REQUIRED_BANK_MONTHS: int = 6

#: Maximum age (days) of the most-recent bank statement page before flagging
#: as stale. Assumption — to be confirmed with GLDB.
BANK_RECENCY_DAYS: int = 45

#: Entity type H ("start-up") maximum age in months (from entity form option H)
START_UP_MAX_MONTHS: int = 24

#: Directory containing official blank GLDB form templates (read-only)
TEMPLATES_DIR: str = "data/templates"

#: Directory containing the synthetic demo packs (one sub-folder per pack)
PACKS_DIR: str = "data/packs"

#: Runtime directories (gitignored): officer uploads and per-case results
UPLOADS_DIR: str = "data/uploads"
CASES_DIR: str = "data/cases"

#: Checklist items for the loan application.
#: "implemented" indicates whether automated checking is available in this prototype.
CHECKLIST: list[dict] = [
    {
        "id": "FATCA_CRS_ENTITY",
        "label": "FATCA & CRS Self-Certification Form (entity)",
        "implemented": True,
    },
    {
        "id": "BANK_6M",
        "label": "Latest 6 months of bank statements",
        "implemented": True,
    },
    {
        "id": "NOA_GUARANTORS",
        "label": "Latest Notice of Assessment by all guarantors",
        "implemented": False,
    },
    {
        "id": "POA_FOREIGNERS",
        "label": "Proof of residential address (foreigners only)",
        "implemented": False,
    },
]

#: Items that require manual officer review; cannot be checked programmatically.
MANUAL_CHECKS: list[str] = [
    "Signatures on self-certification forms",
    "Signatories match the Extract of Board Resolution",
    "Supporting identity / tax residence documents",
    "US IRS Form W-8 or W-9 where the form requires one",
]

# ---------------------------------------------------------------------------
# Forensics
# ---------------------------------------------------------------------------

#: Text smaller than this (points) is treated as hidden
HIDDEN_TEXT_MAX_SIZE_PT: float = 2.0

#: A gray/RGB fill with every component at or above this counts as white
WHITE_FILL_MIN: float = 0.95

#: Two words closer than this (fraction of font size) form one token. Must stay
#: below a space (about 0.28 em) and above kerning/rounding noise.
TOKEN_GAP_FRACTION: float = 0.15

#: Longest hidden-text excerpt kept as Evidence quote
HIDDEN_QUOTE_MAX_CHARS: int = 200

# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

#: Points added to the risk score per severity level
SEVERITY_WEIGHTS: dict[str, int] = {
    "info": 0,
    "low": 5,
    "medium": 15,
    "high": 30,
    "critical": 50,
}

#: Score bands  (score 0-19 → Low, 20-49 → Medium, 50-100 → High)
BAND_LOW_MAX: int = 19
BAND_MEDIUM_MAX: int = 49

# ---------------------------------------------------------------------------
# Routing policy (gldb/routing.py). Every Routing records POLICY_VERSION, so a
# past decision can be replayed against the policy that made it. Bump the
# version whenever anything in this section changes.
# ---------------------------------------------------------------------------

POLICY_VERSION: str = "2026-10-04.2"

#: Findings serious enough on their own to force enhanced review (EDD),
#: whatever the score: sanctions hits, unregistered entities, and deliberate
#: concealment (hidden text aimed at automated review).
HARD_FLAG_CHECK_IDS: frozenset[str] = frozenset({
    "SANCTIONS_MATCH",
    "REGISTRY_ENTITY_NOT_FOUND",
    "FORENSIC_HIDDEN_TEXT",
    "PROMPT_INJECTION",
})

#: Share of fast-track cases also sent to an analyst for quality assurance
QA_SAMPLE_PERCENT: int = 5

#: Textract page confidence (0-100) below which a case goes to an analyst
OCR_MIN_CONFIDENCE: float = 80.0

# ---------------------------------------------------------------------------
# Reference data and screening (gldb/reference.py, gldb/screening.py)
# ---------------------------------------------------------------------------

#: Versioned reference data: synthetic ACRA register and sanctions list
REFERENCE_DIR: str = "data/reference"

#: Name similarity (0-1) at or above which a screened name is a sanctions hit.
#: Tuned for recall: a false hit costs an analyst a look, a miss is a breach.
SCREENING_MATCH_THRESHOLD: float = 0.90

# ---------------------------------------------------------------------------
# Environment / runtime settings (with safe defaults)
# ---------------------------------------------------------------------------

def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no", ""):
        return default
    return default


#: "stub" uses the deterministic parser; "llm" calls AWS Bedrock.
EXTRACTION_MODE: str = os.getenv("EXTRACTION_MODE", "stub").strip().lower()
if EXTRACTION_MODE not in ("stub", "llm"):
    raise ValueError(
        f"EXTRACTION_MODE must be 'stub' or 'llm', got {EXTRACTION_MODE!r}"
    )

AWS_REGION: str = os.getenv("AWS_REGION", "ap-southeast-1")

#: Bedrock model ID or cross-region inference profile ARN.
BEDROCK_MODEL_ID: str = os.getenv("BEDROCK_MODEL_ID", "")

#: Whether to run the OCR stage: Textract reads every page, so scans can be
#: parsed and the text layer can be compared with what a reader sees.
OCR_ENABLED: bool = _bool_env("OCR_ENABLED", False)

#: Concurrent Textract calls. Match the account's DetectDocumentText quota
#: (transactions per second; 1 on a new account).
OCR_MAX_WORKERS: int = int(os.getenv("OCR_MAX_WORKERS", "1"))

#: Resolution of document pages rendered for the evidence viewer (gldb.viewer)
VIEWER_DPI: int = 110

#: Whether to screen document text for prompt attacks with a Bedrock
#: guardrail before any model reads it (gldb.prompt_screen; infra/guardrail.yaml).
PROMPT_SCREEN_ENABLED: bool = _bool_env("PROMPT_SCREEN_ENABLED", False)
#: The guardrail and its pinned, immutable version (stack outputs)
GUARDRAIL_ID: str = os.getenv("GUARDRAIL_ID", "")
GUARDRAIL_VERSION: str = os.getenv("GUARDRAIL_VERSION", "")

#: Whether to run the AI agent team (gldb.ai_team): a Supervisor and specialists
#: on Bedrock that investigate each case and write leads for the analyst.
AGENTS_ENABLED: bool = _bool_env("AGENTS_ENABLED", False)
#: Bedrock model IDs (cross-region inference profiles) for the Supervisor and
#: for the specialists it dispatches
AGENT_SUPERVISOR_MODEL: str = os.getenv("AGENT_SUPERVISOR_MODEL", "global.anthropic.claude-sonnet-4-6")
AGENT_SPECIALIST_MODEL: str = os.getenv(
    "AGENT_SPECIALIST_MODEL", "global.anthropic.claude-haiku-4-5-20251001-v1:0"
)
#: Tool calls one specialist may make before it is stopped (recording a lead
#: or drafting a question does not count)
AGENT_MAX_STEPS: int = int(os.getenv("AGENT_MAX_STEPS", "8"))
#: Longest tool result (characters) handed back to a model
AGENT_MAX_RESULT_CHARS: int = int(os.getenv("AGENT_MAX_RESULT_CHARS", "8000"))
#: Input plus output tokens the whole team may spend on one case
AGENT_TOKEN_BUDGET: int = int(os.getenv("AGENT_TOKEN_BUDGET", "400000"))
#: Output cap per agent call
AGENT_MAX_TOKENS: int = 2048
#: Where the agent evaluation report is kept (the API's controls page shows it)
AGENT_EVAL_PATH: str = os.getenv("AGENT_EVAL_PATH", "logs/agent_eval.json")

#: Path to the append-only officer/pipeline audit log.
AUDIT_LOG_PATH: str = os.getenv("AUDIT_LOG_PATH", "logs/audit_log.jsonl")

#: Path to the usage log: Bedrock tokens and Textract pages per case, never
#: document content (gldb.usage).
USAGE_LOG_PATH: str = os.getenv("USAGE_LOG_PATH", "logs/usage.jsonl")

#: Where `python -m gldb.evaluate --save` keeps the latest evaluation report,
#: which the API's controls page shows (gldb.evaluate).
EVAL_REPORT_PATH: str = os.getenv("EVAL_REPORT_PATH", "logs/evaluation.json")

# ---------------------------------------------------------------------------
# Unit prices for cost estimates (gldb.usage). From the AWS Price List API,
# ap-southeast-1 on-demand list prices, as of PRICES_AS_OF. Estimates only:
# the bill is the record of what was charged.
# ---------------------------------------------------------------------------

PRICES_AS_OF: str = "2026-10-04"

#: Textract DetectDocumentText (SyncTextPagesProcessed), first 1M pages/month
TEXTRACT_USD_PER_PAGE: Decimal = Decimal("0.0015")

#: Bedrock model ID -> (USD per 1M input tokens, USD per 1M output tokens)
MODEL_USD_PER_M_TOKENS: dict[str, tuple[Decimal, Decimal]] = {
    "global.anthropic.claude-sonnet-4-6": (Decimal("3"), Decimal("15")),
    "global.anthropic.claude-haiku-4-5-20251001-v1:0": (Decimal("1"), Decimal("5")),
    "global.anthropic.claude-opus-4-6-v1": (Decimal("5"), Decimal("25")),
    "apac.amazon.nova-pro-v1:0": (Decimal("1.08"), Decimal("4.32")),
}

#: Bedrock Guardrails content filters (incl. prompt attack), per text unit
#: (up to 1,000 characters)
GUARDRAIL_USD_PER_TEXT_UNIT: Decimal = Decimal("0.00015")
