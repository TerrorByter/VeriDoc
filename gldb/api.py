"""
gldb/api.py
-----------
HTTP API for the React frontend. A thin layer over pipeline.run() and the
audit log: it holds no business logic.

Run:  uvicorn gldb.api:app --reload

Rules:
- CaseView deliberately omits the raw ExtractedPack (it holds unmasked
  personal identifiers); the browser only ever sees masked Evidence.
- The system never approves, declines or reports a case: every decision is a
  person's (analyst or MLRO), recorded as an audit event.
- Case status is derived from the audit log by gldb.lifecycle, which also
  decides which actions are allowed; an illegal action returns 409.
- Each case keeps its documents in UPLOADS_DIR/<case_id>/, so documents can
  re-enter the case after a request for information.
- Document pages are served as images for the evidence viewer: the one
  place the browser sees unmasked document content. In production this needs
  access control and an audit entry per document viewed.
- There is no authentication in this prototype: the actor name is taken from
  the request. In production it comes from single sign-on, and the role
  (analyst, MLRO) from the user's group.
"""

from __future__ import annotations

import json
import re
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, model_validator

from gldb import lifecycle
from gldb.audit import log_event, read_all_events, read_events
from gldb.checks import PACK_DOC
from gldb.config import CASES_DIR, PACKS_DIR, UPLOADS_DIR
from gldb.extract_llm import ExtractionError
from gldb.feedback import CheckFeedback, report
from gldb.intake import IntakeError
from gldb.pipeline import OCR_LINES_FILE, run
from gldb.viewer import locate, render_page
from gldb.schemas import (
    AgentReport,
    AuditEvent,
    Box,
    CaseResult,
    CaseStatus,
    DocMeta,
    Evidence,
    InvestigationTask,
    OcrLine,
    ReadinessResult,
    ReasonCode,
    RiskResult,
    Routing,
    SemanticReview,
    TimelineEvent,
    TransactionChain,
)

SYSTEM = "system"
MANIFEST = "application.json"
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
    routing: Routing | None
    chains: list[TransactionChain]
    timeline: list[TimelineEvent]
    investigation: list[InvestigationTask]
    review: SemanticReview | None
    extraction_mode: str
    status: CaseStatus
    allowed_actions: list[str]
    #: Fraud flags from the latest assessment not yet confirmed or dismissed
    open_flags: list[str]


class FlagDecision(BaseModel):
    decision: Literal["confirmed", "dismissed"]
    #: Required to dismiss; "other" also needs a note
    reason_code: ReasonCode | None = None
    note: str = ""
    actor: str = "analyst"

    @model_validator(mode="after")
    def _dismissal_is_explained(self) -> "FlagDecision":
        if self.decision == "dismissed" and self.reason_code is None:
            raise ValueError("dismissing a flag needs a reason_code")
        if self.reason_code == "other" and not self.note.strip():
            raise ValueError("reason_code 'other' needs a note")
        return self


class AnalystDecision(BaseModel):
    decision: Literal["approve", "decline", "request_info", "escalate"]
    reason: str = Field(min_length=1)
    actor: str = "analyst"


class MlroDecision(BaseModel):
    #: Whether a suspicious transaction report is filed with STRO
    decision: Literal["str_filed", "no_str"]
    reason: str = Field(min_length=1)
    actor: str = "mlro"


class EvidenceLocation(BaseModel):
    """Where a finding is in its document. doc_id is PACK_DOC (and
    page_count 0) for a finding about the application data, which has no page."""

    evidence_id: str
    doc_id: str
    page: int
    page_count: int
    #: The text highlighted: the quote, else the observed value
    highlighted: str | None
    boxes: list[Box]


def _view(result: CaseResult) -> CaseView:
    events = read_events(result.case_id)
    status = lifecycle.status(events)
    return CaseView(
        case_id=result.case_id,
        docs=result.pack.docs,
        evidence=result.evidence,
        agent_reports=result.agent_reports,
        risk=result.risk,
        readiness=result.readiness,
        routing=result.routing,
        chains=result.chains,
        timeline=result.timeline,
        investigation=result.investigation,
        review=result.review,
        extraction_mode=result.pack.extraction_mode,
        status=status,
        allowed_actions=lifecycle.allowed_actions(status),
        open_flags=lifecycle.open_flags(result.evidence, events),
    )


