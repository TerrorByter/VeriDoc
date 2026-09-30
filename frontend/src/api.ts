import type {
  AuditEvent,
  CaseView,
  FinalDecision,
  FlagDecision,
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

export const getAudit = (caseId: string) =>
  request<AuditEvent[]>(`/api/cases/${caseId}/audit`);

export const decideFlag = (
  caseId: string,
  evidenceId: string,
  decision: FlagDecision,
  reason: string,
) =>
  request<AuditEvent>(
    `/api/cases/${caseId}/flags/${evidenceId}`,
    json({ decision, reason }),
  );

export const decideCase = (
  caseId: string,
  decision: FinalDecision,
  reason: string,
) => request<AuditEvent>(`/api/cases/${caseId}/decision`, json({ decision, reason }));
