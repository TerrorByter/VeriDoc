"""
gldb/extract.py
---------------
Extraction dispatcher: EXTRACTION_MODE "llm" uses extract_llm (Bedrock),
"stub" uses the deterministic parser for our own generated layout.
"""

from __future__ import annotations

from pathlib import Path

from gldb import extract_llm, extract_stub
from gldb.config import EXTRACTION_MODE
from gldb.schemas import ApplicationManifest, DocMeta, ExtractedPack


def parse_pack(
    case_id: str,
    docs: list[DocMeta],
    pdf_paths: list[Path],
    manifest: ApplicationManifest | None,
    ocr: dict[str, list[str]] | None = None,
) -> ExtractedPack:
    """Parse all documents in a pack with the configured extractor."""
    impl = extract_llm if EXTRACTION_MODE == "llm" else extract_stub
    return impl.parse_pack(case_id, docs, pdf_paths, manifest, ocr)
