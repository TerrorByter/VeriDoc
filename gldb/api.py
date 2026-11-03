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
- Starting a run (new case, demo pack, or documents after a request for
  information) answers 202 with the case ID at once; the pipeline runs in the
  background and GET /api/cases/{id}/progress says which stage it is on
  (gldb.jobs).
- Document pages are served as images for the evidence viewer: the one
  place the browser sees unmasked document content. In production this needs
  access control and an audit entry per document viewed.
- There is no authentication in this prototype: the actor name is taken from
  the request. In production it comes from single sign-on, and the role
  (analyst, MLRO) from the user's group.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from botocore.exceptions import BotoCoreError, ClientError
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, model_validator

from gldb import agent_eval, jobs, lifecycle, usage
from gldb.ai_team.checks import subject_locations
from gldb.agent_eval import AgentEvalRow
from gldb.audit import log_event, read_all_events, read_events
from gldb.checks import PACK_DOC
from gldb.config import (
    AGENT_SPECIALIST_MODEL,
    AGENT_SUPERVISOR_MODEL,
    AGENTS_ENABLED,
    BAND_LOW_MAX,
    BAND_MEDIUM_MAX,
    BEDROCK_MODEL_ID,
    CASES_DIR,
    EXTRACTION_MODE,
    GUARDRAIL_ID,
    GUARDRAIL_VERSION,
    HARD_FLAG_CHECK_IDS,
    OCR_ENABLED,
    PACKS_DIR,
    POLICY_VERSION,
    PROMPT_SCREEN_ENABLED,
    QA_SAMPLE_PERCENT,
    SEVERITY_WEIGHTS,
    SHADOW_SCORE_ENABLED,
    SHADOW_SCORER_MODEL,
    UPLOADS_DIR,
)
from gldb.evaluate import Report, load_saved
from gldb.rfi import RequestDraft, asked_findings, draft_request
from gldb.scoring import HARD_RULE_IDS
from gldb.extract_llm import ExtractionError
from gldb.feedback import CheckFeedback, report
from gldb.intake import IntakeError, document_paths, intake, load_manifest, validate_file
from gldb.jobs import Progress, StageProgress
from gldb.pipeline import ASSESS_STAGES, OCR_LINES_FILE, run
from gldb.reference import versions as reference_versions
from gldb.privacy import mask_id
from gldb.signal_library import RULE_VERSION, SIGNALS, signal_for
from gldb.usage import CaseCost
from gldb.viewer import locate, render_page
from gldb.schemas import (
    AgentTeam,
    AuditEvent,
    Box,
    CaseResult,
    CaseStatus,
    DocMeta,
    DocRef,
    DocType,
    Evidence,
    GateDecision,
    InvestigationTask,
    OcrLine,
    OfficialRecords,
    PromptAttackHit,
    ReadinessResult,
    ReasonCode,
    ReviewPath,
    RiskResult,
    Routing,
    RuleGroupReport,
    Severity,
    ShadowScore,
    Signal,
    StageReport,
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

log = logging.getLogger(__name__)

app = FastAPI(title="GLDB Loan Document Assessment (prototype)")


class CaseView(BaseModel):
    """What the browser sees of a CaseResult."""

    case_id: str
    #: The applicant company, from the application data, else ACRA's record
    company_name: str | None
    docs: list[DocMeta]
    evidence: list[Evidence]
    rule_group_reports: list[RuleGroupReport]
    risk: RiskResult
    readiness: ReadinessResult | None
    routing: Routing | None
    chains: list[TransactionChain]
    timeline: list[TimelineEvent]
    investigation: list[InvestigationTask]
    ai_team: AgentTeam | None
    #: What a model would route the case to, and what the disposition gate would do: logged only
    shadow: ShadowScore | None
    gate: GateDecision | None
    extraction_mode: str
    status: CaseStatus
    allowed_actions: list[str]
    #: Fraud flags from the latest assessment not yet confirmed or dismissed
    open_flags: list[str]
    stages: list[StageReport]
    #: What ACRA and IRAS hold for the case, every NRIC/FIN masked; None when not fetched
    official: OfficialRecords | None = None
    #: OCR confidence per document page (no text); empty when OCR was off
    ocr_confidence: dict[str, list[float]]
    #: Prompt attack hits per screened document; None when the screen was off
    prompt_screen: dict[str, list[PromptAttackHit]] | None
    #: Estimated AWS cost of the case; None when it used no AWS service
    cost: CaseCost | None
    #: check_id -> signal (name and ID) for every check with a finding
    signals: dict[str, Signal]
    #: Bank rows (T01...), invoice numbers and counterparties (P01...) -> the document page they are on
    subjects: dict[str, DocRef]
    #: Findings the request to the applicant already asks about: an AI question resting only on them adds nothing
    asked_findings: list[str] = []


class RunStarted(BaseModel):
    """A pipeline run accepted: follow it at /api/cases/{case_id}/progress."""

    case_id: str


class CaseSummary(BaseModel):
    """One row of the case list."""

    case_id: str
    company_name: str | None
    doc_count: int
    band: str | None
    score: int | None
    path: str | None
    status: CaseStatus | Literal["running", "failed"]
    #: When the case was opened (ISO 8601); empty if the run has not logged it yet
    created_at: str


class Capability(BaseModel):
    """An optional pipeline capability and whether this server has it on."""

    name: Literal["ocr", "prompt_screen", "llm_extraction", "ai_team", "shadow_score"]
    enabled: bool
    detail: str


class CostSummary(BaseModel):
    cases: int
    usd_total: Decimal
    #: Mean over cases with a cost; None before any case used AWS
    usd_per_case: Decimal | None


class ScoringPolicy(BaseModel):
    """How a finding becomes points and a route: what the controls page states
    so a reader can see how much each finding counts without the code."""

    #: Points per fraud finding by its severity; each check scores once per document
    severity_points: dict[str, int]
    band_low_max: int
    band_medium_max: int
    #: Checks that force band High at high or critical severity
    forces_high: list[str]
    #: Checks that send a case to enhanced review whatever its score
    forces_enhanced_review: list[str]
    #: Share of fast-track cases an analyst samples
    qa_sample_percent: int


class Controls(BaseModel):
    """How the pipeline is governed: versions, capabilities, the signal
    library, spend, the latest evaluation and analyst override statistics."""

    policy_version: str
    rule_version: str
    reference_data: dict[str, str]
    capabilities: list[Capability]
    scoring: ScoringPolicy
    signals: list[Signal]
    cost: CostSummary
    #: None until `python -m gldb.evaluate --save` has run
    evaluation: Report | None
    #: Saved AI team evaluations, one per model setup; empty until one has run
    agent_eval: list[AgentEvalRow]
    feedback: list[CheckFeedback]


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


class PageFinding(BaseModel):
    """A finding on one page, with what the overlay shows of it: boxes around
    its quote (or observed value), empty when its text cannot be placed."""

    evidence_id: str
    check_id: str
    title: str
    severity: Severity
    explanation: str
    observed: str | None
    boxes: list[Box]


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


def _masked(records: OfficialRecords | None) -> OfficialRecords | None:
    """*records* with every NRIC/FIN masked, for the browser."""
    if records is None:
        return None
    return records.model_copy(update={
        "companies": {
            uen: c.model_copy(update={"officers": [o.model_copy(update={"id_number": mask_id(o.id_number)})
                                                   for o in c.officers]}) if c else None
            for uen, c in records.companies.items()
        },
        "notices": [n.model_copy(update={"id_number": mask_id(n.id_number)}) for n in records.notices],
        "no_notice": [mask_id(i) for i in records.no_notice],
    })


def _company_name(result: CaseResult) -> str | None:
    if result.pack.manifest is not None:
        return result.pack.manifest.company_name
    record = result.pack.official.applicant() if result.pack.official else None
    return record.company_name if record else None


def _view(result: CaseResult) -> CaseView:
    events = read_events(result.case_id)
    status = lifecycle.status(events)
    return CaseView(
        case_id=result.case_id,
        company_name=_company_name(result),
        docs=result.pack.docs,
        evidence=result.evidence,
        rule_group_reports=result.rule_group_reports,
        risk=result.risk,
        readiness=result.readiness,
        routing=result.routing,
        chains=result.chains,
        timeline=result.timeline,
        investigation=result.investigation,
        ai_team=result.ai_team,
        shadow=result.shadow,
        gate=result.gate,
        extraction_mode=result.pack.extraction_mode,
        status=status,
        allowed_actions=lifecycle.allowed_actions(status, _edd(result)),
        open_flags=lifecycle.open_flags(result.evidence, events),
        stages=result.stages,
        official=_masked(result.pack.official),
        ocr_confidence=result.pack.ocr_confidence,
        prompt_screen=result.pack.prompt_screen,
        cost=usage.case_cost(result.case_id),
        signals={e.check_id: signal_for(e.check_id) for e in result.evidence},
        subjects={k: DocRef(doc_id=doc, page=page) for k, (doc, page) in subject_locations(result.pack).items()},
        asked_findings=sorted(asked_findings(result)),
    )


def _edd(result: CaseResult) -> bool:
    """Whether the case's latest assessment put it on the enhanced review path."""
    return result.routing is not None and result.routing.edd_required


def _require(result: CaseResult, action: str) -> list[AuditEvent]:
    """The case's events, after checking *action* is allowed (409 if not)."""
    events = read_events(result.case_id)
    try:
        lifecycle.require(lifecycle.status(events), action, _edd(result))
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


def _execute(case_id: str, files: list[Path], manifest: Path | None, new_case: bool) -> None:
    """Run the pipeline in the background, reporting each stage to gldb.jobs."""
    try:
        run(case_id, files, manifest_path=manifest, actor=SYSTEM, new_case=new_case,
            on_stage=lambda stage, state: jobs.stage(case_id, stage, state),
            on_activity=lambda text: jobs.detail(case_id, text))
    except (IntakeError, ExtractionError) as exc:
        jobs.fail(case_id, str(exc))
    except (BotoCoreError, ClientError) as exc:
        # Expired sign-in, missing permission, throttling: say so, it is fixable
        log.exception("AWS call failed for %s", case_id)
        jobs.fail(case_id, f"AWS call failed: {exc}")
    except Exception:
        log.exception("pipeline run failed for %s", case_id)
        jobs.fail(case_id, "unexpected error; see the server log")
    else:
        jobs.finish(case_id)


def _start(case_id: str, background: BackgroundTasks, new_case: bool = True) -> RunStarted:
    """Assess every document in the case folder in the background. Files are
    validated first, so an unreadable upload is refused (422) before the run."""
    folder = _case_folder(case_id)
    manifest = folder / MANIFEST
    files = sorted(p for p in folder.iterdir() if p != manifest)
    try:
        for path in files:
            validate_file(path)
    except IntakeError as exc:
        raise HTTPException(422, str(exc)) from exc
    jobs.start(case_id)
    background.add_task(_execute, case_id, files, manifest if manifest.is_file() else None, new_case)
    return RunStarted(case_id=case_id)


class PackInfo(BaseModel):
    name: str
    #: What the pack shows, from its ground truth; None where it says nothing
    note: str | None = None
    #: The route its ground truth expects; None where it gives none
    expected_path: ReviewPath | None = None


def _pack_names() -> list[str]:
    return sorted(p.name for p in Path(PACKS_DIR).iterdir() if p.is_dir())


def _pack_info(folder: Path) -> PackInfo:
    path = folder / "ground_truth.json"
    truth = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return PackInfo(name=folder.name, note=truth.get("note"), expected_path=truth.get("expected_path"))


@app.get("/api/packs")
def list_packs() -> list[PackInfo]:
    """The synthetic demo packs, each with what it shows and the route it should take."""
    return [_pack_info(Path(PACKS_DIR) / name) for name in _pack_names()]


@app.post("/api/cases/from-pack/{name}", status_code=202)
def create_case_from_pack(name: str, background: BackgroundTasks) -> RunStarted:
    """Open a case from a demo pack: its files are copied into the case, as
    an upload would be, so the case can later take more documents."""
    if name not in _pack_names():
        raise HTTPException(404, "Pack not found")
    case_id = _new_case_id()
    folder = _case_folder(case_id)
    folder.mkdir(parents=True)
    source = Path(PACKS_DIR) / name
    for path in [*document_paths(source), source / MANIFEST]:
        if path.is_file():
            shutil.copy2(path, folder / path.name)
    return _start(case_id, background)


class Identified(BaseModel):
    """What one file is, read the way a run reads it."""

    filename: str
    #: "application" for the application data (application.json)
    doc_type: DocType | Literal["application"]
    #: Why a run could not use the file; None when it can
    error: str | None = None


@app.post("/api/documents/identify")
def identify_document(file: UploadFile = File(...)) -> Identified:
    """Identify one file before a case is opened, so the upload page can say
    which required documents are still missing. Nothing is kept. A scan with
    no text reads as unknown here; a run with OCR on reads it."""
    name = Path(file.filename or "upload").name
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / name
        with path.open("wb") as out:
            shutil.copyfileobj(file.file, out)
        if name == MANIFEST:
            try:
                load_manifest(tmp)
            except ValueError:
                return Identified(filename=name, doc_type="unknown",
                                  error=f"{name} is not valid application data: a field is missing or malformed")
            return Identified(filename=name, doc_type="application")
        try:
            (doc,) = intake("identify", [path])
        except IntakeError as exc:
            return Identified(filename=name, doc_type="unknown", error=str(exc))
        return Identified(filename=name, doc_type=doc.doc_type)


@app.post("/api/cases", status_code=202)
def create_case(background: BackgroundTasks, files: list[UploadFile] = File(...)) -> RunStarted:
    """Upload PDFs plus an optional application.json (the ApplicationManifest)."""
    case_id = _new_case_id()
    folder = _case_folder(case_id)
    folder.mkdir(parents=True)
    _save(folder, files)
    return _start(case_id, background)


def _summary(result: CaseResult, events: list[AuditEvent]) -> CaseSummary:
    created = next((e.timestamp for e in events if e.action == "case_created"), None)
    return CaseSummary(
        case_id=result.case_id,
        company_name=_company_name(result),
        doc_count=len(result.pack.docs),
        band=result.risk.band,
        score=result.risk.score,
        path=result.routing.path if result.routing else None,
        status=lifecycle.status(events),
        created_at=created.isoformat() if created else "",
    )


@app.get("/api/cases")
def list_cases() -> list[CaseSummary]:
    """Every case, newest first; runs still going (or failed before a result
    was stored) are listed too."""
    by_case: dict[str, list[AuditEvent]] = defaultdict(list)
    for event in read_all_events():
        by_case[event.case_id].append(event)
    root = Path(CASES_DIR)
    stored = [
        _summary(CaseResult.model_validate_json(path.read_text(encoding="utf-8")),
                 by_case[path.parent.name])
        for path in (root.glob("*/result.json") if root.is_dir() else [])
    ]
    known = {s.case_id for s in stored}
    pending = [
        CaseSummary(case_id=p.case_id, company_name=None, doc_count=0, band=None, score=None,
                    path=None, status=p.state if p.state == "failed" else "running",
                    created_at="")
        for p in jobs.unfinished() if p.case_id not in known
    ]
    # Unfinished runs first, then newest first
    return pending + sorted(stored, key=lambda s: s.created_at, reverse=True)


@app.get("/api/cases/{case_id}/progress")
def get_progress(case_id: str) -> Progress:
    """Which stage the case's run is on. After a server restart a finished
    case still reports "done", rebuilt from its stored result."""
    progress = jobs.get(case_id)
    if progress is not None:
        return progress
    result = _load(case_id)
    stages = [StageProgress(stage_id=s.stage_id, state=s.status) for s in result.stages]
    return Progress(case_id=case_id, state="done",
                    stages=[*stages, StageProgress(stage_id="record", state="ran")])


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
    events = _require(result, body.decision)
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
    events = _require(_load(case_id), "mlro_decision")
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


@app.post("/api/cases/{case_id}/documents", status_code=202)
def add_documents(
    case_id: str, background: BackgroundTasks, files: list[UploadFile] = File(...)
) -> RunStarted:
    """Documents answering a request for information: added to the case,
    which is then re-assessed on its full document set."""
    _require(_load(case_id), "documents_added")
    names = _save(_case_folder(case_id), files)
    log_event(AuditEvent(
        case_id=case_id, actor=SYSTEM, action="documents_added", details={"filenames": names},
    ))
    return _start(case_id, background, new_case=False)


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


@app.get("/api/cases/{case_id}/request-draft")
def get_request_draft(case_id: str) -> RequestDraft:
    """A request for information drafted from the case, for the analyst to
    edit and send: applicant-safe findings and verified AI questions only."""
    return draft_request(_load(case_id))


@app.get("/api/cases/{case_id}/evidence/{evidence_id}/location")
def get_evidence_location(case_id: str, evidence_id: str, row: int | None = None) -> EvidenceLocation:
    """Where a finding is: its document and page, and boxes around its quote
    (or, without a quote, its observed value) when the page has a text layer.
    A finding with rows boxes each of its rows on its page; *row* (0-based)
    points to that one row instead, on its own page."""
    result = _load(case_id)
    ev = next((e for e in result.evidence if e.evidence_id == evidence_id), None)
    if ev is None or (row is not None and not 0 <= row < len(ev.rows)):
        raise HTTPException(404, "Evidence not found")
    if ev.document == PACK_DOC:
        return EvidenceLocation(evidence_id=evidence_id, doc_id=PACK_DOC, page=0,
                                page_count=0, highlighted=None, boxes=[])
    page = ev.page if row is None else ev.rows[row].page
    path, doc = _document(result, ev.document, page)
    highlighted, boxes = _placed(_spots(ev, page, row), path, page, _ocr_lines(result.case_id, doc.doc_id, page))
    return EvidenceLocation(evidence_id=evidence_id, doc_id=doc.doc_id, page=page,
                            page_count=doc.page_count, highlighted=highlighted, boxes=boxes)


def _spots(ev: Evidence, page: int, row: int | None = None) -> list[tuple[str | None, str | None]]:
    """The (quote, observed) of *ev* to box on *page*: each of its rows on
    that page (or row *row* alone), else its own when the page is its page."""
    if ev.rows:
        rows = ev.rows if row is None else [ev.rows[row]]
        return [(r.quote, r.observed) for r in rows if r.page == page]
    return [(ev.quote, ev.observed)] if ev.page == page else []


def _placed(spots: list[tuple[str | None, str | None]], path: Path, page: int,
            lines: list[OcrLine]) -> tuple[str | None, list[Box]]:
    """The boxes around each spot on *page* (its quote, else its observed
    value) and the first text placed; (None, []) when none can be placed."""
    highlighted: str | None = None
    boxes: list[Box] = []
    for spot in spots:
        for text in spot:
            if text and (found := locate(path, page, text, lines)):
                highlighted = highlighted or text
                boxes += found
                break
    return highlighted, boxes


@app.get("/api/cases/{case_id}/documents/{doc_id}/pages/{page}/findings")
def get_page_findings(case_id: str, doc_id: str, page: int) -> list[PageFinding]:
    """Every finding on one page that is not information only, most severe
    first, each with boxes around its text: the forensic overlay. A finding
    with rows is on every page one of its rows is."""
    result = _load(case_id)
    path, _ = _document(result, doc_id, page)
    lines = _ocr_lines(result.case_id, doc_id, page)
    on_page = sorted(((e, spots) for e in result.evidence
                      if e.document == doc_id and e.severity != "info" and (spots := _spots(e, page))),
                     key=lambda found: -SEVERITY_WEIGHTS[found[0].severity])
    return [PageFinding(evidence_id=e.evidence_id, check_id=e.check_id, title=signal_for(e.check_id).name,
                        severity=e.severity, explanation=e.explanation, observed=e.observed,
                        boxes=_placed(spots, path, page, lines)[1]) for e, spots in on_page]


@app.get("/api/feedback")
def get_feedback() -> list[CheckFeedback]:
    """Override statistics per check, from the whole audit log."""
    return report(read_all_events())


def _cost_summary() -> CostSummary:
    costs = [c for c in usage.report(usage.read_usage()) if c.case_id != usage.UNATTRIBUTED]
    priced = [c.usd for c in costs if c.usd is not None]
    total = sum(priced, Decimal(0))
    return CostSummary(cases=len(costs), usd_total=total,
                       usd_per_case=total / len(priced) if priced else None)


@app.get("/api/controls")
def get_controls() -> Controls:
    """Versions, capabilities, signal library, spend, the latest saved
    evaluation and analyst override statistics."""
    return Controls(
        policy_version=POLICY_VERSION,
        rule_version=RULE_VERSION,
        reference_data=reference_versions(),
        capabilities=[
            Capability(name="ocr", enabled=OCR_ENABLED, detail="Amazon Textract DetectDocumentText"),
            Capability(name="prompt_screen", enabled=PROMPT_SCREEN_ENABLED,
                       detail=f"Bedrock guardrail {GUARDRAIL_ID} v{GUARDRAIL_VERSION}"),
            Capability(name="llm_extraction", enabled=EXTRACTION_MODE == "llm",
                       detail=BEDROCK_MODEL_ID),
            Capability(name="ai_team", enabled=AGENTS_ENABLED,
                       detail=f"{AGENT_SUPERVISOR_MODEL} + {AGENT_SPECIALIST_MODEL}"),
            Capability(name="shadow_score", enabled=AGENTS_ENABLED and SHADOW_SCORE_ENABLED,
                       detail=SHADOW_SCORER_MODEL),
        ],
        scoring=ScoringPolicy(
            severity_points=SEVERITY_WEIGHTS, band_low_max=BAND_LOW_MAX, band_medium_max=BAND_MEDIUM_MAX,
            forces_high=sorted(HARD_RULE_IDS), forces_enhanced_review=sorted(HARD_FLAG_CHECK_IDS),
            qa_sample_percent=QA_SAMPLE_PERCENT,
        ),
        signals=list(SIGNALS),
        cost=_cost_summary(),
        evaluation=load_saved(),
        agent_eval=agent_eval.load_saved(),
        feedback=report(read_all_events()),
    )
