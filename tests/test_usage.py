"""
tests/test_usage.py
-------------------
Usage metering and cost per case (gldb.usage). Fake AWS clients only.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gldb import llm_client, ocr, usage

PACKS = Path(__file__).parent.parent / "data" / "packs"
SONNET = "global.anthropic.claude-sonnet-4-6"


@pytest.fixture(autouse=True)
def _log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "usage.jsonl"
    monkeypatch.setenv("USAGE_LOG_PATH", str(path))
    return path


def test_cost_per_case_prices_tokens_and_pages() -> None:
    usage.log_usage("bedrock", "extract_invoice", "CASE-A", model_id=SONNET,
                    input_tokens=1000, output_tokens=500)
    usage.log_usage("textract", "ocr", "CASE-A", pages=10)
    usage.log_usage("textract", "ocr", "CASE-B", pages=2)
    rows = {r.case_id: r for r in usage.report(usage.read_usage())}
    a = rows["CASE-A"]
    assert (a.pages, a.input_tokens, a.output_tokens) == (10, 1000, 500)
    # 1000 x $3/M + 500 x $15/M + 10 x $0.0015
    assert a.usd == Decimal("0.0255")
    assert rows["CASE-B"].usd == Decimal("0.003")


def test_unknown_model_is_reported_unpriced_not_free() -> None:
    usage.log_usage("bedrock", "explain_case", "CASE-C", model_id="some-new-model",
                    input_tokens=10, output_tokens=10)
    (row,) = usage.report(usage.read_usage())
    assert row.usd is None and row.unpriced == ["some-new-model"]


def test_old_records_without_case_or_service_still_count() -> None:
    old = {"ts": "2026-10-03T00:00:00+00:00", "purpose": "extract_invoice",
           "model_id": "arn:aws:bedrock:ap-southeast-1:1:inference-profile/" + SONNET,
           "input_tokens": 1_000_000, "output_tokens": 0}
    (row,) = usage.report([old])
    assert (row.case_id, row.usd) == (usage.UNATTRIBUTED, Decimal("3"))


class FakeTextract:
    def detect_document_text(self, **_: Any) -> dict[str, Any]:
        return {"Blocks": [{"BlockType": "LINE", "Text": "x", "Confidence": 99.0}]}


def test_ocr_meters_pages_per_case() -> None:
    ocr.ocr_files([PACKS / "clean" / "invoice_INV-1001.pdf"], case_id="CASE-D", client=FakeTextract())
    (record,) = usage.read_usage()
    assert (record["service"], record["case_id"], record["pages"]) == ("textract", "CASE-D", 1)


def test_bedrock_call_is_metered_against_its_case(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBedrock:
        def converse(self, **_: Any) -> dict[str, Any]:
            return {"output": {"message": {"content": [{"text": "ok"}]}},
                    "usage": {"inputTokens": 3, "outputTokens": 4}}

    monkeypatch.setattr(llm_client, "BEDROCK_MODEL_ID", SONNET)
    llm_client.converse("s", "u", purpose="t", case_id="CASE-E", client=FakeBedrock())
    (record,) = usage.read_usage()
    assert (record["service"], record["case_id"], record["input_tokens"]) == ("bedrock", "CASE-E", 3)


def test_cost_is_split_by_pipeline_stage() -> None:
    usage.log_usage("textract", "ocr", "CASE-D", pages=10)
    usage.log_usage("guardrails", "prompt_screen", "CASE-D", text_units=10)
    usage.log_usage("bedrock", "extract_invoice", "CASE-D", model_id=SONNET,
                    input_tokens=1000, output_tokens=0)
    usage.log_usage("bedrock", "extract_bank_statement", "CASE-D", model_id=SONNET,
                    input_tokens=1000, output_tokens=0)
    (row,) = usage.report(usage.read_usage())
    assert row.usd_by_stage == {
        "ocr": Decimal("0.015"), "prompt_screen": Decimal("0.0015"), "extract": Decimal("0.006"),
    }


def test_case_cost_finds_one_case() -> None:
    usage.log_usage("textract", "ocr", "CASE-E", pages=2)
    cost = usage.case_cost("CASE-E")
    assert cost is not None and cost.usd == Decimal("0.003")
    assert usage.case_cost("CASE-NONE") is None
