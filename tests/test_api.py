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
        "Document Integrity", "Forensics", "Cross-Document", "Registry & Screening",
        "Compliance & Completeness",
    ]
    assert {e["agent"] for e in case["evidence"]} <= set(names)
    fraud = [e for e in case["evidence"] if e["category"] == "fraud_signal"]
    assert all(e["agent"] != "Compliance & Completeness" for e in fraud)


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


def _flags(case: dict[str, Any]) -> list[str]:
    return case["open_flags"]


def test_new_case_reports_status_routing_and_open_flags(client: TestClient) -> None:
    case = _case(client)
    assert case["status"] == "assessed"
    assert case["allowed_actions"] == ["approve", "decline", "escalate", "request_info"]
    assert case["routing"]["path"] == "enhanced_review"
    assert _flags(case) and all(f.startswith("E") for f in _flags(case))


def test_flag_decisions_need_a_reason_code_to_dismiss(client: TestClient) -> None:
    case = _case(client)
    cid, eid = case["case_id"], _flags(case)[0]
    url = f"/api/cases/{cid}/flags/{eid}"
    assert client.post(url, json={"decision": "dismissed"}).status_code == 422
    assert client.post(url, json={"decision": "dismissed", "reason_code": "other"}).status_code == 422
    assert client.post(url, json={"decision": "approved"}).status_code == 422
    assert client.post(f"/api/cases/{cid}/flags/E99", json={"decision": "confirmed"}).status_code == 404
    ok = client.post(url, json={"decision": "dismissed", "reason_code": "ocr_misread"})
    assert ok.status_code == 200
    assert ok.json()["details"]["check_id"]
    assert eid not in _flags(client.get(f"/api/cases/{cid}").json())


def test_approval_waits_until_every_flag_is_decided(client: TestClient) -> None:
    case = _case(client)
    cid = case["case_id"]
    approve = {"decision": "approve", "reason": "looks fine"}
    blocked = client.post(f"/api/cases/{cid}/decision", json=approve)
    assert blocked.status_code == 409 and "undecided" in blocked.json()["detail"]
    for eid in _flags(case):
        client.post(f"/api/cases/{cid}/flags/{eid}", json={"decision": "confirmed"})
    assert client.post(f"/api/cases/{cid}/decision", json=approve).status_code == 200
    final = client.get(f"/api/cases/{cid}").json()
    assert (final["status"], final["allowed_actions"]) == ("approved", [])
    assert client.post(f"/api/cases/{cid}/decision", json=approve).status_code == 409


def test_escalation_needs_a_different_person_as_mlro(client: TestClient) -> None:
    cid = _case(client)["case_id"]
    escalate = {"decision": "escalate", "reason": "possible TBML", "actor": "alice"}
    assert client.post(f"/api/cases/{cid}/decision", json=escalate).status_code == 200
    same = {"decision": "no_str", "reason": "ok", "actor": "alice"}
    refused = client.post(f"/api/cases/{cid}/mlro-decision", json=same)
    assert refused.status_code == 409 and "segregation of duties" in refused.json()["detail"]
    other = {"decision": "str_filed", "reason": "filed with STRO", "actor": "mona"}
    assert client.post(f"/api/cases/{cid}/mlro-decision", json=other).status_code == 200
    assert client.get(f"/api/cases/{cid}").json()["status"] == "mlro_reviewed"
    decline = {"decision": "decline", "reason": "STR filed"}
    assert client.post(f"/api/cases/{cid}/decision", json=decline).status_code == 200

    audit = client.get(f"/api/cases/{cid}/audit").json()
    assert [(e["action"], e["role"]) for e in audit][2:] == [
        ("analyst_decision", "analyst"), ("mlro_decision", "mlro"), ("analyst_decision", "analyst"),
    ]


def test_request_info_then_new_documents_rerun_the_case(client: TestClient, tmp_path: Path) -> None:
    folder = PACKS / "incomplete"
    files = [("files", (p.name, p.read_bytes())) for p in
             [*sorted(folder.glob("*.pdf")), folder / "application.json"]]
    case = client.post("/api/cases", files=files).json()
    cid = case["case_id"]
    assert case["routing"]["path"] == "standard_review"

    april = PACKS / "clean" / "bank_statement_2026-04.pdf"
    upload = [("files", (april.name, april.read_bytes()))]
    assert client.post(f"/api/cases/{cid}/documents", files=upload).status_code == 409  # not requested

    rfi = {"decision": "request_info", "reason": "April statement missing"}
    assert client.post(f"/api/cases/{cid}/decision", json=rfi).status_code == 200
    rerun = client.post(f"/api/cases/{cid}/documents", files=upload)
    assert rerun.status_code == 200
    assert rerun.json()["status"] == "assessed"
    assert "BANK_COVERAGE" not in {e["check_id"] for e in rerun.json()["evidence"]}
    assert client.post(f"/api/cases/{cid}/decision", json=rfi).status_code == 200
    duplicate = client.post(f"/api/cases/{cid}/documents", files=upload)
    assert duplicate.status_code == 409 and "already" in duplicate.json()["detail"]

    actions = [e["action"] for e in client.get(f"/api/cases/{cid}/audit").json()]
    assert actions[:6] == [
        "case_created", "pipeline_completed", "analyst_decision",
        "documents_added", "pipeline_completed", "analyst_decision",
    ]
    assert (tmp_path / "cases" / cid / "result.v1.json").is_file()


def test_feedback_endpoint_reports_overrides(client: TestClient) -> None:
    case = _case(client)
    eid = _flags(case)[0]
    client.post(f"/api/cases/{case['case_id']}/flags/{eid}",
                json={"decision": "dismissed", "reason_code": "duplicate_finding"})
    rows = client.get("/api/feedback").json()
    assert [r["dismissed"] for r in rows] == [1]
    assert rows[0]["reasons"] == {"duplicate_finding": 1}
