import type { AuditEvent } from "../../types";
import type { StageProps } from "../workspace/StageDetail";
import { Section } from "./common";

const ACTION: Record<string, string> = {
  case_created: "Case opened",
  pipeline_completed: "Pipeline finished",
  flag_confirmed: "Flag confirmed",
  flag_dismissed: "Flag dismissed",
  analyst_decision: "Analyst decision",
  mlro_decision: "MLRO decision",
  documents_added: "Documents added",
};

const TIME = new Intl.DateTimeFormat("en-SG", {
  day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit",
});

const text = (v: unknown) => (typeof v === "string" ? v : v == null ? "—" : JSON.stringify(v));

function Versions({ event }: { event: AuditEvent }) {
  const d = event.details;
  const reference = (d.reference_data ?? {}) as Record<string, string>;
  const screen = d.prompt_screen as { guardrail_id: string; version: string } | null | undefined;
  const prompts = (d.prompt_versions ?? {}) as Record<string, string>;
  const hashes = (d.sha256s ?? {}) as Record<string, string>;
  return (
    <dl className="facts">
      <div><dt>Policy</dt><dd className="mono">{text(d.policy_version)}</dd></div>
      <div><dt>Rule set</dt><dd className="mono">{text(d.rule_version)}</dd></div>
      <div><dt>Reference data</dt><dd className="mono">{Object.values(reference).join(", ") || "—"}</dd></div>
      <div><dt>Prompt screen</dt><dd className="mono">{screen ? `${screen.guardrail_id} v${screen.version}` : "Off"}</dd></div>
      <div><dt>Model</dt><dd className="mono">{text(d.model_id ?? "None called")}</dd></div>
      <div>
        <dt>Prompt versions</dt>
        <dd className="mono">{Object.entries(prompts).map(([k, v]) => `${k} ${v}`).join(", ") || "—"}</dd>
      </div>
      <div><dt>Evidence coverage</dt><dd className="mono">{typeof d.evidence_coverage === "number" ? d.evidence_coverage.toFixed(2) : "—"}</dd></div>
      <div><dt>Files fingerprinted</dt><dd className="mono">{Object.keys(hashes).length}</dd></div>
    </dl>
  );
}

export function AuditStage({ audit }: StageProps) {
  const completed = [...audit].reverse().find((e) => e.action === "pipeline_completed");
  return (
    <>
      {completed && (
        <Section title="What produced this result" aside="From the latest pipeline audit event">
          <Versions event={completed} />
        </Section>
      )}
      <Section title="Audit trail" aside="Append-only; every action by a person or the system">
        <ol className="audit">
          {audit.map((e, i) => (
            <li key={`${e.timestamp}-${i}`}>
              <time dateTime={e.timestamp} className="mono small muted">{TIME.format(new Date(e.timestamp))}</time>
              <div>
                <strong>{ACTION[e.action] ?? e.action}</strong>
                {e.evidence_id && <span className="mono"> {e.evidence_id}</span>}
                {e.decision && <span>: {e.decision.replace(/_/g, " ")}</span>}
                {e.reason_code && <span className="muted"> ({e.reason_code.replace(/_/g, " ")})</span>}
                <span className="muted"> by {e.actor}{e.role && `, ${e.role}`}</span>
                {e.reason && <p className="muted small">{e.reason}</p>}
              </div>
            </li>
          ))}
        </ol>
      </Section>
    </>
  );
}
