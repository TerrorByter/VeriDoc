"""
tests/test_live_aws.py
----------------------
Live AWS smoke tests. Skipped unless RUN_LIVE=1 (set it in .env or the shell).

    RUN_LIVE=1 pytest tests/test_live_aws.py -v

Each test makes one real call (the AI team test, one run of the whole team),
so a failure names the service that is not set up: credentials/permissions/
model access (Bedrock) or Textract.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import gldb.config as config  # noqa: F401  (loads .env before RUN_LIVE is read)
from gldb import llm_client
from gldb.ai_team.team import TeamModels, run_team
from gldb.ai_team.tools import CaseData
from gldb.intake import load_manifest
from gldb.ocr import ocr_files
from gldb.pipeline import assess

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call AWS"
)

PACKS = Path(__file__).parent.parent / "data" / "packs"
INVOICE = next((PACKS / "clean").glob("invoice_*.pdf"))


def test_bedrock_converse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert config.BEDROCK_MODEL_ID, "BEDROCK_MODEL_ID is empty in .env"
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    result = llm_client.converse(
        "Reply with the single word OK.", "ping", purpose="live_smoke"
    )
    assert result.text.strip()
    assert result.input_tokens > 0


def test_bedrock_forced_tool_schema(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
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
    pages = ocr_files([INVOICE]).text[INVOICE.stem]
    assert "INVOICE" in pages[0].upper()


def test_live_agent_team_runs_on_bedrock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("USAGE_LOG_PATH", str(tmp_path / "usage.jsonl"))
    folder = PACKS / "third_party_payment"
    result = assess(
        folder.name, sorted(folder.glob("*.pdf")), load_manifest(folder), team=None
    )
    # The offline suite runs the prompt screen off; {} marks the pages as
    # screened (nothing quarantined) so the agents can read page text
    pack = result.pack.model_copy(update={"prompt_screen": {}})
    team = run_team(
        CaseData(result.case_id, pack, result.evidence),
        TeamModels(config.AGENT_SUPERVISOR_MODEL, config.AGENT_SPECIALIST_MODEL),
    )
    assert team.status == "ran"
    assert team.tool_calls
    unreceived = "cites a tool result it did not receive"
    assert not [
        lead.lead_id
        for lead in team.leads
        if (lead.withheld_reason or "").startswith(unreceived)
    ]