def _require(case_id: str, action: str) -> list[AuditEvent]:
    """The case's events, after checking *action* is allowed (409 if not)."""
    events = read_events(case_id)
    try:
        lifecycle.require(lifecycle.status(events), action)
    except lifecycle.TransitionError as exc:
        raise HTTPException(409, str(exc)) from exc
    return events


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


def _case_folder(case_id: str) -> Path:
    return Path(UPLOADS_DIR) / case_id


def _save(folder: Path, files: list[UploadFile]) -> list[str]:
    """Store uploads in *folder*; a file already in the case is refused (409)
    so a submitted document is never silently replaced."""
    names = [Path(upload.filename or "upload").name for upload in files]
    clash = [n for n in names if (folder / n).exists()]
    if clash:
        raise HTTPException(409, f"already in the case: {', '.join(clash)}")
    for name, upload in zip(names, files):
        with (folder / name).open("wb") as out:
            shutil.copyfileobj(upload.file, out)
    return names


def _run(case_id: str, new_case: bool = True) -> CaseView:
    """Assess every document in the case folder."""
    folder = _case_folder(case_id)
    manifest = folder / MANIFEST
    files = sorted(p for p in folder.iterdir() if p != manifest)
    try:
        result = run(
            case_id, files, manifest_path=manifest if manifest.is_file() else None,
            actor=SYSTEM, new_case=new_case,
        )
    except IntakeError as exc:
        raise HTTPException(422, str(exc)) from exc
    except ExtractionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return _view(result)


@app.get("/api/packs")
def list_packs() -> list[str]:
    """Names of the synthetic demo packs."""
    return sorted(p.name for p in Path(PACKS_DIR).iterdir() if p.is_dir())


@app.post("/api/cases/from-pack/{name}")
def create_case_from_pack(name: str) -> CaseView:
    """Open a case from a demo pack: its files are copied into the case, as
    an upload would be, so the case can later take more documents."""
    if name not in list_packs():
        raise HTTPException(404, "Pack not found")
    case_id = _new_case_id()
    folder = _case_folder(case_id)
    folder.mkdir(parents=True)
    source = Path(PACKS_DIR) / name
    for path in [*source.glob("*.pdf"), source / MANIFEST]:
        if path.is_file():
            shutil.copy2(path, folder / path.name)
    return _run(case_id)


@app.post("/api/cases")
def create_case(files: list[UploadFile] = File(...)) -> CaseView:
    """Upload PDFs plus an optional application.json (the ApplicationManifest)."""
    case_id = _new_case_id()
    folder = _case_folder(case_id)
    folder.mkdir(parents=True)
    _save(folder, files)
    return _run(case_id)


@app.get("/api/cases/{case_id}")
def get_case(case_id: str) -> CaseView:
    return _view(_load(case_id))


@app.get("/api/cases/{case_id}/audit")
def get_audit(case_id: str) -> list[AuditEvent]:
    _load(case_id)
    return read_events(case_id)


@app.post("/api/cases/{case_id}/flags/{evidence_id}")
def decide_flag(case_id: str, evidence_id: str, body: FlagDecision) -> AuditEvent:
    """Confirm or dismiss one finding. Dismissals carry a reason code, which
    the feedback report aggregates per check."""
    evidence = {e.evidence_id: e for e in _load(case_id).evidence}
    if evidence_id not in evidence:
        raise HTTPException(404, "Evidence not found")
    if not lifecycle.allowed_actions(lifecycle.status(read_events(case_id))):
        raise HTTPException(409, "the case is closed")
    event = AuditEvent(
        case_id=case_id,
        actor=body.actor,
        role="analyst",
        action=_FLAG_ACTION[body.decision],
        evidence_id=evidence_id,
        decision=body.decision,
        reason_code=body.reason_code,
        reason=body.note or None,
        details={"check_id": evidence[evidence_id].check_id},
    )
    log_event(event)
    return event


@app.post("/api/cases/{case_id}/decision")
def analyst_decision(case_id: str, body: AnalystDecision) -> AuditEvent:
    """Approve, decline, request information, or escalate to the MLRO.
    Approval waits until every fraud flag has been confirmed or dismissed."""
    result = _load(case_id)
    events = _require(case_id, body.decision)
    if body.decision == "approve":
        undecided = lifecycle.open_flags(result.evidence, events)
        if undecided:
            raise HTTPException(409, f"undecided flags: {', '.join(undecided)}")
    event = AuditEvent(
        case_id=case_id,
        actor=body.actor,
        role="analyst",
        action="analyst_decision",
        decision=body.decision,
        reason=body.reason,
    )
    log_event(event)
    return event


