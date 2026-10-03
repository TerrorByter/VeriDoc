"""
gldb/ocr.py
-----------
AWS Textract OCR: what a reader sees on each page.

Textract's synchronous API takes one page per call, so the PDF is split into
single-page PDFs with pypdf. Returns per-page text in reading order, the same
shape as intake.read_text().

ocr_files() is the pipeline's OCR stage: every page of every document, so
scans can be read and the Forensics agent can compare the text layer with
what is visible. Fillable forms (AcroForms) are skipped: they are read
deterministically and their tax identifiers stay on our side.
"""

from __future__ import annotations

import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import boto3
import pypdf
from botocore.config import Config

from gldb.config import AWS_REGION, OCR_MAX_WORKERS

#: Adaptive retries slow the client down when Textract throttles, so a burst
#: above the account's rate quota waits instead of failing the case.
_RETRY = Config(retries={"mode": "adaptive", "max_attempts": 10})


@cache
def _default_client() -> Any:
    return boto3.client("textract", region_name=AWS_REGION, config=_RETRY)


def _page_bytes(page: pypdf.PageObject) -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


#: Confidence reported for a page with no text: there is nothing to misread
_BLANK_PAGE_CONFIDENCE = 100.0


@dataclass(frozen=True)
class OcrResult:
    """doc_id (file stem) -> per-page text, and per-page mean line confidence
    (0-100) so the router can send poorly read cases to an analyst."""

    text: dict[str, list[str]] = field(default_factory=dict)
    confidence: dict[str, list[float]] = field(default_factory=dict)


def _ocr_bytes(textract: Any, page_pdf: bytes) -> tuple[str, float]:
    response = textract.detect_document_text(Document={"Bytes": page_pdf})
    lines = [b for b in response["Blocks"] if b["BlockType"] == "LINE"]
    confidence = (
        sum(b["Confidence"] for b in lines) / len(lines) if lines else _BLANK_PAGE_CONFIDENCE
    )
    return "\n".join(b["Text"] for b in lines), confidence


def ocr_files(paths: list[Path], client: Any | None = None) -> OcrResult:
    """OCR every page of every non-form PDF with Textract DetectDocumentText.

    Pages are split sequentially (pypdf readers are not thread-safe); only the
    Textract calls run concurrently.

    Args:
        paths: PDF files to OCR.
        client: Injectable textract client (tests). Defaults to boto3.
    """
    textract = client or _default_client()
    jobs: list[tuple[str, bytes]] = []
    for path in paths:
        reader = pypdf.PdfReader(str(path))
        if not reader.get_fields():
            jobs.extend((path.stem, _page_bytes(page)) for page in reader.pages)
    with ThreadPoolExecutor(max_workers=OCR_MAX_WORKERS) as pool:
        pages = list(pool.map(lambda job: _ocr_bytes(textract, job[1]), jobs))
    result = OcrResult()
    for (doc_id, _), (text, confidence) in zip(jobs, pages):
        result.text.setdefault(doc_id, []).append(text)
        result.confidence.setdefault(doc_id, []).append(round(confidence, 1))
    return result
