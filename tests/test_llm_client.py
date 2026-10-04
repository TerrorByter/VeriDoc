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
from gldb.config import AGENT_MAX_TOKENS


class FakeBedrock:
    def __init__(self, content: list[dict[str, Any]]) -> None:
        self.content = content
        self.requests: list[dict[str, Any]] = []

    def converse(self, **request: Any) -> dict[str, Any]:
        self.requests.append(request)
        tool_use = any("toolUse" in block for block in self.content)
        return {
            "output": {"message": {"content": self.content}},
            "stopReason": "tool_use" if tool_use else "end_turn",
            "usage": {"inputTokens": 11, "outputTokens": 7},
        }


@pytest.fixture(autouse=True)
def _model_and_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setattr(llm_client, "BEDROCK_MODEL_ID", "test-model")
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))


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


def test_converse_turn_is_portable_and_metered(tmp_path: Any) -> None:
    fake = FakeBedrock([{"toolUse": {"toolUseId": "t1", "name": "get_chain", "input": {"invoice_number": "INV-1002"}}}])
    tools = [llm_client.tool_spec("get_chain", "d", {"type": "object"})]
    turn = llm_client.converse_turn("m-1", "sys", [{"role": "user", "content": [{"text": "go"}]}],
                                    tools=tools, purpose="ai_team_transaction", case_id="C1", client=fake)
    req = fake.requests[0]
    assert req["modelId"] == "m-1" and req["inferenceConfig"] == {"maxTokens": AGENT_MAX_TOKENS}
    assert "toolChoice" not in req["toolConfig"] and "temperature" not in req["inferenceConfig"]
    assert turn.tool_uses == [{"toolUseId": "t1", "name": "get_chain", "input": {"invoice_number": "INV-1002"}}]
    assert turn.stop_reason == "tool_use"
    record = json.loads((tmp_path / "usage.jsonl").read_text())
    assert record["purpose"] == "ai_team_transaction" and record["model_id"] == "m-1" and record["case_id"] == "C1"


def test_tool_spec_wraps_the_schema() -> None:
    assert llm_client.tool_spec("n", "d", {"type": "object"}) == {
        "toolSpec": {"name": "n", "description": "d", "inputSchema": {"json": {"type": "object"}}}
    }


def test_turn_exposes_text_and_token_counts() -> None:
    fake = FakeBedrock([{"text": "all "}, {"text": "clear"}])
    turn = llm_client.converse_turn("m-1", "sys", [], tools=[llm_client.tool_spec("n", "d", {"type": "object"})],
                                    purpose="ai_team_supervisor", case_id="C1", client=fake)
    assert turn.text == "all clear" and turn.tool_uses == [] and turn.stop_reason == "end_turn"
    assert (turn.input_tokens, turn.output_tokens) == (11, 7)
    assert turn.content == [{"text": "all "}, {"text": "clear"}]
