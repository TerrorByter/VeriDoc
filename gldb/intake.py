"""
gldb/intake.py
--------------
Intake stage: file validation, hashing, metadata extraction, text extraction,
and document classification.

Rules:
- validate_file raises IntakeError for bad extension, missing PDF magic bytes,
  or oversized files.
- sha256_file returns a 64-char hex digest.
- parse_pdf_date parses Adobe PDF date strings to timezone-aware datetimes.
  Never raises; returns None on bad/missing input.
- read_text returns per-page text via pdfplumber; pages with no text layer
  are filled from OCR text when the pipeline's OCR stage supplied it.
- classify inspects the first page only; forms take priority over generic types.
- intake() orchestrates all of the above and returns a list of DocMeta.
- load_manifest reads application.json from a folder and parses it as
  ApplicationManifest, returning None if the file is absent.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pdfplumber
import pypdf

from gldb.config import MAX_FILE_MB
from gldb.forensics import hidden_reasons
from gldb.schemas import ApplicationManifest, DocMeta, DocType


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IntakeError(Exception):
    """Raised by validate_file when a file fails intake validation."""


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


def validate_file(path: "Path | str") -> None:
    """Validate *path* as an acceptable PDF upload.

    Raises IntakeError unless ALL three conditions pass:
    1. Extension is .pdf (case-insensitive).
    2. File starts with the PDF magic bytes b"%PDF".
    3. File size is within MAX_FILE_MB megabytes.
    """
    p = Path(path)

    # 1 — extension check
    if p.suffix.lower() != ".pdf":
        raise IntakeError(
            f"Unsupported file type: {p.suffix!r}. Only .pdf files are accepted."
        )

    # 2 — magic bytes check
    try:
        with p.open("rb") as fh:
            header = fh.read(4)
    except OSError as exc:
        raise IntakeError(f"Cannot read file {p.name}: {exc}") from exc

    if header != b"%PDF":
        raise IntakeError(
            f"{p.name} does not appear to be a valid PDF (missing %PDF header)."
        )

    # 3 — size check
    size_mb = p.stat().st_size / (1024 * 1024)
    if size_mb > MAX_FILE_MB:
        raise IntakeError(
            f"{p.name} is {size_mb:.1f} MB, which exceeds the {MAX_FILE_MB} MB limit."
        )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def sha256_file(path: "Path | str") -> str:
    """Return the hex-encoded SHA-256 digest of *path* (64 characters)."""
    p = Path(path)
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# PDF date parsing
# ---------------------------------------------------------------------------

# Matches: D:YYYYMMDDHHmmSS followed by an optional timezone:
#   Z                   (UTC)
#   +HH'mm'  or -HH'mm'
#   +HHmm    or -HHmm   (without apostrophes)
#   (nothing)           (no timezone info — treated as UTC)
_PDF_DATE_RE = re.compile(
    r"^D:(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})"
    r"(?P<tz>Z|[+-]\d{2}'?\d{2}'?)?$"
)


def parse_pdf_date(s: "str | None") -> "datetime | None":
    """Parse an Adobe PDF date string to a timezone-aware datetime.

    Handles variants:
    - ``D:YYYYMMDDHHmmSS+HH'mm'``
    - ``D:YYYYMMDDHHmmSS-HH'mm'``
    - ``D:YYYYMMDDHHmmSS+HHmm``
    - ``D:YYYYMMDDHHmmSSZ``
    - ``D:YYYYMMDDHHmmSS`` (no offset — assumed UTC)

    Returns ``None`` on missing or malformed input. Never raises.
    """
    if not s:
        return None
    s = s.strip()
    m = _PDF_DATE_RE.match(s)
    if not m:
        return None

    try:
        year, month, day, hour, minute, second = (int(m.group(i)) for i in range(1, 7))
        tz_str: str | None = m.group("tz")

        if not tz_str or tz_str == "Z":
            tz = timezone.utc
        else:
            sign = 1 if tz_str[0] == "+" else -1
            # Strip apostrophes: "+08'00'" → "0800"
            digits = re.sub(r"[^0-9]", "", tz_str)
            tz_hours = int(digits[:2])
            tz_minutes = int(digits[2:4]) if len(digits) >= 4 else 0
            offset = timedelta(hours=tz_hours, minutes=tz_minutes) * sign
            tz = timezone(offset)

        return datetime(year, month, day, hour, minute, second, tzinfo=tz)
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------


def _visible(page: pdfplumber.page.Page) -> pdfplumber.page.Page:
    """*page* without its hidden characters."""
    return page.filter(
        lambda obj: obj["object_type"] != "char"
        or not hidden_reasons(obj, page.width, page.height)
    )


def read_text(
    path: "Path | str", visible_only: bool = False, ocr: list[str] | None = None
) -> list[str]:
    """Return per-page text from *path* via pdfplumber.

    With *visible_only*, characters a reader cannot see (white, tiny or
    off-page text) are dropped. Text bound for an LLM should be read this way
    so hidden instructions never reach the model.

    Returns a list of strings, one per page. A page with no extractable text
    (a scanned page) takes its *ocr* text when given, else an empty string.
    """
    with pdfplumber.open(str(path)) as pdf:
        pages = [
            (_visible(page) if visible_only else page).extract_text() or ""
            for page in pdf.pages
        ]
    if ocr:
        pages = [t if t.strip() else seen for t, seen in zip(pages, ocr)]
    return pages


def model_view(path: "Path | str", ocr: list[str] | None = None) -> list[str]:
    """The text of *path* a model is given: visible characters only, with
    OCR for scanned pages. The prompt attack screen checks exactly this."""
    return read_text(path, visible_only=True, ocr=ocr)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def contains_quote(pages: list[str], quote: str) -> bool:
    """Whether *quote* appears in *pages*, ignoring differences in spacing
    and line breaks (pdfplumber and OCR wrap lines differently)."""
    return _squash(quote) in _squash(" ".join(pages))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

# Form keywords take priority; must be checked before generic document types.
_FORM_KEYWORDS: list[tuple[str, DocType]] = [
    ("FATCA & CRS Self-Certification Form (For Entity)", "fatca_crs_entity"),
    ("SELF CERTIFICATION ON TAX RESIDENCY (INDIVIDUAL) FORM", "fatca_crs_individual"),
]

_DOC_KEYWORDS: list[tuple[str, DocType]] = [
    ("TAX INVOICE", "invoice"),
    ("STATEMENT OF ACCOUNT", "bank_statement"),
    ("FINANCIAL STATEMENTS SUMMARY", "financial_statements"),
    ("PURCHASE ORDER", "purchase_order"),
    ("BILL OF LADING", "bill_of_lading"),
]


def classify(text: list[str]) -> DocType:
    """Classify a document by inspecting the first page of *text*.

    *text* is the output of read_text() — a list of per-page strings.
    Returns a DocType literal.  Classification is substring-based on the
    first page only.  Forms are checked before generic document types.
    Falls back to ``"unknown"`` when no keyword matches.
    """
    if not text:
        return "unknown"

    first_page = text[0]

    # Forms first (higher specificity)
    for keyword, doc_type in _FORM_KEYWORDS:
        if keyword in first_page:
            return doc_type

    # Generic document types
    for keyword, doc_type in _DOC_KEYWORDS:
        if keyword in first_page:
            return doc_type

    return "unknown"


# ---------------------------------------------------------------------------
# Intake orchestration
# ---------------------------------------------------------------------------


def intake(
    case_id: str,
    file_paths: "list[Path | str]",
    ocr: dict[str, list[str]] | None = None,
) -> list[DocMeta]:
    """Validate, hash, and classify each file in *file_paths*.

    Steps per file:
    1. validate_file — raises IntakeError on failure.
    2. sha256_file — SHA-256 hex digest.
    3. pypdf.PdfReader — page count, creation date, mod date, producer.
    4. classify(read_text(path)) — document type.
    5. Build and return DocMeta.

    Args:
        case_id: The case identifier (used for audit/logging; not stored in DocMeta).
        file_paths: Paths to the uploaded PDF files.
        ocr: Optional doc_id -> OCR page text from the OCR stage, so scanned
            documents can be classified.

    Returns:
        A list of DocMeta objects, one per file.

    Raises:
        IntakeError: If any file fails validation.
    """
    docs: list[DocMeta] = []

    for raw_path in file_paths:
        path = Path(raw_path)

        # 1. Validate
        validate_file(path)

        # 2. Hash
        sha256 = sha256_file(path)

        # 3. PDF metadata
        reader = pypdf.PdfReader(str(path))
        page_count: int = len(reader.pages)
        metadata = reader.metadata or {}
        creation_date: datetime | None = parse_pdf_date(
            str(metadata.get("/CreationDate", "")) or None
        )
        mod_date: datetime | None = parse_pdf_date(
            str(metadata.get("/ModDate", "")) or None
        )
        producer_raw = metadata.get("/Producer")
        producer: str | None = str(producer_raw) if producer_raw is not None else None

        # 4. doc_id and classification
        doc_id = path.stem
        doc_type: DocType = classify(read_text(path, ocr=(ocr or {}).get(doc_id)))

        # 5. Build DocMeta
        docs.append(
            DocMeta(
                doc_id=doc_id,
                filename=path.name,
                sha256=sha256,
                doc_type=doc_type,
                page_count=page_count,
                creation_date=creation_date,
                mod_date=mod_date,
                producer=producer,
            )
        )

    return docs


# ---------------------------------------------------------------------------
# Application manifest loader
# ---------------------------------------------------------------------------


def load_manifest(folder: "Path | str") -> ApplicationManifest | None:
    """Read and parse ``application.json`` from *folder*.

    Returns an ApplicationManifest if the file exists and is valid.
    Returns None if the file does not exist.
    Propagates Pydantic validation errors if the JSON is malformed.
    """
    manifest_path = Path(folder) / "application.json"
    if not manifest_path.exists():
        return None
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    return ApplicationManifest.model_validate(raw)
