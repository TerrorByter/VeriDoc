import type {
  AgentName, CaseStatus, EvidenceStatus, InvestigationStatus, Lead, Pattern, RailStageId, ReviewPath, SpecialistName,
  StageId,
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
    input: "Uploaded PDFs (workbooks are not read)", output: "Page text, confidence and line positions",
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
    id: "records", n: 5, group: "Read", name: "Official records",
    what: "Fetches ACRA's record of every company the documents name and IRAS's Notice of Assessment for each guarantor, as the bank would itself. The documents are held to these, never to a copy the applicant supplies.",
    input: "UENs and NRICs the documents give", output: "ACRA records and IRAS notices",
  },
  {
    id: "rule_checks", n: 6, group: "Check", name: "Rule checks",
    what: "The rule checks run first, before any AI: each is a fixed test over the extracted fields and raw files, and every finding names the check behind it. Nothing the agents find afterwards changes the score, readiness or route.",
    input: "Extracted fields and raw files", output: "Findings",
  },
  {
    id: "ai_team", n: 7, group: "Check", name: "Agents", service: "Bedrock",
    what: "A Supervisor briefs four AI specialists, who investigate what the rules cannot state, with read-only tools, and report leads. Code verifies every lead, and leads never change the score or the route.",
    input: "Findings and extracted fields", output: "Verified leads and a case memo",
  },
  {
    id: "score", n: 8, group: "Decide", name: "Score", steps: ["score", "shadow_score"],
    what: "Only fraud findings score. Each maps to a named signal with fixed points, so every line of the score names the rule behind it.",
    input: "Fraud findings", output: "Score out of 100 and risk band",
  },
  {
    id: "readiness", n: 9, group: "Decide", name: "Readiness",
    what: "Missing documents and form errors make a case incomplete. They never add risk points: completeness and risk are kept apart on purpose.",
    input: "Compliance and completeness findings", output: "Complete or incomplete",
  },
  {
    id: "route", n: 10, group: "Decide", name: "Route",
    what: "A versioned policy table turns the score, readiness and hard flags into a review path. A hard flag can raise the path but never lower it.",
    input: "Score, readiness, hard flags", output: "Review path",
  },
  {
    id: "projections", n: 11, group: "Explain", name: "Investigation",
    what: "The investigation queue, transaction chains and timeline are built from the findings, never separately, so they cannot disagree with them.",
    input: "Findings and extracted fields", output: "Tasks, chains and a timeline",
  },
  {
    id: "record", n: 12, group: "Record", name: "Audit record",
    what: "The result is stored and one audit event records everything needed to reconstruct the decision later: versions, file hashes and evidence coverage.",
    input: "Case result", output: "Stored result and audit event",
  },
];

export const STAGE_BY_ID = Object.fromEntries(STAGES.map((s) => [s.id, s])) as Record<RailStageId, StageInfo>;

/** The rail stage a server stage belongs to: the shadow score runs in the Score stage */
export const stageOf = (id: StageId): StageInfo => STAGE_BY_ID[id === "shadow_score" ? "score" : id];

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
export const LEAD_STATUS_LABEL: Record<Lead["kind"], Record<InvestigationStatus, string>> = {
  triage: { supported: "Finding stands", refuted: "Ordinary explanation", unresolved: "Unresolved" },
  pattern: { supported: "Pattern found", refuted: "Checked, clear", unresolved: "Unresolved" },
};

/** What code established, as the analyst reads it */
export const EVIDENCE_LABEL: Record<EvidenceStatus, { label: string; title: string }> = {
  verified: { label: "Verified by code", title: "A code check of the case data holds" },
  corroborated: { label: "Corroborated", title: "Verified by code, and an independent rule source agrees" },
  unverified: { label: "Not checked", title: "No code check could establish it: the AI's cited evidence only" },
  contradicted: { label: "Contradicted by code", title: "A code check of the case data shows it does not hold" },
};

/** The facts a code check read, as the ledger labels them; any other key is shown humanised. */
export const FACT_LABEL: Record<string, string> = {
  invoice: "Invoice",
  buyer: "Buyer",
  payer: "Payer",
  transaction: "Bank row",
  amount: "Amount",
  date: "Date",
  counterparty: "Counterparty",
  register_uen: "Register UEN",
  purchase_order: "Purchase order",
  shipped: "Shipped",
  payment: "Paid",
  incorporated: "Incorporated",
  rule_finding: "Rule finding",
  corroborated_by: "Corroborated by",
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
  notice_of_assessment: "Notice of Assessment",
  acra_profile: "ACRA business profile",
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
