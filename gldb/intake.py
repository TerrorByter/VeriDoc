"""
gldb/intake.py
--------------
Intake stage: file validation, hashing, metadata extraction, text extraction,
and document classification.

Rules:
- validate_file raises IntakeError for a bad extension, a file that does not
  start like its extension says (PDF or .xlsx), or an oversized file.
- sha256_file returns a 64-char hex digest.
- parse_pdf_date parses Adobe PDF date strings to timezone-aware datetimes.
  Never raises; returns None on bad/missing input.
- read_text returns per-page text via pdfplumber; pages with no text layer
  are filled from OCR text when the pipeline's OCR stage supplied it. For a
  workbook it returns one text per sheet.
- classify inspects the first page only; forms take priority over generic types.
- document_paths lists the documents (PDFs and workbooks) in a folder.
- intake() orchestrates all of the above and returns a list of DocMeta. A
  workbook's sheets count as its pages, and a workbook is classified by its
  sheets.
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
from gldb.forensics import form_values, hidden_chars
from gldb.schemas import ApplicationManifest, DocMeta, DocType
from gldb.workbook import (
    WorkbookError,
    is_workbook,
    properties,
    read_workbook,
    workbook_text,
)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IntakeError(Exception):
    """Raised by validate_file when a file fails intake validation."""


# ---------------------------------------------------------------------------
# File validation
# ---------------------------------------------------------------------------


#: Accepted file types and the bytes each starts with (an .xlsx is a zip)
FILE_TYPES: dict[str, bytes] = {".pdf": b"%PDF", ".xlsx": b"PK\x03\x04"}


def validate_file(path: "Path | str") -> None:
    """Validate *path* as an acceptable PDF or workbook upload.

    Raises IntakeError unless ALL three conditions pass:
    1. Extension is .pdf or .xlsx (case-insensitive).
    2. File starts with the magic bytes of its extension.
    3. File size is within MAX_FILE_MB megabytes.
    """
    p = Path(path)

    # 1 — extension check
    magic = FILE_TYPES.get(p.suffix.lower())
    if magic is None:
        raise IntakeError(
            f"Unsupported file type: {p.suffix!r}. Only {' and '.join(FILE_TYPES)} files are accepted."
        )

    # 2 — magic bytes check
    try:
        with p.open("rb") as fh:
            header = fh.read(len(magic))
    except OSError as exc:
        raise IntakeError(f"Cannot read file {p.name}: {exc}") from exc

    if header != magic:
        raise IntakeError(
            f"{p.name} does not appear to be a valid {p.suffix.lstrip('.').upper()} file (wrong file header)."
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
    hidden = {id(char) for char, _ in hidden_chars(page)}
    return page.filter(lambda obj: obj["object_type"] != "char" or id(obj) not in hidden)


def read_text(
    path: "Path | str", visible_only: bool = False, ocr: list[str] | None = None
) -> list[str]:
    """Return per-page text from *path* via pdfplumber.

    With *visible_only*, characters a reader cannot see (white, tiny or
    off-page text) are dropped. Text bound for an LLM should be read this way
    so hidden instructions never reach the model.

    Returns a list of strings, one per page. A page with no extractable text
    (a scanned page) takes its *ocr* text when given, else an empty string. A
    workbook has one text per sheet (see gldb.workbook.workbook_text).
    """
    if is_workbook(path):
        return workbook_text(path)
    with pdfplumber.open(str(path)) as pdf:
        pages = [
            (_visible(page) if visible_only else page).extract_text() or ""
            for page in pdf.pages
        ]
    if ocr:
        pages = [t if t.strip() else seen for t, seen in zip(pages, ocr)]
    return pages


def _with_form_values(path: "Path | str", pages: list[str]) -> list[str]:
    """*pages* with the values of each page's form fields added: a fillable
    form keeps its figures there and only its labels in the text."""
    if is_workbook(path):
        return pages
    return ["\n".join(filter(None, (text, values))) for text, values in zip(pages, form_values(path))]


def model_view(path: "Path | str", ocr: list[str] | None = None) -> list[str]:
    """The text of *path* a model is given: visible characters only, with
    OCR for scanned pages, and the values in a fillable form's fields. The
    prompt attack screen checks exactly this."""
    return _with_form_values(path, read_text(path, visible_only=True, ocr=ocr))


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
    # After the purchase order, whose terms ask for the order number on the delivery order
    ("DELIVERY ORDER", "delivery_order"),
    ("NOTICE OF ASSESSMENT", "notice_of_assessment"),
    ("CORPORATE COMPLIANCE AND FINANCIAL PROFILE", "acra_profile"),
]


#: The FATCA/CRS forms. They are read field by field and carry tax identifiers,
#: so no OCR, no model and no forensic text checks see them.
FORM_DOC_TYPES: frozenset[DocType] = frozenset({"fatca_crs_entity", "fatca_crs_individual"})


def classify(text: list[str]) -> DocType:
    """Classify a document by inspecting the first page of *text*.

    *text* is the output of read_text() — a list of per-page strings.
    Returns a DocType literal.  Classification is case-insensitive and
    substring-based on the first page only.  Forms are checked before generic
    document types.
    Falls back to ``"unknown"`` when no keyword matches.
    """
    if not text:
        return "unknown"

    first_page = text[0].casefold()

    # Forms first (higher specificity)
    for keyword, doc_type in _FORM_KEYWORDS:
        if keyword.casefold() in first_page:
            return doc_type

    # Generic document types
    for keyword, doc_type in _DOC_KEYWORDS:
        if keyword.casefold() in first_page:
            return doc_type

    return "unknown"


#: Sheets a workbook of financial statements has
_STATEMENT_SHEETS = {"income statement", "balance sheet"}


def classify_workbook(path: "Path | str") -> DocType:
    """A workbook with an income statement and a balance sheet is the
    applicant's financial statements; any other workbook is unknown."""
    names = {sheet.name.strip().casefold() for sheet in read_workbook(path)}
    return "financial_statements" if _STATEMENT_SHEETS <= names else "unknown"


