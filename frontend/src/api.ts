import type {
  AnalystDecision,
  AuditEvent,
  CaseView,
  EvidenceLocation,
  FlagDecision,
  MlroDecision,
  ReasonCode,
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

export const listPacks = () => request<string[]>("/api/packs");

export const createCaseFromPack = (name: string) =>
  request<CaseView>(`/api/cases/from-pack/${encodeURIComponent(name)}`, {
    method: "POST",
  });

export const createCase = (files: File[]) => {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return request<CaseView>("/api/cases", { method: "POST", body: form });
};

export const getCase = (caseId: string) => request<CaseView>(`/api/cases/${caseId}`);

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

export const addDocuments = (caseId: string, files: File[]) => {
  const form = new FormData();
  files.forEach((file) => form.append("files", file));
  return request<CaseView>(`/api/cases/${caseId}/documents`, { method: "POST", body: form });
};

export const pageUrl = (caseId: string, docId: string, page: number) =>
  `/api/cases/${caseId}/documents/${encodeURIComponent(docId)}/pages/${page}`;

export const getEvidenceLocation = (caseId: string, evidenceId: string) =>
  request<EvidenceLocation>(`/api/cases/${caseId}/evidence/${evidenceId}/location`);
