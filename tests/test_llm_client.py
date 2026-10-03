"""
tests/test_llm_client.py
------------------------
Unit tests for gldb/llm_client.py. A fake bedrock-runtime client is injected,
so no AWS call is made.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from gldb import llm_client


class FakeBedrock:
    def __init__(self, content: list[dict[str, Any]]) -> None:
        self.content = content
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        return {
            "output": {"message": {"content": self.content}},
            "usage": {"inputTokens": 11, "outputTokens": 7},
        }


@pytest.fixture(autouse=True)
def _model_and_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(llm_client, "BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("LLM_USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))


def test_text_call_uses_temperature_zero_and_logs_tokens_only(tmp_path: Any) -> None:
    fake = FakeBedrock([{"text": "hello"}])
    result = llm_client.converse("sys", "SECRET DOC TEXT", purpose="t", client=fake)

    assert result.text == "hello"
    assert result.data is None
    assert fake.requests[0]["inferenceConfig"]["temperature"] == 0
    assert "toolConfig" not in fake.requests[0]

    log = (tmp_path / "usage.jsonl").read_text()
    record = json.loads(log)
    assert record["input_tokens"] == 11 and record["output_tokens"] == 7
    assert "SECRET" not in log


def test_schema_forces_tool_and_returns_data() -> None:
    fake = FakeBedrock([{"toolUse": {"name": "emit", "input": {"total": "10.00"}}}])
    schema = {"type": "object", "properties": {"total": {"type": "string"}}}
    result = llm_client.converse("s", "u", purpose="t", schema=schema, client=fake)

    assert result.data == {"total": "10.00"}
    assert fake.requests[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "emit"}}


def test_missing_model_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_client, "BEDROCK_MODEL_ID", "")
    with pytest.raises(RuntimeError):
        llm_client.converse("s", "u", purpose="t", client=FakeBedrock([]))


def test_prompt_version_is_a_content_hash() -> None:
    v = llm_client.prompt_version("system prompt", "schema")
    assert len(v) == 12 and v == llm_client.prompt_version("system prompt", "schema")
    assert v != llm_client.prompt_version("system prompt.", "schema")
