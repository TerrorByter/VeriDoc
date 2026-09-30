"""
gldb/ocr.py
-----------
AWS Textract OCR for scanned PDFs (pages with no text layer).

Textract's synchronous API takes one page per call, so the PDF is split into
single-page PDFs with pypdf. Returns per-page text in reading order, the same
shape as intake.read_text().
"""

from __future__ import annotations

import io
from functools import cache
from pathlib import Path
from typing import Any

import boto3
import pypdf

from gldb.config import AWS_REGION


@cache
def _default_client() -> Any:
    return boto3.client("textract", region_name=AWS_REGION)


def _page_bytes(page: pypdf.PageObject) -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_page(page)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def ocr_pages(path: Path | str, client: Any | None = None) -> list[str]:
    """OCR every page of the PDF at *path* with Textract DetectDocumentText.

    Args:
        path: PDF file to OCR.
        client: Injectable textract client (tests). Defaults to boto3.
    """
    textract = client or _default_client()
    reader = pypdf.PdfReader(str(path))
    texts: list[str] = []
    for page in reader.pages:
        response = textract.detect_document_text(
            Document={"Bytes": _page_bytes(page)}
        )
        lines = [
            b["Text"] for b in response["Blocks"] if b["BlockType"] == "LINE"
        ]
        texts.append("\n".join(lines))
    return texts
