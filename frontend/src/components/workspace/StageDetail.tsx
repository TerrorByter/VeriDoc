import type { ReactNode } from "react";
import { duration, PATH_LABEL, STAGE_BY_ID, STAGES, usd } from "../../catalogue";
import { href } from "../../route";
import type { AuditEvent, CaseView, FlagDecision, RailStageId, ReasonCode, StageId, ViewTarget } from "../../types";
import { AgentsStage } from "../stages/AgentsStage";
import { AuditStage } from "../stages/AuditStage";
import { ExtractStage } from "../stages/ExtractStage";
import { IntakeStage } from "../stages/IntakeStage";
import { InvestigationStage } from "../stages/InvestigationStage";
import { OcrStage } from "../stages/OcrStage";
import { PromptScreenStage } from "../stages/PromptScreenStage";
import { RecordsStage } from "../stages/RecordsStage";
import { ReadinessStage } from "../stages/ReadinessStage";
import { RouteStage } from "../stages/RouteStage";
import { ScoreStage } from "../stages/ScoreStage";
import { Icon, Pill, ServiceTag } from "../ui";
import { aiLeads, openFindings, stageStatus } from "./stageStatus";

export interface StageProps {
  kase: CaseView;
  audit: AuditEvent[];
  onView: (target: ViewTarget) => void;
  onDecideFlag: (id: string, decision: FlagDecision, code: ReasonCode | null, note: string) => Promise<void>;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** What the stage produced for this case, in one line. */
function result(kase: CaseView, id: RailStageId): string {
  const risky = kase.evidence.filter((e) => e.severity !== "info");
  switch (id) {
    case "ocr": {
      const pages = Object.values(kase.ocr_confidence).reduce((n, p) => n + p.length, 0);
      return `${plural(pages, "page")} read`;
    }
    case "intake":
      return `${plural(kase.docs.length, "document")} hashed and typed`;
    case "prompt_screen": {
      const hits = Object.values(kase.prompt_screen ?? {}).reduce((n, h) => n + h.length, 0);
      return hits ? `${plural(hits, "prompt attack")} found` : "No prompt attacks";
    }
    case "extract":
      return kase.extraction_mode === "llm" ? "Fields extracted by the model" : "Fields extracted by the rule-based parser";
    case "records": {
      const records = kase.official;
      if (!records) return "Not fetched";
      if (records.error) return "ACRA and IRAS did not answer";
      const found = Object.values(records.companies).filter(Boolean).length;
      return `${plural(found, "ACRA record")}, ${plural(records.notices.length, "IRAS notice")}`;
    }
    case "agents": {
      const explained = risky.length - openFindings(kase).length;
      const rules = `${plural(risky.length - explained, "open finding")} from ${kase.agent_reports.reduce((n, a) => n + a.checks_run, 0)} checks`
        + (explained ? `, ${explained} explained by code` : "");
      const team = !kase.ai_team ? "AI team off"
        : kase.ai_team.status === "failed" ? "AI team failed" : plural(aiLeads(kase).length, "AI lead");
      return `${rules} · ${team}`;
    }
    case "score":
      return `${kase.risk.score} / 100, ${kase.risk.band}`;
    case "readiness":
      return kase.readiness?.status ?? "Not assessed";
    case "route":
      return kase.routing ? PATH_LABEL[kase.routing.path] : "Not routed";
    case "projections":
      return `${plural(kase.investigation.length, "task")}, ${plural(kase.chains.length, "chain")}, ${plural(kase.timeline.length, "dated event")}`;
    case "record":
      return "Result stored, audit event written";
  }
}

const BODY: Record<RailStageId, (p: StageProps) => ReactNode> = {
  ocr: (p) => <OcrStage {...p} />,
  intake: (p) => <IntakeStage {...p} />,
  prompt_screen: (p) => <PromptScreenStage {...p} />,
  extract: (p) => <ExtractStage {...p} />,
  records: (p) => <RecordsStage {...p} />,
  agents: (p) => <AgentsStage {...p} />,
  score: (p) => <ScoreStage {...p} />,
  readiness: (p) => <ReadinessStage {...p} />,
  route: (p) => <RouteStage {...p} />,
  projections: (p) => <InvestigationStage {...p} />,
  record: (p) => <AuditStage {...p} />,
};

export function StageDetail(props: StageProps & { stage: RailStageId }) {
  const { stage, kase, audit } = props;
  const info = STAGE_BY_ID[stage];
  const status = stageStatus(kase, audit, stage);
  const off = status.state === "off";
  const prev = STAGES[info.n - 2];
  const next = STAGES[info.n];
  // Stage 6 runs two steps (the rule agents and the AI team): its time and cost are both together
  const steps: StageId[] = info.steps ?? [stage];
  const reports = kase.stages.filter((s) => steps.includes(s.stage_id));
  const costs = steps.flatMap((id) => kase.cost?.usd_by_stage[id] ?? []);
  const cost = costs.length ? String(costs.reduce((n, c) => n + Number(c), 0)) : undefined;
  // Only the AI team calls Bedrock in stage 6
  const team = steps.includes("ai_team") ? kase.ai_team : undefined;
  const serviceOff = off || (steps.includes("ai_team") && !team);

  return (
    <section className="detail" aria-labelledby="stage-title">
      <header className="stage-head">
        <div className="stage-title">
          <h2 id="stage-title">{info.name}</h2>
          {off ? <Pill tone="neutral">Switched off</Pill> : status.state === "flag" ? <Pill tone="high">Needs attention</Pill> : <Pill tone="low">Ran</Pill>}
          {team?.status === "failed" && <Pill tone="med">AI team failed</Pill>}
          {info.service && <ServiceTag service={info.service} off={serviceOff} />}
        </div>
        <p className="stage-what">{info.what}</p>
        <dl className="io">
          <div><dt>Input</dt><dd>{info.input}</dd></div>
          <div><dt>Result</dt><dd>{off ? "Not run" : result(kase, stage)}</dd></div>
          <div><dt>Version</dt><dd>{status.report?.version || "—"}</dd></div>
          <div>
            <dt>Time and cost</dt>
            <dd className="mono">
              {reports.length && !off ? duration(reports.reduce((n, r) => n + r.duration_ms, 0)) : "—"}
              {cost ? `, ${usd(cost, 4)}` : ""}
            </dd>
          </div>
        </dl>
      </header>

      <div className="stage-body">{BODY[stage](props)}</div>

      <nav className="pager" aria-label="Stage navigation">
        {prev ? (
          <a className="btn" href={href({ screen: "case", caseId: kase.case_id, stage: prev.id })}>
            <Icon name="left" size={14} /> {prev.name}
          </a>
        ) : <span />}
        {next ? (
          <a className="btn primary" href={href({ screen: "case", caseId: kase.case_id, stage: next.id })}>
            {next.name} <Icon name="right" size={14} />
          </a>
        ) : <span />}
      </nav>
    </section>
  );
}
