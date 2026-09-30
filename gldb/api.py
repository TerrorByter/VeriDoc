"""
gldb/api.py
-----------
HTTP API for the React frontend. A thin layer over pipeline.run() and the
audit log: it holds no business logic.

Run:  uvicorn gldb.api:app --reload

Rules:
- CaseView deliberately omits the raw ExtractedPack (it holds unmasked
  personal identifiers); the browser only ever sees masked Evidence.
- The system never approves or rejects a loan: flag and final decisions are
  the officer's, recorded as audit events.
- There is no authentication in this prototype; the actor is "officer".
"""

from __future__ import annotations

import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from gldb.audit import log_event, read_events
from gldb.config import CASES_DIR, PACKS_DIR, UPLOADS_DIR
from gldb.extract_llm import ExtractionError
from gldb.intake import IntakeError
from gldb.pipeline import run
from gldb.schemas import (
    AgentReport,
    AuditEvent,
    CaseResult,
    DocMeta,
    Evidence,
    ReadinessResult,
    RiskResult,
)

OFFICER = "officer"
_FLAG_ACTION: dict[str, Literal["flag_confirmed", "flag_dismissed"]] = {
    "confirmed": "flag_confirmed",
    "dismissed": "flag_dismissed",
}
_CASE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

app = FastAPI(title="GLDB Loan Document Assessment (prototype)")


class CaseView(BaseModel):
    """What the browser sees of a CaseResult."""

    case_id: str
    docs: list[DocMeta]
    evidence: list[Evidence]
    agent_reports: list[AgentReport]
    risk: RiskResult
    readiness: ReadinessResult | None
    summary: str | None
    extraction_mode: str


class FlagDecision(BaseModel):
    decision: Literal["confirmed", "dismissed"]
    reason: str = ""


class FinalDecision(BaseModel):
    decision: Literal["approved", "rejected", "more_info_requested"]
    reason: str


def _view(result: CaseResult) -> CaseView:
    return CaseView(
        case_id=result.case_id,
        docs=result.pack.docs,
        evidence=result.evidence,
        agent_reports=result.agent_reports,
        risk=result.risk,
        readiness=result.readiness,
        summary=result.summary,
        extraction_mode=result.pack.extraction_mode,
    )


def _new_case_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"CASE-{ts}-{secrets.token_hex(2)}"


def _load(case_id: str) -> CaseResult:
    if not _CASE_ID.fullmatch(case_id):
        raise HTTPException(404, "Case not found")
    path = Path(CASES_DIR) / case_id / "result.json"
    if not path.is_file():
        raise HTTPException(404, "Case not found")
    return CaseResult.model_validate_json(path.read_text(encoding="utf-8"))


def _run(case_id: str, files: list[Path], manifest: Path | None) -> CaseView:
    try:
        return _view(run(case_id, files, manifest_path=manifest, actor=OFFICER))
    except IntakeError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/packs")
def list_packs() -> list[str]:
    """Names of the synthetic demo packs."""
    return sorted(p.name for p in Path(PACKS_DIR).iterdir() if p.is_dir())


@app.post("/api/cases/from-pack/{name}")
def create_case_from_pack(name: str) -> CaseView:
    if name not in list_packs():
        raise HTTPException(404, "Pack not found")
    folder = Path(PACKS_DIR) / name
    manifest = folder / "application.json"
    return _run(
        _new_case_id(),
        sorted(folder.glob("*.pdf")),
        manifest if manifest.is_file() else None,
    )


@app.post("/api/cases")
def create_case(files: list[UploadFile] = File(...)) -> CaseView:
    """Upload PDFs plus an optional application.json (the ApplicationManifest)."""
    case_id = _new_case_id()
    folder = Path(UPLOADS_DIR) / case_id
    folder.mkdir(parents=True)
    for upload in files:
        with (folder / Path(upload.filename or "upload").name).open("wb") as out:
            shutil.copyfileobj(upload.file, out)
    manifest = folder / "application.json"
    return _run(
        case_id,
        sorted(p for p in folder.iterdir() if p != manifest),
        manifest if manifest.is_file() else None,
    )


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> CaseView:
    return _view(_load(case_id))


@app.get("/api/cases/{case_id}/audit")
def get_audit(case_id: str) -> list[AuditEvent]:
    _load(case_id)
    return read_events(case_id)


@app.post("/api/cases/{case_id}/flags/{evidence_id}")
def decide_flag(case_id: str, evidence_id: str, body: FlagDecision) -> AuditEvent:
    if evidence_id not in {e.evidence_id for e in _load(case_id).evidence}:
        raise HTTPException(404, "Evidence not found")
    event = AuditEvent(
        case_id=case_id,
        actor=OFFICER,
        action=_FLAG_ACTION[body.decision],
        evidence_id=evidence_id,
        decision=body.decision,
        reason=body.reason or None,
    )
    log_event(event)
    return event


@app.post("/api/cases/{case_id}/decision")
def final_decision(case_id: str, body: FinalDecision) -> AuditEvent:
    _load(case_id)
    event = AuditEvent(
        case_id=case_id,
        actor=OFFICER,
        action="final_decision",
        decision=body.decision,
        reason=body.reason,
    )
    log_event(event)
    return event
