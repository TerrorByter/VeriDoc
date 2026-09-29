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

#: Whether to run the optional LLM plain-English explanation step.
EXPLAIN_ENABLED: bool = _bool_env("EXPLAIN_ENABLED", False)

#: Path to the append-only officer/pipeline audit log.
AUDIT_LOG_PATH: str = os.getenv("AUDIT_LOG_PATH", "logs/audit_log.jsonl")

#: Path to the LLM usage log.
LLM_USAGE_LOG_PATH: str = os.getenv("LLM_USAGE_LOG_PATH", "logs/llm_usage.jsonl")