@app.post("/api/cases/{case_id}/mlro-decision")
def mlro_decision(case_id: str, body: MlroDecision) -> AuditEvent:
    """The MLRO decides whether to file a suspicious transaction report.
    Maker-checker: the MLRO cannot be the analyst who escalated."""
    _load(case_id)
    events = _require(case_id, "mlro_decision")
    if body.actor == lifecycle.escalated_by(events):
        raise HTTPException(
            409, "segregation of duties: the MLRO must not be the analyst who escalated"
        )
    event = AuditEvent(
        case_id=case_id,
        actor=body.actor,
        role="mlro",
        action="mlro_decision",
        decision=body.decision,
        reason=body.reason,
    )
    log_event(event)
    return event


@app.post("/api/cases/{case_id}/documents")
def add_documents(case_id: str, files: list[UploadFile] = File(...)) -> CaseView:
    """Documents answering a request for information: added to the case,
    which is then re-assessed on its full document set."""
    _load(case_id)
    _require(case_id, "documents_added")
    names = _save(_case_folder(case_id), files)
    log_event(AuditEvent(
        case_id=case_id, actor=SYSTEM, action="documents_added", details={"filenames": names},
    ))
    return _run(case_id, new_case=False)


def _document(result: CaseResult, doc_id: str, page: int) -> tuple[Path, DocMeta]:
    """The case's stored file for *doc_id*; 404 unless the document belongs
    to the case and has *page*."""
    doc = next((d for d in result.pack.docs if d.doc_id == doc_id), None)
    if doc is None or not 1 <= page <= doc.page_count:
        raise HTTPException(404, "Page not found")
    path = _case_folder(result.case_id) / doc.filename
    if not path.is_file():
        raise HTTPException(404, "Page not found")
    return path, doc


def _ocr_lines(case_id: str, doc_id: str, page: int) -> list[OcrLine]:
    """The OCR lines of one page, if the case was OCR'd."""
    path = Path(CASES_DIR) / case_id / OCR_LINES_FILE
    if not path.is_file():
        return []
    pages = json.loads(path.read_text(encoding="utf-8")).get(doc_id, [])
    return [OcrLine.model_validate(line) for line in pages[page - 1]] if page <= len(pages) else []


@app.get("/api/cases/{case_id}/documents/{doc_id}/pages/{page}")
def get_page(case_id: str, doc_id: str, page: int) -> Response:
    """One page of a case document, as PNG."""
    path, _ = _document(_load(case_id), doc_id, page)
    return Response(render_page(path, page), media_type="image/png")


@app.get("/api/cases/{case_id}/evidence/{evidence_id}/location")
def get_evidence_location(case_id: str, evidence_id: str) -> EvidenceLocation:
    """Where a finding is: its document and page, and boxes around its quote
    (or, without a quote, its observed value) when the page has a text layer."""
    result = _load(case_id)
    ev = next((e for e in result.evidence if e.evidence_id == evidence_id), None)
    if ev is None:
        raise HTTPException(404, "Evidence not found")
    if ev.document == PACK_DOC:
        return EvidenceLocation(evidence_id=evidence_id, doc_id=PACK_DOC, page=0,
                                page_count=0, highlighted=None, boxes=[])
    path, doc = _document(result, ev.document, ev.page)
    lines = _ocr_lines(result.case_id, doc.doc_id, ev.page)
    for text in (ev.quote, ev.observed):
        if text and (boxes := locate(path, ev.page, text, lines)):
            return EvidenceLocation(evidence_id=evidence_id, doc_id=doc.doc_id, page=ev.page,
                                    page_count=doc.page_count, highlighted=text, boxes=boxes)
    return EvidenceLocation(evidence_id=evidence_id, doc_id=doc.doc_id, page=ev.page,
                            page_count=doc.page_count, highlighted=None, boxes=[])


@app.get("/api/feedback")
def get_feedback() -> list[CheckFeedback]:
    """Override statistics per check, from the whole audit log."""
    return report(read_all_events())
