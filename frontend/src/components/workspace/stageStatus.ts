import type { AuditEvent, CaseView, Evidence, Lead, RailStageId, StageReport } from "../../types";

export type RailState = "ran" | "flag" | "off";

export interface StageStatus {
  state: RailState;
  /** Something for the analyst to look at in this stage */
  count?: number;
  report?: StageReport;
}

const risky = (e: Evidence) => e.severity !== "info";
const byCheck = (kase: CaseView, ...checks: string[]) =>
  kase.evidence.filter((e) => risky(e) && checks.includes(e.check_id));

const keptLeads = (kase: CaseView) => kase.ai_team?.leads.filter((l) => l.outcome === "kept") ?? [];
/** Kept pattern leads: the AI's hunts for what the rules cannot see */
const patternLeads = (kase: CaseView) => keptLeads(kase).filter((l) => l.kind === "pattern");

const verifiedByCode = (l: Lead) => l.evidence_status === "verified" || l.evidence_status === "corroborated";

/** Flags: leads code verified or corroborated against the case data, other than ones that only confirm a rule finding */
export const aiLeads = (kase: CaseView) =>
  patternLeads(kase).filter((l) => verifiedByCode(l) && !l.check_facts.rule_finding);

/** Leads code verified that only confirm an existing rule finding: the rule's flag, not a second one */
export const ruleConfirmed = (kase: CaseView) =>
  patternLeads(kase).filter((l) => verifiedByCode(l) && l.check_facts.rule_finding);

/** The AI's judgement of rule findings (triage): whether each stands or has an ordinary explanation. No code
 * checks a judgement, so it is a review of the rule's flag, never a flag or a suspicion of its own */
export const triaged = (kase: CaseView) => keptLeads(kase).filter((l) => l.kind === "triage");

/** Patterns the AI found but code could not establish: in front of the analyst to check by hand, never a flag,
 * a question or a score */
export const suspicions = (kase: CaseView) =>
  patternLeads(kase).filter((l) => l.evidence_status === "unverified" && l.investigation_status === "supported");

/** Patterns the AI could not settle and code could not establish: on the record, never a flag or a question */
export const observations = (kase: CaseView) =>
  patternLeads(kase).filter((l) => l.evidence_status === "unverified" && l.investigation_status === "unresolved");

/** Patterns checked and found clear, by the agent's own tests or by code: a record, never a flag */
export const clearChecks = (kase: CaseView) =>
  patternLeads(kase).filter((l) => l.evidence_status === "contradicted"
    || (l.evidence_status === "unverified" && l.investigation_status === "refuted"));

/** Rule findings still open: not info */
export const openFindings = (kase: CaseView) => kase.evidence.filter(risky);

/** The rail's view of one stage, from the stage record and the case's own outputs. */
export function stageStatus(kase: CaseView, audit: AuditEvent[], id: RailStageId): StageStatus {
  if (id === "record") {
    return { state: audit.some((e) => e.action === "pipeline_completed") ? "ran" : "off" };
  }
  const report = kase.stages.find((s) => s.stage_id === id);
  // Results stored before stages were recorded: every stage that produced output ran
  if (report?.status === "off") return { state: "off", report };

  const flagged = (count: number): StageStatus => ({ state: count ? "flag" : "ran", count: count || undefined, report });
  switch (id) {
    case "ocr":
      return flagged(byCheck(kase, "FORENSIC_OCR_MISMATCH").length);
    case "intake":
      return flagged(kase.docs.filter((d) => d.doc_type === "unknown").length);
    case "prompt_screen":
      return flagged(byCheck(kase, "PROMPT_INJECTION").length);
    case "records":
      return flagged(byCheck(kase, "OFFICIAL_RECORDS_UNAVAILABLE", "XDOC_OFFICIAL_RECORD").length);
    case "rule_checks":
      return flagged(openFindings(kase).length);
    case "ai_team":
      return flagged(aiLeads(kase).length);
    case "score":
      return { state: kase.risk.band === "Low" ? "ran" : "flag", report };
    case "readiness":
      return flagged(kase.readiness?.open_items.length ?? 0);
    case "route":
      return flagged(kase.routing?.hard_flags.length ?? 0);
    case "projections":
      return flagged(kase.investigation.length);
    default:
      return { state: "ran", report };
  }
}
