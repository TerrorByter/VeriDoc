"""
tests/test_api.py
-----------------
API tests via FastAPI's TestClient. Stub extraction, no AWS calls; cases,
uploads and the audit log are redirected to a temp directory.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import NoCredentialsError
from fastapi.testclient import TestClient

from gldb import api, pipeline
from gldb.schemas import AgentTeam, Lead, LeadTest

PACKS = Path(__file__).parent.parent / "data" / "packs"


@pytest.fixture(autouse=True)
def _fresh_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api.jobs, "_runs", {})


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    for module in (api, pipeline):
        monkeypatch.setattr(module, "CASES_DIR", str(tmp_path / "cases"))
    monkeypatch.setattr(api, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("AUDIT_LOG_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("EVAL_REPORT_PATH", str(tmp_path / "evaluation.json"))
    monkeypatch.setenv("AGENT_EVAL_PATH", str(tmp_path / "agent_eval.json"))
    return TestClient(api.app)


def _finished(client: TestClient, response: Any) -> dict[str, Any]:
    """The case a run started. TestClient runs background tasks before
    returning, so the run has finished by the time the response arrives."""
    assert response.status_code == 202, response.text
    cid = response.json()["case_id"]
    assert client.get(f"/api/cases/{cid}/progress").json()["state"] == "done"
    return client.get(f"/api/cases/{cid}").json()


def _case(client: TestClient, pack: str = "tampered") -> dict[str, Any]:
    return _finished(client, client.post(f"/api/cases/from-pack/{pack}"))


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
        "Timeline", "Compliance & Completeness",
    ]
    assert {e["agent"] for e in case["evidence"]} <= set(names)
    fraud = [e for e in case["evidence"] if e["category"] == "fraud_signal"]
    assert all(e["agent"] != "Compliance & Completeness" for e in fraud)


def test_clean_pack_is_low(client: TestClient) -> None:
    assert _case(client, "clean")["risk"]["band"] == "Low"


def test_case_has_timeline_and_investigation_queue(client: TestClient) -> None:
    case = _case(client, "retro_po")
    assert [t["check_id"] for t in case["investigation"]] == ["TEMP_TRADE_SEQUENCE"] * 2
    flagged = [e for e in case["timeline"] if e["evidence_ids"]]
    assert {e["kind"] for e in flagged} == {"invoice", "shipped"}


def test_document_pages_are_served_only_for_documents_in_the_case(client: TestClient) -> None:
    cid = _case(client)["case_id"]
    base = f"/api/cases/{cid}/documents"
    ok = client.get(f"{base}/invoice_INV-1002/pages/1")
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    for bad in ("invoice_INV-1002/pages/0", "invoice_INV-1002/pages/2",
                "nope/pages/1", "..%2F..%2Fgldb%2Fapi/pages/1"):
        assert client.get(f"{base}/{bad}").status_code == 404, bad
    assert client.get("/api/cases/nope/documents/invoice_INV-1002/pages/1").status_code == 404


def test_evidence_location_highlights_its_quote(client: TestClient) -> None:
    case = _case(client)
    cid = case["case_id"]
    hidden = next(e for e in case["evidence"] if e["check_id"] == "FORENSIC_HIDDEN_TEXT")
    loc = client.get(f"/api/cases/{cid}/evidence/{hidden['evidence_id']}/location").json()
    assert (loc["doc_id"], loc["page"], loc["page_count"]) == ("invoice_INV-1002", 1, 1)
    assert loc["boxes"] and loc["highlighted"] == hidden["quote"]

    identity = next(e for e in case["evidence"] if e["check_id"] == "XDOC_IDENTITY")
    loc = client.get(f"/api/cases/{cid}/evidence/{identity['evidence_id']}/location").json()
    assert loc["highlighted"] == identity["observed"] and loc["boxes"]  # no quote: observed value
    assert client.get(f"/api/cases/{cid}/evidence/E99/location").status_code == 404


def test_findings_on_the_application_have_no_page(client: TestClient) -> None:
    case = _case(client, "incomplete")
    cid = case["case_id"]
    applicant = [e for e in case["evidence"] if e["document"] == "pack"]
    assert applicant  # BANK_COVERAGE
    for ev in applicant:
        loc = client.get(f"/api/cases/{cid}/evidence/{ev['evidence_id']}/location").json()
        assert (loc["doc_id"], loc["page_count"], loc["boxes"]) == ("pack", 0, [])


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
    assert _finished(client, client.post("/api/cases", files=files))["risk"]["band"] == "Low"


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
    case = _finished(client, client.post("/api/cases", files=files))
    cid = case["case_id"]
    assert case["routing"]["path"] == "standard_review"

    april = PACKS / "clean" / "bank_statement_2026-04.pdf"
    upload = [("files", (april.name, april.read_bytes()))]
    assert client.post(f"/api/cases/{cid}/documents", files=upload).status_code == 409  # not requested

    rfi = {"decision": "request_info", "reason": "April statement missing"}
    assert client.post(f"/api/cases/{cid}/decision", json=rfi).status_code == 200
    rerun = _finished(client, client.post(f"/api/cases/{cid}/documents", files=upload))
    assert rerun["status"] == "assessed"
    assert "BANK_COVERAGE" not in {e["check_id"] for e in rerun["evidence"]}
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


def test_progress_lists_every_stage_in_run_order(client: TestClient) -> None:
    cid = client.post("/api/cases/from-pack/clean").json()["case_id"]
    progress = client.get(f"/api/cases/{cid}/progress").json()
    assert (progress["state"], progress["current"], progress["error"]) == ("done", None, None)
    assert [s["stage_id"] for s in progress["stages"]] == [*pipeline.ASSESS_STAGES, "record"]
    states = {s["stage_id"]: s["state"] for s in progress["stages"]}
    assert states["ocr"] == "off" and states["agents"] == "ran" and states["record"] == "ran"


def test_progress_tracks_the_stage_running_now() -> None:
    api.jobs.start("CASE-P")
    api.jobs.stage("CASE-P", "intake", "ran")
    api.jobs.stage("CASE-P", "extract", "running")
    progress = api.jobs.get("CASE-P")
    assert progress is not None and (progress.state, progress.current) == ("running", "extract")
    assert [(s.stage_id, s.state) for s in progress.stages] == [("intake", "ran"), ("extract", "running")]


def test_activity_reaches_progress() -> None:
    api.jobs.start("C")
    api.jobs.stage("C", "ai_team", "running")
    api.jobs.detail("C", "Transaction specialist: get_chain")
    progress = api.jobs.get("C")
    assert progress is not None and progress.detail == "Transaction specialist: get_chain"
    api.jobs.stage("C", "ai_team", "ran")
    progress = api.jobs.get("C")
    assert progress is not None and progress.detail is None
    api.jobs.detail("NO-RUN", "ignored")
    assert api.jobs.get("NO-RUN") is None


def test_a_failed_run_reports_why(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_: Any, **__: Any) -> None:
        raise api.ExtractionError("Bedrock unavailable")
    monkeypatch.setattr(api, "run", broken)
    cid = client.post("/api/cases/from-pack/clean").json()["case_id"]
    progress = client.get(f"/api/cases/{cid}/progress").json()
    assert (progress["state"], progress["error"]) == ("failed", "Bedrock unavailable")
    assert client.get(f"/api/cases/{cid}").status_code == 404


def test_an_aws_failure_says_what_to_fix(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def expired(*_: Any, **__: Any) -> None:
        raise NoCredentialsError()
    monkeypatch.setattr(api, "run", expired)
    cid = client.post("/api/cases/from-pack/clean").json()["case_id"]
    error = client.get(f"/api/cases/{cid}/progress").json()["error"]
    assert error.startswith("AWS call failed: Unable to locate credentials")


def test_progress_of_an_unknown_case_is_404(client: TestClient) -> None:
    assert client.get("/api/cases/CASE-NOPE/progress").status_code == 404


def test_progress_survives_a_restart_from_the_stored_result(client: TestClient) -> None:
    cid = _case(client, "clean")["case_id"]
    api.jobs.forget(cid)
    progress = client.get(f"/api/cases/{cid}/progress").json()
    assert progress["state"] == "done" and progress["stages"][-1]["stage_id"] == "record"


def test_case_view_carries_stages_ocr_screen_and_cost(client: TestClient) -> None:
    case = _case(client, "clean")
    assert [s["stage_id"] for s in case["stages"]] == list(pipeline.ASSESS_STAGES)
    assert (case["ocr_confidence"], case["prompt_screen"], case["cost"]) == ({}, None, None)
    assert case["company_name"] == "Lumen Harbour Trading Pte. Ltd."
    tampered = _case(client)
    assert tampered["signals"]["FORENSIC_HIDDEN_TEXT"]["name"] == "Hidden text in document"


def test_cases_are_listed_newest_first(client: TestClient) -> None:
    first = _case(client, "clean")["case_id"]
    second = _case(client, "tampered")["case_id"]
    rows = client.get("/api/cases").json()
    assert [r["case_id"] for r in rows] == [second, first]
    top = rows[0]
    assert (top["band"], top["path"], top["status"]) == ("High", "enhanced_review", "assessed")
    assert top["company_name"] and top["doc_count"] > 0 and top["created_at"]


def test_controls_show_versions_capabilities_and_signals(client: TestClient) -> None:
    controls = client.get("/api/controls").json()
    assert controls["policy_version"] == api.POLICY_VERSION
    assert controls["rule_version"] == api.RULE_VERSION
    assert {c["name"]: c["enabled"] for c in controls["capabilities"]}["ocr"] is False
    assert len(controls["signals"]) == len(api.SIGNALS)
    assert controls["evaluation"] is None  # no saved report in the temp folder


def test_controls_list_ai_team_capability(client: TestClient) -> None:
    controls = client.get("/api/controls").json()
    team = {c["name"]: c for c in controls["capabilities"]}["ai_team"]
    assert team["enabled"] is False  # the offline suite forces AGENTS_ENABLED=false
    assert team["detail"] == f"{api.AGENT_SUPERVISOR_MODEL} + {api.AGENT_SPECIALIST_MODEL}"
    assert controls["agent_eval"] == []  # no saved agent evaluation in the temp folder


def test_controls_show_the_saved_agent_evaluation(client: TestClient, tmp_path: Path) -> None:
    row = {
        "evaluated_at": "2026-10-04T00:00:00+00:00", "supervisor": "sup", "specialist": "spec",
        "packs": [], "pattern_recall": 1.0, "triage_accuracy": 0.5, "noise": 1,
        "withheld_rate": 0.25, "usd_per_case": "0.42", "seconds_per_case": 12.5,
    }
    (tmp_path / "agent_eval.json").write_text(json.dumps([row]), encoding="utf-8")
    rows = client.get("/api/controls").json()["agent_eval"]
    assert [(r["supervisor"], r["specialist"], r["pattern_recall"]) for r in rows] == [("sup", "spec", 1.0)]


def test_case_view_has_ai_team_and_no_trace(client: TestClient) -> None:
    cid = _case(client)["case_id"]
    test = LeadTest(cites=["R01"], expected="payer: the buyer", observed="Paid by a third party")
    lead = Lead(agent="transaction", kind="triage", hypothesis="Odd payment", tests=[test],
                investigation_status="supported", evidence_status="corroborated")
    team = AgentTeam(status="ran", leads=[lead], trace={"R01": "PAGE TEXT"})
    pipeline._persist(api._load(cid).model_copy(update={"ai_team": team}))
    response = client.get(f"/api/cases/{cid}")
    assert response.status_code == 200
    (sent,) = response.json()["ai_team"]["leads"]
    assert (sent["hypothesis"], sent["investigation_status"], sent["evidence_status"]) == (
        "Odd payment", "supported", "corroborated")
    assert sent["tests"] == [{"cites": ["R01"], "expected": "payer: the buyer", "observed": "Paid by a third party"}]
    assert "PAGE TEXT" not in response.text and "trace" not in response.json()["ai_team"]


def test_progress_reports_activity_detail(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def team_run(case_id: str, *_: Any, on_stage: Any, on_activity: Any, **__: Any) -> None:
        on_stage("ai_team", "running")
        on_activity("Transaction specialist: get_chain")
    monkeypatch.setattr(api, "run", team_run)
    cid = client.post("/api/cases/from-pack/clean").json()["case_id"]
    progress = client.get(f"/api/cases/{cid}/progress").json()
    assert progress["detail"] == "Transaction specialist: get_chain"


def test_old_result_with_review_still_loads(client: TestClient) -> None:
    """A result stored before the AI team replaced semantic review: its
    "review" and "semantic_review" stage report are ignored."""
    cid = _case(client, "clean")["case_id"]
    path = Path(api.CASES_DIR) / cid / "result.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["review"] = {"observations": [], "questions": [], "source": "template", "rejected": 0}
    stored["stages"].append({"stage_id": "semantic_review", "status": "ran", "duration_ms": 1, "version": ""})
    path.write_text(json.dumps(stored), encoding="utf-8")
    response = client.get(f"/api/cases/{cid}")
    assert response.status_code == 200
    assert response.json()["ai_team"] is None
    assert "semantic_review" not in [s["stage_id"] for s in response.json()["stages"]]
