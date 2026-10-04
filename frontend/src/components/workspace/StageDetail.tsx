import type { ReactNode } from "react";
import { duration, PATH_LABEL, STAGE_BY_ID, STAGES, usd } from "../../catalogue";
import { href } from "../../route";
import type { AuditEvent, CaseView, FlagDecision, ReasonCode, StageId, ViewTarget } from "../../types";
import { AgentsStage } from "../stages/AgentsStage";
import { AuditStage } from "../stages/AuditStage";
import { ExtractStage } from "../stages/ExtractStage";
import { IntakeStage } from "../stages/IntakeStage";
import { InvestigationStage } from "../stages/InvestigationStage";
import { OcrStage } from "../stages/OcrStage";
import { PromptScreenStage } from "../stages/PromptScreenStage";
import { ReadinessStage } from "../stages/ReadinessStage";
import { ReviewStage } from "../stages/ReviewStage";
import { RouteStage } from "../stages/RouteStage";
import { ScoreStage } from "../stages/ScoreStage";
import { Icon, Pill, ServiceTag } from "../ui";
import { stageStatus } from "./stageStatus";

export interface StageProps {
  kase: CaseView;
  audit: AuditEvent[];
  onView: (target: ViewTarget) => void;
  onDecideFlag: (id: string, decision: FlagDecision, code: ReasonCode | null, note: string) => Promise<void>;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? "" : "s"}`;

/** What the stage produced for this case, in one line. */
function result(kase: CaseView, id: StageId): string {
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
    case "agents":
      return `${plural(risky.length, "finding")} from ${kase.agent_reports.reduce((n, a) => n + a.checks_run, 0)} checks`;
    case "score":
      return `${kase.risk.score} / 100, ${kase.risk.band}`;
    case "readiness":
      return kase.readiness?.status ?? "Not assessed";
    case "route":
      return kase.routing ? PATH_LABEL[kase.routing.path] : "Not routed";
    case "projections":
      return `${plural(kase.investigation.length, "task")}, ${plural(kase.chains.length, "chain")}, ${plural(kase.timeline.length, "dated event")}`;
    case "semantic_review":
      return kase.review ? `${plural(kase.review.observations.length, "observation")}` : "Nothing to review";
    case "record":
      return "Result stored, audit event written";
  }
}

const BODY: Record<StageId, (p: StageProps) => ReactNode> = {
  ocr: (p) => <OcrStage {...p} />,
  intake: (p) => <IntakeStage {...p} />,
  prompt_screen: (p) => <PromptScreenStage {...p} />,
  extract: (p) => <ExtractStage {...p} />,
  agents: (p) => <AgentsStage {...p} />,
  score: (p) => <ScoreStage {...p} />,
  readiness: (p) => <ReadinessStage {...p} />,
  route: (p) => <RouteStage {...p} />,
  projections: (p) => <InvestigationStage {...p} />,
  semantic_review: (p) => <ReviewStage {...p} />,
  record: (p) => <AuditStage {...p} />,
};

export function StageDetail(props: StageProps & { stage: StageId }) {
  const { stage, kase, audit } = props;
  const info = STAGE_BY_ID[stage];
  const status = stageStatus(kase, audit, stage);
  const off = status.state === "off";
  const prev = STAGES[info.n - 2];
  const next = STAGES[info.n];
  const cost = kase.cost?.usd_by_stage[stage];

  return (
    <section className="detail" aria-labelledby="stage-title">
      <header className="stage-head">
        <div className="stage-meta">
          <span className="muted">Stage {info.n} of {STAGES.length}, {info.group.toLowerCase()}</span>
          {off ? <Pill tone="neutral">Switched off</Pill> : status.state === "flag" ? <Pill tone="high">Needs attention</Pill> : <Pill tone="low">Ran</Pill>}
          {info.service && <ServiceTag service={info.service} off={off} />}
        </div>
        <h2 id="stage-title">{info.name}</h2>
        <p className="stage-what">{info.what}</p>
        <dl className="io">
          <div><dt>Input</dt><dd>{info.input}</dd></div>
          <div><dt>Result</dt><dd>{off ? "Not run" : result(kase, stage)}</dd></div>
          <div><dt>Version</dt><dd className="mono">{status.report?.version || "—"}</dd></div>
          <div>
            <dt>Time and cost</dt>
            <dd className="mono">
              {status.report && !off ? duration(status.report.duration_ms) : "—"}
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
