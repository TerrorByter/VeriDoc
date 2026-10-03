"""
tests/test_live_aws.py
----------------------
Live AWS smoke tests. Skipped unless RUN_LIVE=1 (set it in .env or the shell).

    RUN_LIVE=1 pytest tests/test_live_aws.py -v

Each test makes one real call, so a failure names the service that is not
set up: credentials/permissions/model access (Bedrock) or Textract.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import gldb.config as config  # noqa: F401  (loads .env before RUN_LIVE is read)
from gldb import llm_client
from gldb.ocr import ocr_files

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call AWS"
)

INVOICE = next(
    (Path(__file__).parent.parent / "data" / "packs" / "clean").glob("invoice_*.pdf")
)


def test_bedrock_converse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert config.BEDROCK_MODEL_ID, "BEDROCK_MODEL_ID is empty in .env"
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    result = llm_client.converse(
        "Reply with the single word OK.", "ping", purpose="live_smoke"
    )
    assert result.text.strip()
    assert result.input_tokens > 0


def test_bedrock_forced_tool_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    result = llm_client.converse(
        "Return the requested value.",
        "The answer is 42.",
        purpose="live_smoke_schema",
        schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )
    assert result.data is not None and "42" in result.data["answer"]


def test_textract_reads_an_invoice_page() -> None:
    pages = ocr_files([INVOICE])[INVOICE.stem]
    assert "INVOICE" in pages[0].upper()