# ---------------------------------------------------------------------------
# Intake orchestration
# ---------------------------------------------------------------------------


def document_paths(folder: "Path | str") -> list[Path]:
    """The documents (PDFs and workbooks) in *folder*, in name order."""
    return sorted(p for p in Path(folder).iterdir() if p.suffix.lower() in FILE_TYPES)


def _file_metadata(path: Path) -> tuple[int, datetime | None, datetime | None, str | None]:
    """(page count, creation date, modification date, producer) of a PDF or workbook."""
    if is_workbook(path):
        props = properties(path)
        return len(read_workbook(path)), props.created, props.modified, props.application
    reader = pypdf.PdfReader(str(path))
    metadata = reader.metadata or {}
    producer = metadata.get("/Producer")
    return (
        len(reader.pages),
        parse_pdf_date(str(metadata.get("/CreationDate", "")) or None),
        parse_pdf_date(str(metadata.get("/ModDate", "")) or None),
        str(producer) if producer is not None else None,
    )


def intake(
    case_id: str,
    file_paths: "list[Path | str]",
    ocr: dict[str, list[str]] | None = None,
) -> list[DocMeta]:
    """Validate, hash, and classify each file in *file_paths*.

    Steps per file:
    1. validate_file — raises IntakeError on failure.
    2. sha256_file — SHA-256 hex digest.
    3. pypdf.PdfReader — page count, creation date, mod date, producer (for a
       workbook: its sheet count and its own properties).
    4. classify(read_text(path)) — document type (a workbook: by its sheets).
    5. Build and return DocMeta.

    Args:
        case_id: The case identifier (used for audit/logging; not stored in DocMeta).
        file_paths: Paths to the uploaded PDF and workbook files.
        ocr: Optional doc_id -> OCR page text from the OCR stage, so scanned
            documents can be classified.

    Returns:
        A list of DocMeta objects, one per file.

    Raises:
        IntakeError: If any file fails validation or cannot be read.
    """
    docs: list[DocMeta] = []

    for raw_path in file_paths:
        path = Path(raw_path)

        # 1. Validate
        validate_file(path)

        # 2. Hash
        sha256 = sha256_file(path)

        # 3. Metadata
        try:
            page_count, creation_date, mod_date, producer = _file_metadata(path)
            # 4. doc_id and classification
            doc_id = path.stem
            doc_type: DocType = (
                classify_workbook(path) if is_workbook(path)
                else classify(read_text(path, ocr=(ocr or {}).get(doc_id)))
            )
        except WorkbookError as exc:
            raise IntakeError(str(exc)) from exc

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
