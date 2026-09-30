export type Severity = "info" | "low" | "medium" | "high" | "critical";
export type Category = "fraud_signal" | "compliance" | "completeness";
export type Band = "Low" | "Medium" | "High";

export interface Evidence {
  evidence_id: string;
  agent: string;
  check_id: string;
  category: Category;
  severity: Severity;
  document: string;
  page: number;
  quote: string | null;
  expected: string | null;
  observed: string | null;
  method: "deterministic" | "llm";
  confidence: number;
  explanation: string;
}

export interface DocMeta {
  doc_id: string;
  filename: string;
  sha256: string;
  doc_type: string;
  page_count: number;
}

export interface AgentReport {
  name: string;
  description: string;
  checks_run: number;
  findings: number;
  duration_ms: number;
}

export interface Risk {
  score: number;
  band: Band;
  manual_review_required: boolean;
  reasons: string[];
}

export interface ChecklistItem {
  id: string;
  label: string;
  status: string;
}

export interface Readiness {
  status: "Complete" | "Incomplete";
  open_items: string[];
  checklist: ChecklistItem[];
}

export interface CaseView {
  case_id: string;
  docs: DocMeta[];
  evidence: Evidence[];
  agent_reports: AgentReport[];
  risk: Risk;
  readiness: Readiness | null;
  summary: string | null;
  extraction_mode: string;
}

export interface AuditEvent {
  timestamp: string;
  actor: string;
  action: string;
  evidence_id: string | null;
  decision: string | null;
  reason: string | null;
}

export type FlagDecision = "confirmed" | "dismissed";
export type FinalDecision = "approved" | "rejected" | "more_info_requested";
