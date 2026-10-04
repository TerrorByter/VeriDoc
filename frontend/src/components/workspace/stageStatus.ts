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

const verifiedByCode = (l: Lead) => l.evidence_status === "verified" || l.evidence_status === "corroborated";

/** Flags: leads code verified or corroborated against the case data, other than ones that only confirm a rule finding */
export const aiLeads = (kase: CaseView) =>
  keptLeads(kase).filter((l) => verifiedByCode(l) && !l.check_facts.rule_finding);

/** Leads code verified that only confirm an existing rule finding: the rule's flag, not a second one */
export const ruleConfirmed = (kase: CaseView) =>
  keptLeads(kase).filter((l) => verifiedByCode(l) && l.check_facts.rule_finding);

/** What the AI suspects but code could not establish: for the analyst to see, never a flag or a question */
export const observations = (kase: CaseView) =>
  keptLeads(kase).filter((l) => l.evidence_status === "unverified" && l.investigation_status !== "refuted");

/** Checks that came back clear, by the agent's own tests or by code: a record, never a flag */
export const clearChecks = (kase: CaseView) =>
  keptLeads(kase).filter((l) => l.evidence_status === "contradicted"
    || (l.evidence_status === "unverified" && l.investigation_status === "refuted"));

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
    case "agents":
      return flagged(kase.evidence.filter(risky).length + aiLeads(kase).length);
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
