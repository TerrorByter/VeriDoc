export type Severity = "info" | "low" | "medium" | "high" | "critical";
export type Category = "fraud_signal" | "compliance" | "completeness";
export type Band = "Low" | "Medium" | "High";

export interface Evidence {
  evidence_id: string;
  rule_group: string;
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

export interface RuleGroupReport {
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

export type TimelineKind =
  | "incorporated"
  | "financial_year_end"
  | "bank_statement"
  | "purchase_order"
  | "shipped"
  | "invoice"
  | "payment"
  | "application";

export interface TimelineEvent {
  on: string;
  kind: TimelineKind;
  label: string;
  doc_id: string;
  page: number;
  ref: string;
  evidence_ids: string[];
}

export type TaskPriority = "urgent" | "high" | "normal";

export interface InvestigationTask {
  task_id: string;
  priority: TaskPriority;
  signal_id: string;
  check_id: string;
  name: string;
  category: Category;
  document: string;
  evidence_ids: string[];
  remedy: string;
  points: number;
  score_if_resolved: number;
  band_if_resolved: Band;
  path_if_resolved: ReviewPath;
}

/** A highlight, as fractions of the page width and height */
export interface Box {
  x0: number;
  top: number;
  x1: number;
  bottom: number;
}

export interface EvidenceLocation {
  evidence_id: string;
  doc_id: string;
  page: number;
  page_count: number;
  highlighted: string | null;
  boxes: Box[];
}

/** What the document viewer should open: a page, optionally for a finding */
export interface ViewTarget {
  docId: string;
  page: number;
  evidenceId?: string;
}

/** Findings about the application data have no document page */
export const APPLICATION_DOC = "pack";

export interface ReviewPoint {
  text: string;
  evidence_ids: string[];
}

export type AgentName = "supervisor" | "forensics" | "transaction" | "kyc" | "timeline";
export type SpecialistName = Exclude<AgentName, "supervisor">;
/** What a lead's tests show, as its specialist proposes it; it never decides anything */
export type InvestigationStatus = "supported" | "refuted" | "unresolved";
/** What code establishes from the case data, never a model: only verified and corroborated leads are flags */
export type EvidenceStatus = "verified" | "corroborated" | "unverified" | "contradicted";
export type Pattern =
  | "third_party_payment"
  | "round_trip"
  | "split_payment"
  | "related_party"
  | "business_mismatch"
  | "document_inconsistency"
  | "sequence_anomaly";

export interface ToolCall {
  /** R01… */
  call_id: string;
  agent: AgentName;
  tool: string;
  args: Record<string, unknown>;
  /** Short and masked; never page text */
  summary: string;
  duration_ms: number;
  error: boolean;
}

export interface Lead {
  /** L01… */
  lead_id: string;
  agent: SpecialistName;
  /** Triage: judges risky findings (finding_ids set). Pattern: a hunt for one of the specialist's patterns (pattern set) */
  kind: "triage" | "pattern";
  finding_ids: string[];
  pattern: Pattern | null;
  /** The subject a code check reads: an invoice, bank rows (T01…), a counterparty (P01…) */
  invoice_number: string | null;
  transaction_ids: string[];
  counterparty: string | null;
  /** The claim tested */
  hypothesis: string;
  tests: LeadTest[];
  investigation_status: InvestigationStatus;
  /** What would settle an unresolved lead */
  missing_evidence: string[];
  outcome: "kept" | "withheld";
  withheld_reason: string | null;
  evidence_status: EvidenceStatus | null;
  /** The facts the code check read, and why it decided as it did */
  check_facts: Record<string, string>;
  check_reason: string | null;
}

export interface LeadTest {
  /** The tool calls (R…) the test rests on */
  cites: string[];
  expected: string;
  observed: string;
}

export interface ShadowReason {
  /** A rule's check id, a verified pattern, or "no_issue" */
  code: string;
  evidence_ids: string[];
}

/** What a model would route the case to: logged beside the rule route, never used by it */
export interface ShadowScore {
  status: "ran" | "failed";
  route: ReviewPath | null;
  reasons: ShadowReason[];
  model: string;
  prompt_version: string;
  /** Worked out after the fact: the scorer never saw the rule route */
  agrees_with_champion: boolean | null;
  error: string | null;
}

/** The route the disposition gate would take: logged, never applied */
export interface GateDecision {
  would_route: ReviewPath;
  reasons: string[];
}

export interface Memo {
  observations: ReviewPoint[];
  questions: ReviewPoint[];
  /** Points dropped by the verifier */
  withheld: number;
}

export interface AgentTeam {
  status: "ran" | "failed";
  /** Specialist -> its brief */
  plan: Partial<Record<SpecialistName, string>>;
  tool_calls: ToolCall[];
  leads: Lead[];
  memo: Memo | null;
  models: Partial<Record<"supervisor" | "specialist", string>>;
  prompt_versions: Record<string, string>;
  notes: string[];
  /** Rule findings code explained (finding id -> why): not raised in the memo, still on the record */
  resolved: Record<string, string>;
  error: string | null;
}

/** A page of a case document */
export interface DocRef {
  doc_id: string;
  page: number;
}

export type CaseStatus =
  | "assessed"
  | "info_requested"
  | "escalated"
  | "mlro_reviewed"
  | "approved"
  | "declined";

export type StageId =
  | "ocr"
  | "intake"
  | "prompt_screen"
  | "extract"
  | "records"
  | "rule_checks"
  | "ai_team"
  | "shadow_score"
  | "score"
  | "readiness"
  | "route"
  | "projections"
  | "record";

/** The stages the rail shows: the shadow score runs inside the Score stage */
export type RailStageId = Exclude<StageId, "shadow_score">;

export type StageState = "running" | "ran" | "off" | "failed";

export interface StageReport {
  stage_id: StageId;
  status: "ran" | "off" | "failed";
  duration_ms: number;
  version: string;
}

export interface PromptAttackHit {
  page: number;
  confidence: string;
}

/** Decimal amounts arrive as strings, to keep their precision */
export type Usd = string;

export interface CaseCost {
  case_id: string;
  pages: number;
  text_units: number;
  input_tokens: number;
  output_tokens: number;
  usd: Usd | null;
  unpriced: string[];
  usd_by_stage: Partial<Record<StageId, Usd>>;
}

export interface CaseView {
  case_id: string;
  company_name: string | null;
  docs: DocMeta[];
  evidence: Evidence[];
  rule_group_reports: RuleGroupReport[];
  risk: Risk;
  readiness: Readiness | null;
  routing: Routing | null;
  chains: TransactionChain[];
  timeline: TimelineEvent[];
  investigation: InvestigationTask[];
  /** Null when the AI team was off */
  ai_team: AgentTeam | null;
  shadow: ShadowScore | null;
  gate: GateDecision | null;
  extraction_mode: string;
  status: CaseStatus;
  allowed_actions: string[];
  open_flags: string[];
  stages: StageReport[];
  /** What ACRA and IRAS hold for the case, NRIC/FIN masked; null when not fetched */
  official: OfficialRecords | null;
  ocr_confidence: Record<string, number[]>;
  prompt_screen: Record<string, PromptAttackHit[]> | null;
  cost: CaseCost | null;
  /** check_id -> its signal, for every check with a finding */
  signals: Record<string, Signal>;
  /** Bank rows (T01...), invoice numbers and counterparties (P01...) -> the document page they are on */
  subjects: Record<string, DocRef>;
}

/** Money as the server sends it: a decimal string */
type Amount = string | null;

export interface AcraOfficer {
  name: string;
  /** Masked NRIC/FIN */
  id_number: string;
  position: string;
  appointed: string;
  postal_code: string | null;
}

export interface AcraFinancials {
  period_start: string;
  period_end: string;
  currency: string;
  total_assets: Amount;
  total_current_assets: Amount;
  total_liabilities: Amount;
  total_current_liabilities: Amount;
  retained_earnings: Amount;
  revenue: Amount;
  profit_before_tax: Amount;
  profit_after_tax: Amount;
  ebit: Amount;
  operating_cashflow: Amount;
  investing_cashflow: Amount;
  financing_cashflow: Amount;
}

export interface AcraRecord {
  company_name: string;
  uen: string;
  incorporation_date: string;
  status: string;
  registered_address: string;
  issued_on: string;
  officers: AcraOfficer[];
  shareholders: { name: string; shares: number }[];
  financials: AcraFinancials | null;
}

export interface IrasNotice {
  name: string;
  /** Masked NRIC/FIN */
  id_number: string;
  year_of_assessment: number;
  income: Amount;
  deductions: Amount;
  chargeable_income: Amount;
  tax_payable: Amount;
  postal_code: string | null;
}

/** The records the pipeline fetched from ACRA and IRAS rather than took from the applicant */
export interface OfficialRecords {
  retrieved_at: string;
  versions: Record<string, string>;
  applicant_uen: string | null;
  /** UEN -> ACRA's record; null where ACRA has no such company */
  companies: Record<string, AcraRecord | null>;
  notices: IrasNotice[];
  /** Masked NRIC/FIN of each person IRAS holds no notice for */
  no_notice: string[];
  error: string | null;
}

/** A synthetic demo pack and what it shows */
export interface PackInfo {
  name: string;
  note: string | null;
}

export interface RunStarted {
  case_id: string;
}

export type RunState = "running" | "done" | "failed";

export interface Progress {
  case_id: string;
  state: RunState;
  stages: { stage_id: StageId; state: StageState }[];
  current: StageId | null;
  /** What the running stage is doing now, e.g. an AI team tool call */
  detail: string | null;
  error: string | null;
}

export interface CaseSummary {
  case_id: string;
  company_name: string | null;
  doc_count: number;
  band: Band | null;
  score: number | null;
  path: ReviewPath | null;
  status: CaseStatus | "running" | "failed";
  created_at: string;
}

export type SignalFamily = "DOC" | "FIN" | "TXN" | "REG" | "ENT" | "TEMP" | "COMP" | "SYS";

export interface Signal {
  signal_id: string;
  check_id: string;
  family: SignalFamily;
  name: string;
  remedy: string;
  points: number | null;
}

export interface AttackResult {
  pack: string;
  attack: string;
  document: string;
  detail: string;
  detected: boolean | null;
  signals: string[];
  evidence_ids: string[];
}

export interface PackResult {
  pack: string;
  band: string;
  expected_band: string;
  readiness: string;
  expected_readiness: string;
  path: string;
  expected_path: string;
  missed: string[];
  unexpected: string[];
  error: string | null;
}

export interface EvaluationReport {
  packs: PackResult[];
  precision: number;
  recall: number;
  band_accuracy: number;
  readiness_accuracy: number;
  routing_accuracy: number;
  attacks: AttackResult[];
  attack_detection_rate: number;
  evidence_coverage: number;
  skipped: string[];
  evaluated_at: string;
  capabilities: Record<string, boolean>;
  extraction_mode: string;
}

export interface Capability {
  name: "ocr" | "prompt_screen" | "llm_extraction" | "ai_team" | "shadow_score";
  enabled: boolean;
  detail: string;
}

export interface AgentPackResult {
  pack: string;
  /** "agent:pattern" */
  expected_leads: string[];
  found_leads: string[];
  triage_expected: number;
  triage_correct: number;
  noise: number;
  withheld: number;
  leads: number;
  usd: Usd | null;
  seconds: number;
  /** The team's status, or "error: …" */
  status: string;
  /** Pattern leads code verified or corroborated, "agent:pattern" */
  verified_leads: string[];
  /** Flags matching nothing planted: the false flags an analyst would get */
  flag_noise: number;
  observations: number;
  contradicted: number;
  ai_questions: number;
  planted: boolean;
  shadow_route: ReviewPath | null;
  would_route: ReviewPath | null;
  /** A planted fraud the shadow scorer or the gate would send to fast track */
  miss: boolean;
  /** A planted fraud the shadow scorer alone would send to fast track */
  shadow_miss: boolean;
  shadow_agrees_champion: boolean | null;
  shadow_matches_truth: boolean | null;
}

/** One saved AI team evaluation, for one model setup */
export interface AgentEvalRow {
  evaluated_at: string;
  supervisor: string;
  specialist: string;
  shadow: string | null;
  packs: AgentPackResult[];
  supported_recall: number;
  triage_accuracy: number;
  noise: number;
  withheld_rate: number;
  /** The rest were not measured by rows saved before they existed: null there */
  verified_recall: number | null;
  flag_noise: number | null;
  /** Planted frauds the shadow scorer or the gate would send to fast track / planted frauds */
  miss_rate: number | null;
  shadow_miss_rate: number | null;
  /** Packs the gate would send to fast track / packs */
  auto_clear_rate: number | null;
  shadow_champion_agreement: number | null;
  shadow_truth_agreement: number | null;
  usd_per_case: Usd | null;
  seconds_per_case: number;
}

export interface CheckFeedback {
  check_id: string;
  confirmed: number;
  dismissed: number;
  dismiss_rate: number;
  reasons: Record<string, number>;
}

export interface Controls {
  policy_version: string;
  rule_version: string;
  reference_data: Record<string, string>;
  capabilities: Capability[];
  signals: Signal[];
  cost: { cases: number; usd_total: Usd; usd_per_case: Usd | null };
  evaluation: EvaluationReport | null;
  agent_eval: AgentEvalRow[];
  feedback: CheckFeedback[];
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
  details: Record<string, unknown>;
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
