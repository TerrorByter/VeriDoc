import type {
  AgentName, CaseStatus, Lead, LeadStatus, Pattern, RailStageId, ReviewPath, SpecialistName, StageId, Strength,
} from "./types";

export type StageGroup = "Read" | "Check" | "Decide" | "Explain" | "Record";
export type Service = "Textract" | "Guardrails" | "Bedrock";

export interface StageInfo {
  id: RailStageId;
  /** Position in the run, 1-based */
  n: number;
  /** The server stages this rail stage runs, when more than its own */
  steps?: StageId[];
  group: StageGroup;
  name: string;
  /** What the stage does, in one or two plain sentences */
  what: string;
  input: string;
  output: string;
  service?: Service;
}

/** Every pipeline stage in run order: the server's ASSESS_STAGES plus "record". */
export const STAGES: StageInfo[] = [
  {
    id: "ocr", n: 1, group: "Read", name: "OCR", service: "Textract",
    what: "Reads every page as it appears visually, independently of the text stored inside the PDF. The two readings are compared later to catch figures pasted over the original.",
    input: "Uploaded PDFs", output: "Page text, confidence and line positions",
  },
  {
    id: "intake", n: 2, group: "Read", name: "Intake",
    what: "Fingerprints each file with SHA-256, so the audit trail proves exactly which bytes were assessed, and works out what kind of document each one is.",
    input: "Uploaded files", output: "Typed, hashed documents",
  },
  {
    id: "prompt_screen", n: 3, group: "Read", name: "Prompt screen", service: "Guardrails",
    what: "A Bedrock guardrail scans exactly the text a model would read, before any model reads it. A hit becomes a finding for the analyst rather than silently blocking the document.",
    input: "Model-visible page text", output: "Prompt attacks per page",
  },
  {
    id: "extract", n: 4, group: "Read", name: "Extract", service: "Bedrock",
    what: "Turns each document into structured fields. With the model on, every quote it returns must appear on the page or it is thrown away.",
    input: "Model-visible page text", output: "Fields with source quotes",
  },
  {
    id: "agents", n: 5, group: "Check", name: "Agents", steps: ["agents", "ai_team"], service: "Bedrock",
    what: "The 36 checks run first as mandatory tools, so no AI can skip one. Then a Supervisor briefs four AI specialists, who investigate with read-only tools and report leads. Leads never change the score.",
    input: "Extracted fields and raw files", output: "Findings",
  },
  {
    id: "score", n: 6, group: "Decide", name: "Score",
    what: "Only fraud findings score. Each maps to a named signal with fixed points, so every line of the score names the rule behind it.",
    input: "Fraud findings", output: "Score out of 100 and risk band",
  },
  {
    id: "readiness", n: 7, group: "Decide", name: "Readiness",
    what: "Missing documents and form errors make a case incomplete. They never add risk points: completeness and risk are kept apart on purpose.",
    input: "Compliance and completeness findings", output: "Complete or incomplete",
  },
  {
    id: "route", n: 8, group: "Decide", name: "Route",
    what: "A versioned policy table turns the score, readiness and hard flags into a review path. A hard flag can raise the path but never lower it.",
    input: "Score, readiness, hard flags", output: "Review path",
  },
  {
    id: "projections", n: 9, group: "Explain", name: "Investigation",
    what: "The investigation queue, transaction chains and timeline are built from the findings, never separately, so they cannot disagree with them.",
    input: "Findings and extracted fields", output: "Tasks, chains and a timeline",
  },
  {
    id: "record", n: 10, group: "Record", name: "Audit record",
    what: "The result is stored and one audit event records everything needed to reconstruct the decision later: versions, file hashes and evidence coverage.",
    input: "Case result", output: "Stored result and audit event",
  },
];

export const STAGE_BY_ID = Object.fromEntries(STAGES.map((s) => [s.id, s])) as Record<RailStageId, StageInfo>;

/** The rail stage a server stage belongs to: the AI team runs in stage 5 */
export const stageOf = (id: StageId): StageInfo => STAGE_BY_ID[id === "ai_team" ? "agents" : id];

/** The AI team in its order: the Supervisor plans, then the specialists investigate */
export const SPECIALISTS: SpecialistName[] = ["forensics", "transaction", "kyc", "timeline"];
export const AGENTS: AgentName[] = ["supervisor", ...SPECIALISTS];

export const AGENT_TITLE: Record<AgentName, string> = {
  supervisor: "Supervisor",
  forensics: "Forensics specialist",
  transaction: "Transaction specialist",
  kyc: "KYC and registry specialist",
  timeline: "Timeline specialist",
};

/** A lead's status as the analyst reads it: triage judges a finding, a pattern lead reports a hunt */
export const LEAD_STATUS_LABEL: Record<Lead["kind"], Record<LeadStatus, string>> = {
  triage: { supported: "Finding stands", refuted: "Ordinary explanation", unresolved: "Unresolved" },
  pattern: { supported: "Pattern found", refuted: "Checked, clear", unresolved: "Unresolved" },
};

export const STRENGTH_LABEL: Record<Strength, { label: string; title: string }> = {
  corroborated: { label: "Corroborated", title: "A rule finding, backed by the AI's cited evidence" },
  unverified: { label: "Unverified", title: "No code check of this pattern yet: cited evidence only" },
};

export const PATTERN_LABEL: Record<Pattern, string> = {
  third_party_payment: "Third-party payment",
  round_trip: "Round trip",
  split_payment: "Split payment",
  related_party: "Related party",
  business_mismatch: "Business mismatch",
  document_inconsistency: "Document inconsistency",
  sequence_anomaly: "Sequence anomaly",
};

export const PATH_LABEL: Record<ReviewPath, string> = {
  fast_track: "Fast track",
  standard_review: "Standard review",
  enhanced_review: "Enhanced review",
};

export const STATUS_LABEL: Record<CaseStatus | "running" | "failed", string> = {
  running: "Running",
  failed: "Run failed",
  assessed: "Awaiting analyst",
  info_requested: "Info requested",
  escalated: "With MLRO",
  mlro_reviewed: "MLRO decided",
  approved: "Approved",
  declined: "Declined",
};

export const DOC_TYPE_LABEL: Record<string, string> = {
  invoice: "Invoice",
  bank_statement: "Bank statement",
  financial_statements: "Financial statements",
  purchase_order: "Purchase order",
  bill_of_lading: "Bill of lading",
  fatca_crs_entity: "FATCA/CRS form (entity)",
  fatca_crs_individual: "FATCA/CRS form (individual)",
  unknown: "Unrecognised",
};

export const usd = (amount: string | null | undefined, digits = 3) =>
  amount == null ? "—" : `$${Number(amount).toFixed(digits)}`;

export const duration = (ms: number) =>
  ms < 1 ? "<1 ms" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;

const DATE_TIME = new Intl.DateTimeFormat("en-SG", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit",
});
export const when = (iso: string) => (iso ? DATE_TIME.format(new Date(iso)) : "—");
