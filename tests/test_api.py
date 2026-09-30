"""
tests/test_api.py
-----------------
API tests via FastAPI's TestClient. Stub extraction, no AWS calls; cases,
uploads and the audit log are redirected to a temp directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from gldb import api, pipeline

PACKS = Path(__file__).parent.parent / "data" / "packs"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    for module in (api, pipeline):
        monkeypatch.setattr(module, "CASES_DIR", str(tmp_path / "cases"))
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    return TestClient(api.app)


def _case(client: TestClient, pack: str = "tampered") -> dict[str, Any]:
    response = client.post(f"/api/cases/from-pack/{pack}")
    assert response.status_code == 200
    return response.json()


def test_list_packs(client: TestClient) -> None:
    assert {"clean", "tampered", "incomplete"} <= set(client.get("/api/packs").json())


def test_tampered_pack_is_high_and_pack_is_not_exposed(client: TestClient) -> None:
    case = _case(client)
    assert case["risk"]["band"] == "High"
    assert "pack" not in case and "entity_self_certs" not in case
    assert client.get(f"/api/cases/{case['case_id']}").json() == case


def test_case_reports_each_agent_and_tags_evidence(client: TestClient) -> None:
    case = _case(client)
    names = [a["name"] for a in case["agent_reports"]]
    assert names == [
        "Document Integrity", "Forensics", "Cross-Document", "Compliance & Completeness"
    ]
    assert {e["agent"] for e in case["evidence"]} <= set(names)
    fraud = [e for e in case["evidence"] if e["category"] == "fraud_signal"]
    assert all(e["agent"] in names[:3] for e in fraud)


def test_clean_pack_is_low(client: TestClient) -> None:
    assert _case(client, "clean")["risk"]["band"] == "Low"


def test_unknown_pack_and_bad_case_ids_are_404(client: TestClient) -> None:
    assert client.post("/api/cases/from-pack/nope").status_code == 404
    assert client.get("/api/cases/does-not-exist").status_code == 404
    assert client.get("/api/cases/..%2F..%2Fetc").status_code == 404


def test_upload_runs_pipeline_with_uploaded_manifest(client: TestClient) -> None:
    folder = PACKS / "clean"
    files = [
        ("files", (p.name, p.read_bytes()))
        for p in [*sorted(folder.glob("*.pdf")), folder / "application.json"]
    ]
    response = client.post("/api/cases", files=files)
    assert response.status_code == 200
    assert response.json()["risk"]["band"] == "Low"


def test_upload_rejects_non_pdf(client: TestClient) -> None:
    response = client.post("/api/cases", files=[("files", ("evil.exe", b"MZ"))])
    assert response.status_code == 422


def test_flag_and_final_decisions_are_audited(client: TestClient) -> None:
    case = _case(client)
    cid = case["case_id"]
    evidence_id = case["evidence"][0]["evidence_id"]

    ok = client.post(
        f"/api/cases/{cid}/flags/{evidence_id}",
        json={"decision": "confirmed", "reason": "matches bank"},
    )
    assert ok.status_code == 200
    assert client.post(
        f"/api/cases/{cid}/flags/E99", json={"decision": "dismissed"}
    ).status_code == 404
    assert client.post(
        f"/api/cases/{cid}/flags/{evidence_id}", json={"decision": "approved"}
    ).status_code == 422
    assert client.post(
        f"/api/cases/{cid}/decision", json={"decision": "rejected", "reason": "tampering"}
    ).status_code == 200

    actions = [e["action"] for e in client.get(f"/api/cases/{cid}/audit").json()]
    assert actions == [
        "case_created", "pipeline_completed", "flag_confirmed", "final_decision"
    ]
