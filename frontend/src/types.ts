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

export interface Contribution {
  signal_id: string;
  check_id: string;
  name: string;
  evidence_id: string;
  document: string;
  page: number;
  points: number;
}

export interface Risk {
  score: number;
  band: Band;
  manual_review_required: boolean;
  reasons: string[];
  contributions: Contribution[];
  rule_version: string;
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

export type ReviewPath = "fast_track" | "standard_review" | "enhanced_review";

export interface Routing {
  path: ReviewPath;
  analyst_review: boolean;
  qa_sampled: boolean;
  edd_required: boolean;
  hard_flags: string[];
  low_confidence: string[];
  reasons: string[];
  policy_version: string;
}

export interface ClaimRef {
  doc_id: string;
  page: number;
  label: string;
  value: string;
}

export type ChainStatus = "matched" | "contradicts" | "missing" | "not_provided";

export interface ChainStep {
  step: "purchase_order" | "bill_of_lading" | "payment";
  status: ChainStatus;
  invoice_claim: ClaimRef;
  counterpart: ClaimRef | null;
  evidence_ids: string[];
  note: string;
}

export interface TransactionChain {
  invoice_number: string;
  doc_id: string;
  steps: ChainStep[];
  broken: boolean;
}

export type CaseStatus =
  | "assessed"
  | "info_requested"
  | "escalated"
  | "mlro_reviewed"
  | "approved"
  | "declined";

export interface CaseView {
  case_id: string;
  docs: DocMeta[];
  evidence: Evidence[];
  agent_reports: AgentReport[];
  risk: Risk;
  readiness: Readiness | null;
  routing: Routing | null;
  chains: TransactionChain[];
  summary: string | null;
  extraction_mode: string;
  status: CaseStatus;
  allowed_actions: string[];
  open_flags: string[];
}

export interface AuditEvent {
  timestamp: string;
  actor: string;
  role: string | null;
  action: string;
  evidence_id: string | null;
  decision: string | null;
  reason_code: string | null;
  reason: string | null;
}

export type FlagDecision = "confirmed" | "dismissed";

export const REASON_CODES = {
  ocr_misread: "OCR misread the document",
  extraction_error: "Field extracted wrongly",
  legitimate_explanation: "Legitimate explanation",
  duplicate_finding: "Duplicate of another finding",
  threshold_too_strict: "Threshold too strict",
  other: "Other (note required)",
} as const;
export type ReasonCode = keyof typeof REASON_CODES;

export type AnalystDecision = "approve" | "decline" | "request_info" | "escalate";
export type MlroDecision = "str_filed" | "no_str";
