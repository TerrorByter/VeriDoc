import type {
  AnalystDecision,
  AuditEvent,
  CaseSummary,
  CaseView,
  Controls,
  EvidenceLocation,
  PageFinding,
  FlagDecision,
  Identified,
  MlroDecision,
  PackInfo,
  Progress,
  ReasonCode,
  RequestDraft,
  RunStarted,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.detail;
    throw new Error(
      typeof detail === "string" ? detail : `Request failed (${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const listPacks = () => request<PackInfo[]>("/api/packs");

const upload = (files: File[]): RequestInit => {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  return { method: "POST", body };
};

/** Runs start in the background: follow them with getProgress. */
export const createCaseFromPack = (name: string) =>
  request<RunStarted>(`/api/cases/from-pack/${encodeURIComponent(name)}`, { method: "POST" });

export const createCase = (files: File[]) => request<RunStarted>("/api/cases", upload(files));

/** What one file is, before a case is opened; the server keeps nothing */
export const identifyDocument = (file: File) => {
  const body = new FormData();
  body.append("file", file);
  return request<Identified>("/api/documents/identify", { method: "POST", body });
};

export const listCases = () => request<CaseSummary[]>("/api/cases");

export const getProgress = (caseId: string) =>
  request<Progress>(`/api/cases/${caseId}/progress`);

export const getControls = () => request<Controls>("/api/controls");

export const getCase = (caseId: string) => request<CaseView>(`/api/cases/${caseId}`);

export const getRequestDraft = (caseId: string) => request<RequestDraft>(`/api/cases/${caseId}/request-draft`);

export const getAudit = (caseId: string) =>
  request<AuditEvent[]>(`/api/cases/${caseId}/audit`);

export const decideFlag = (
  caseId: string,
  evidenceId: string,
  decision: FlagDecision,
  reasonCode: ReasonCode | null,
  note: string,
  actor: string,
) =>
  request<AuditEvent>(
    `/api/cases/${caseId}/flags/${evidenceId}`,
    json({ decision, reason_code: reasonCode, note, actor }),
  );

export const decideCase = (
  caseId: string,
  decision: AnalystDecision,
  reason: string,
  actor: string,
) =>
  request<AuditEvent>(`/api/cases/${caseId}/decision`, json({ decision, reason, actor }));

export const decideAsMlro = (
  caseId: string,
  decision: MlroDecision,
  reason: string,
  actor: string,
) =>
  request<AuditEvent>(
    `/api/cases/${caseId}/mlro-decision`,
    json({ decision, reason, actor }),
  );

export const addDocuments = (caseId: string, files: File[]) =>
  request<RunStarted>(`/api/cases/${caseId}/documents`, upload(files));

export const pageUrl = (caseId: string, docId: string, page: number) =>
  `/api/cases/${caseId}/documents/${encodeURIComponent(docId)}/pages/${page}`;

export const getEvidenceLocation = (caseId: string, evidenceId: string, row?: number) =>
  request<EvidenceLocation>(
    `/api/cases/${caseId}/evidence/${evidenceId}/location${row === undefined ? "" : `?row=${row}`}`,
  );

export const getPageFindings = (caseId: string, docId: string, page: number) =>
  request<PageFinding[]>(`${pageUrl(caseId, docId, page)}/findings`);
