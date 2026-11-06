import { type ReactNode, useEffect, useState } from "react";
import { getControls } from "../../api";
import { duration, PATH_LABEL, usd, when } from "../../catalogue";
import type { AgentEvalRow, Capability, Controls, EvaluationReport, HeldoutReport, ReviewPath, ScoringPolicy, Severity, SignalFamily } from "../../types";
import { interval } from "../../stats";
import { Metric, Section } from "../stages/common";
import { Empty, ErrorNote, Pill } from "../ui";

const CAPABILITY_LABEL: Record<Capability["name"], string> = {
  ocr: "OCR",
  prompt_screen: "Prompt attack screen",
  llm_extraction: "Model extraction",
  ai_team: "AI agent team",
  shadow_score: "Shadow case scorer",
};

const FAMILY_LABEL: Record<SignalFamily, string> = {
  DOC: "Document integrity and forensics",
  FIN: "Financial arithmetic",
  TXN: "Transaction consistency",
  REG: "Registry and identity",
  ENT: "Entity relationships",
  TEMP: "Dates and sequence",
  COMP: "Compliance and completeness",
  SYS: "System health",
};

const ACRONYMS = /\b(uen|po|ocr|bl)\b/gi;
const sentence = (s: string) =>
  s.replace(/_/g, " ").replace(ACRONYMS, (m) => m.toUpperCase()).replace(/^./, (c) => c.toUpperCase());
const pct = (x: number | null) => (x === null ? "-" : x.toFixed(2));
const pathLabel = (p: string) => PATH_LABEL[p as ReviewPath] ?? p;

/** A count, and the 95% interval of the rate behind it, as a metric */
function Counted({ label, of, total, note }: { label: string; of: number; total: number; note: string }) {
  if (total === 0) return <Metric label={label} value="None run" note={`${note} · nothing to count`} />;
  return (
    <Metric label={label} value={`${of} of ${total}`}
      note={`${note} · 95% interval ${interval(of, total)}`} />
  );
}

/** What a score on these packs can and cannot show, said before any number */
function Caveat() {
  return (
    <p className="eval-caveat small">
      <strong>What these numbers show.</strong> The demo packs are synthetic, and the team built them knowing what the
      checks look for, so a full score shows the pipeline catches what was planted. It is not a detection rate on real
      applications. Each count is small, so each comes with the range the true rate could lie in (a 95% interval):
      14 of 14 is consistent with a true rate as low as 78%.
    </p>
  );
}

/** The rules' evaluation: what it shows, its counts, then *children* (the comparison with the AI team, the result
 * that matters most), then the attack and pack tables behind the counts. */
function Evaluation({ report, children }: { report: EvaluationReport; children: ReactNode }) {
  const run = report.attacks.filter((a) => a.detected !== null);
  const caught = run.filter((a) => a.detected).length;
  const { true_positives: tp, false_positives: fp, false_negatives: fn } = report;
  const routed = report.packs.filter((p) => p.path === p.expected_path).length;
  return (
    <>
      <Caveat />
      <div className="metrics wide">
        <Counted label="Attacks caught" of={caught} total={run.length}
          note={run.length < report.attacks.length ? `${report.attacks.length - run.length} not run` : "planted tampering"} />
        <Counted label="Planted findings found" of={tp} total={tp + fn} note="recall" />
        <Counted label="Findings that were expected" of={tp} total={tp + fp} note="precision" />
        <Counted label="Packs routed as expected" of={routed} total={report.packs.length} note="route" />
        <Metric label="Evidence coverage" value={pct(report.evidence_coverage)} note="Findings traceable to a page" />
      </div>
      <p className="muted small">
        Last run {when(report.evaluated_at)}, extraction {report.extraction_mode || "unknown"}, with{" "}
        {Object.entries(report.capabilities).filter(([, on]) => on).map(([c]) => CAPABILITY_LABEL[c as Capability["name"]] ?? c).join(" and ") || "no optional capabilities"}.
      </p>

      {children}

      <Section title="Attack coverage" aside="Each planted tampering and the signal that caught it">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">Attack</th><th scope="col">Pack</th><th scope="col">Caught by</th><th scope="col">Result</th></tr>
            </thead>
            <tbody>
              {report.attacks.map((a) => (
                <tr key={`${a.pack}-${a.attack}`}>
                  <td>{sentence(a.attack)}<span className="muted small block">{a.detail}</span></td>
                  <td className="mono">{a.pack}</td>
                  <td className="mono">{a.signals.join(", ") || "—"}</td>
                  <td>
                    {a.detected === null ? <Pill tone="neutral">Not run</Pill> : a.detected ? <Pill tone="low">Caught</Pill> : <Pill tone="crit">Missed</Pill>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>

      <Section title="Test packs" aside="Expected outcome against what the pipeline did">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr><th scope="col">Pack</th><th scope="col">Risk band</th><th scope="col">Route</th><th scope="col">Readiness</th><th scope="col">Result</th></tr>
            </thead>
            <tbody>
              {report.packs.map((p) => {
                const pass = !p.error && p.band === p.expected_band && p.path === p.expected_path
                  && p.readiness === p.expected_readiness && !p.missed.length && !p.unexpected.length;
                return (
                  <tr key={p.pack}>
                    <td className="mono">{p.pack}</td>
                    <td>{p.band}{p.band !== p.expected_band && <span className="muted"> (expected {p.expected_band})</span>}</td>
                    <td>{pathLabel(p.path)}{p.path !== p.expected_path && <span className="muted"> (expected {pathLabel(p.expected_path)})</span>}</td>
                    <td>{p.readiness}</td>
                    <td>{pass ? <Pill tone="low">As expected</Pill> : <Pill tone="crit">{p.error ? "Error" : "Differs"}</Pill>}</td>
                  </tr>
                );
              })}
              {report.skipped.map((name) => (
                <tr key={name}>
                  <td className="mono">{name}</td>
                  <td colSpan={3} className="muted">Skipped: needs a capability that was off</td>
                  <td><Pill tone="neutral">Skipped</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}

const SEVERITIES: Severity[] = ["low", "medium", "high", "critical"];

const OUTCOME_LABEL: Record<HeldoutReport["packs"][number]["outcome"], string> = {
  caught: "Caught",
  missed: "Missed",
  clear: "Clear",
  false_alarm: "False alarm",
  error: "Error",
};

const OUTCOME_TONE: Record<HeldoutReport["packs"][number]["outcome"], "low" | "crit" | "med" | "neutral"> = {
  caught: "low",
  missed: "crit",
  clear: "low",
  false_alarm: "crit",
  error: "neutral",
};

/** The blind held-out test: the labels were written by someone who did not build the checks, sealed before the
 * run, and revealed only to score it. This is the detection-rate claim the demo packs cannot make. */
function Heldout({ report }: { report: HeldoutReport }) {
  const pilot = report.kind === "pilot";
  return (
    <>
      {pilot && (
        <p className="eval-caveat small">
          <strong>Pilot.</strong> This set was written by the build team to test the renderer and the scoring, not
          independently, so its numbers are development data. An official set needs an independent author, a
          pre-registered plan, and a sealed key.
        </p>
      )}
      <div className="metrics wide">
        <Counted label="Fraudulent packs caught" of={report.caught} total={report.fraud_packs} note="recall, rules route" />
        <Counted label="Legitimate packs sent for review" of={report.false_alarms} total={report.legitimate_packs} note="false alarms" />
        <Metric label="Errors" value={`${report.errors}`} note="packs that did not run" />
        <Metric label="Extraction" value={report.extraction_mode || "unknown"} note="how the fields were read" />
      </div>
      <p className="muted small">
        Test set <span className="mono">{report.test_set}</span> · author {report.author} · {report.fraud_packs}{" "}
        fraudulent and {report.legitimate_packs} legitimate packs · run {when(report.evaluated_at)} · seal{" "}
        <span className="mono">{report.seal.slice(0, 12)}…</span>
      </p>
      {report.gate_runs > 0 && (
        <p className="muted small">
          With the AI team, the log-only gate would send {report.gate_review_runs} of {report.gate_runs} pack runs for
          review; {report.verified_lead_runs} runs carried a code-verified lead. The gate is not applied; the rules'
          route stands.
        </p>
      )}
      <Section title="Every pack, misses included" aside="The label is revealed here; the run never saw it">
        <div className="table-wrap">
          <table className="data">
            <thead>
              <tr>
                <th scope="col">Pack</th>
                <th scope="col">Label</th>
                <th scope="col">What it carries</th>
                <th scope="col">Route</th>
                <th scope="col">Result</th>
              </tr>
            </thead>
            <tbody>
              {report.packs.map((p) => (
                <tr key={p.pack}>
                  <td className="mono">{p.pack}<span className="muted small block">{p.company}</span></td>
                  <td>{p.label}</td>
                  <td className="mono">{p.typology ?? p.quirk ?? "—"}</td>
                  <td>{pathLabel(p.path)}</td>
                  <td><Pill tone={OUTCOME_TONE[p.outcome]}>{OUTCOME_LABEL[p.outcome]}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
    </>
  );
}

/** How findings become points, a band and a route: the policy the server runs with */
function Scoring({ policy }: { policy: ScoringPolicy }) {
  return (
    <>
      <div className="metrics wide">
        {SEVERITIES.map((s) => (
          <Metric key={s} label={sentence(s)} value={`+${policy.severity_points[s]}`} note="points per finding" />
        ))}
      </div>
      <ul className="reasons small">
        <li>Only fraud signals score. Compliance and completeness findings make a pack incomplete, which forces at least standard review but adds no points.</li>
        <li>Each check scores once per document, at its most severe finding; repeats are counted, not added. The total is capped at 100.</li>
        <li>
          Low up to {policy.band_low_max} goes to fast track ({policy.qa_sample_percent}% sampled for QA), medium up to{" "}
          {policy.band_medium_max} to standard review, high from {policy.band_medium_max + 1} to enhanced review.
        </li>
        <li>
          Forces band High at high severity: <span className="mono">{policy.forces_high.join(", ") || "none"}</span>.
          Forces enhanced review whatever the score: <span className="mono">{policy.forces_enhanced_review.join(", ")}</span>.
        </li>
        <li>Each check sets a finding's severity from what it found; the weights are judgement-based assumptions, not calibrated on real cases.</li>
      </ul>
    </>
  );
}

interface PackRuns {
  pack: string;
  planted: boolean;
  rules: string;
  /** Runs the gate would send for review (the rules' route when the gate did not decide) */
  review: number;
  runs: number;
}

/** Each pack's rule route beside the route the AI team's gate would take in each run */
function packRuns(report: EvaluationReport, row: AgentEvalRow): PackRuns[] {
  const rules = new Map(report.packs.map((p) => [p.pack, p.path]));
  const byPack = new Map<string, AgentEvalRow["packs"]>();
  row.packs.forEach((p) => byPack.set(p.pack, [...(byPack.get(p.pack) ?? []), p]));
  return [...byPack].map(([pack, runs]) => {
    const route = rules.get(pack) ?? "-";
    return {
      pack,
      planted: runs.some((r) => r.planted),
      rules: route,
      review: runs.filter((r) => (r.would_route ?? route) !== "fast_track").length,
      runs: runs.length,
    };
  });
}

/** The question the AI team has to answer: which planted frauds reach a person with it that would not without it,
 * and whether it sends clean packs to a person for nothing. */
function Comparison({ report, row }: { report: EvaluationReport; row: AgentEvalRow }) {
  const packs = packRuns(report, row);
  const planted = packs.filter((p) => p.planted);
  const clean = packs.filter((p) => !p.planted);
  const sum = (ps: PackRuns[], f: (p: PackRuns) => number) => ps.reduce((n, p) => n + f(p), 0);
  const rulesCaught = planted.filter((p) => p.rules !== "fast_track").length;
  const aiCaught = sum(planted, (p) => p.review);
  const aiRuns = sum(planted, (p) => p.runs);
  const cleanKept = sum(clean, (p) => p.runs - p.review);
  const cleanRuns = sum(clean, (p) => p.runs);
  const runs = row.runs ?? 1;
  return (
    <>
      <div className="metrics wide">
        <Counted label="Planted frauds sent for review, rules alone" of={rulesCaught} total={planted.length} note="packs" />
        <Counted label="With the AI team" of={aiCaught} total={aiRuns} note={runs > 1 ? `pack runs (${runs} runs each)` : "packs, one run"} />
        <Counted label="Clean packs kept on fast track" of={cleanKept} total={cleanRuns} note="no review for nothing" />
      </div>
      <p className="muted small">
        With the AI team means the route its log-only gate would take: a fact code confirmed raises fast track to
        standard review, and nothing lowers a route. The gate is not applied; the rules' route stands.
      </p>
      <div className="table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Pack</th>
              <th scope="col">Planted fraud</th>
              <th scope="col">Rules alone</th>
              <th scope="col">With the AI team</th>
            </tr>
          </thead>
          <tbody>
            {packs.map((p) => {
              const good = p.planted ? p.review : p.runs - p.review;
              const tone = good === p.runs ? "low" : good === 0 ? "crit" : "med";
              return (
                <tr key={p.pack}>
                  <td className="mono">{p.pack}</td>
                  <td>{p.planted ? "Yes" : <span className="muted">No</span>}</td>
                  <td>
                    {pathLabel(p.rules)}
                    {p.planted && p.rules === "fast_track" && <span className="muted small block">missed by the rules</span>}
                  </td>
                  <td>
                    <Pill tone={tone}>
                      {p.planted
                        ? `Review in ${p.review} of ${p.runs} run${p.runs === 1 ? "" : "s"}`
                        : `Fast track in ${p.runs - p.review} of ${p.runs} run${p.runs === 1 ? "" : "s"}`}
                    </Pill>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

/** Planted patterns found, as "k of n" with the 95% interval on hover */
function share(row: AgentEvalRow, found: (p: AgentEvalRow["packs"][number]) => string[]) {
  const expected = row.packs.reduce((n, p) => n + p.expected_leads.length, 0);
  const hit = row.packs.reduce((n, p) => n + p.expected_leads.filter((l) => found(p).includes(l)).length, 0);
  return <span title={`95% interval ${interval(hit, expected)}`}>{hit} of {expected}</span>;
}

/** One row per model setup, so a challenger is read against the champion */
function AgentEvaluation({ rows }: { rows: AgentEvalRow[] }) {
  return (
    <div className="table-wrap">
      <table className="data">
        <thead>
          <tr>
            <th scope="col">Setup</th>
            <th scope="col" className="num">Runs</th>
            <th scope="col" className="num" title="Planted patterns the agents reported as supported, over every run">Patterns reported</th>
            <th scope="col" className="num" title="Planted patterns code verified, over every run">Facts confirmed by code</th>
            <th scope="col" className="num">Triage accuracy</th>
            <th scope="col" className="num" title="Supported leads matching nothing planted, before code checks">Noise</th>
            <th scope="col" className="num" title="Leads code verified that match nothing planted: the false flags an analyst would get">Flag noise</th>
            <th scope="col" className="num">Withheld rate</th>
            <th scope="col" className="num" title="Planted frauds the shadow scorer or the log-only gate would send to fast track">Miss rate</th>
            <th scope="col" className="num" title="Packs the log-only gate would send to fast track">Auto-clear rate</th>
            <th scope="col" className="num" title="Planted frauds the shadow scorer alone would send to fast track">Scorer miss rate</th>
            <th scope="col" className="num" title="How often the shadow scorer's route is the rule route">Scorer agrees with rules</th>
            <th scope="col" className="num" title="How often the shadow scorer's route is the pack's expected route">Scorer matches expected</th>
            <th scope="col" className="num">Cost per case</th>
            <th scope="col" className="num">Time per case</th>
            <th scope="col">Evaluated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.supervisor}-${r.specialist}`}>
              <td>
                <span className="mono">{r.supervisor}</span>
                <span className="muted small block">+ <span className="mono">{r.specialist}</span></span>
                {r.shadow && <span className="muted small block">scorer <span className="mono">{r.shadow}</span></span>}
              </td>
              <td className="num mono">{r.runs ?? 1}</td>
              <td className="num mono">{share(r, (p) => p.found_leads)}</td>
              <td className="num mono">{r.verified_recall === null ? "-" : share(r, (p) => p.verified_leads)}</td>
              <td className="num mono">{pct(r.triage_accuracy)}</td>
              <td className="num mono">{r.noise}</td>
              <td className="num mono">{r.flag_noise ?? "-"}</td>
              <td className="num mono">{pct(r.withheld_rate)}</td>
              <td className="num mono">{pct(r.miss_rate)}</td>
              <td className="num mono">{pct(r.auto_clear_rate)}</td>
              <td className="num mono">{pct(r.shadow_miss_rate)}</td>
              <td className="num mono">{pct(r.shadow_champion_agreement)}</td>
              <td className="num mono">{pct(r.shadow_truth_agreement)}</td>
              <td className="num mono">{usd(r.usd_per_case)}</td>
              <td className="num mono">{duration(Math.round(r.seconds_per_case * 1000))}</td>
              <td className="nowrap muted">{when(r.evaluated_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ControlsScreen() {
  const [controls, setControls] = useState<Controls | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getControls().then(setControls).catch((e: Error) => setError(e.message));
  }, []);

  if (!controls) {
    return (
      <main className="page">
        {error ? <ErrorNote>Could not load controls: {error}</ErrorNote> : <div className="skeleton block-skeleton" aria-busy="true" />}
      </main>
    );
  }

  const families = Object.entries(
    controls.signals.reduce<Record<string, typeof controls.signals>>(
      (acc, s) => ({ ...acc, [s.family]: [...(acc[s.family] ?? []), s] }), {},
    ),
  );
  const biggest = Math.max(...families.map(([, list]) => list.length));
  /** The most recently evaluated model setup is the one compared with the rules */
  const latest = [...controls.agent_eval].sort((a, b) => a.evaluated_at.localeCompare(b.evaluated_at)).at(-1);

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Model and controls</h1>
          <p className="muted">How we know the pipeline works, what it runs with, and what it costs.</p>
        </div>
        <div className="chips">
          <Pill tone="neutral">Policy <span className="mono">{controls.policy_version}</span></Pill>
          <Pill tone="neutral">Rules <span className="mono">{controls.rule_version}</span></Pill>
        </div>
      </div>

      <Section title="Evaluation">
        {controls.evaluation ? (
          <Evaluation report={controls.evaluation}>
            <Section title="Rules alone and with the AI team" aside="Does each planted fraud reach a person, and does a clean pack stay on fast track">
              {latest ? (
                <Comparison report={controls.evaluation} row={latest} />
              ) : (
                <Empty title="Not compared yet">
                  Run <strong>Actions › Run evaluation</strong> with the AI team ticked (about US$3 a run) to see which
                  planted frauds reach a person only because of it.
                </Empty>
              )}
            </Section>
          </Evaluation>
        ) : (
          <Empty title="No saved evaluation yet">
            On GitHub, run <strong>Actions › Run evaluation</strong> to score the deployed pipeline against every test
            pack. The rule checks cost nothing to evaluate.
          </Empty>
        )}
      </Section>

      <Section title="Agent evaluation" aside="The AI team on every test pack, one row per model setup">
        {controls.agent_eval.length ? (
          <AgentEvaluation rows={controls.agent_eval} />
        ) : (
          <Empty title="No agent evaluation yet">
            Run <strong>Actions › Run evaluation</strong> with the AI team ticked to score it against every test pack.
          </Empty>
        )}
      </Section>

      <Section title="Held-out test" aside="Scored on a set the checks were not built against">
        {controls.heldout ? (
          <Heldout report={controls.heldout} />
        ) : (
          <Empty title="No held-out set scored yet">
            A blind held-out set needs an independent author, a pre-registered plan and a sealed key. Until one is
            scored, the numbers above are the demo packs only, which were built knowing the checks.
          </Empty>
        )}
      </Section>

      <div className="two-col">
        <Section title="Capabilities on this server">
          <ul className="capabilities">
            {controls.capabilities.map((c) => (
              <li key={c.name}>
                <span>
                  <strong>{CAPABILITY_LABEL[c.name]}</strong>
                  <span className="muted small block mono">{c.detail}</span>
                </span>
                <Pill tone={c.enabled ? "low" : "neutral"}>{c.enabled ? "On" : "Off"}</Pill>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="AWS spend" aside="Estimate in USD from list prices; the bill is the record">
          <div className="metrics">
            <Metric label="Per case" value={usd(controls.cost.usd_per_case)} />
            <Metric label="Cases metered" value={controls.cost.cases} />
            <Metric label="Total" value={usd(controls.cost.usd_total, 2)} />
          </div>
          <dl className="facts">
            {Object.entries(controls.reference_data).map(([k, v]) => (
              <div key={k}><dt>{sentence(k)} data</dt><dd className="mono">{v}</dd></div>
            ))}
          </dl>
        </Section>
      </div>

      <Section title="How scoring works" aside="The policy this server runs with">
        <Scoring policy={controls.scoring} />
      </Section>

      <Section title="Signal library" aside={`${controls.signals.length} signals, each mapped to one check`}>
        <div className="families">
          {families.map(([family, list]) => (
            <details key={family} className="family">
              <summary>
                <span className="mono family-code">{family}</span>
                <span>{FAMILY_LABEL[family as SignalFamily]}</span>
                <span className="meter" aria-hidden="true"><i className="brand" style={{ width: `${(list.length / biggest) * 100}%` }} /></span>
                <span className="mono">{list.length}</span>
              </summary>
              <table className="data">
                <tbody>
                  {list.map((s) => (
                    <tr key={s.signal_id}>
                      <td className="mono nowrap">{s.signal_id}</td>
                      <td>
                        {s.name}
                        {controls.scoring.forces_enhanced_review.includes(s.check_id) && <> <Pill tone="high">Forces enhanced review</Pill></>}
                        {controls.scoring.forces_high.includes(s.check_id) && <> <Pill tone="high">Forces High at high severity</Pill></>}
                        <span className="muted small block">{s.remedy}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </details>
          ))}
        </div>
      </Section>

      {controls.feedback.length > 0 && (
        <Section title="Analyst overrides" aside="How often each check's flags are dismissed">
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr><th scope="col">Check</th><th scope="col" className="num">Confirmed</th><th scope="col" className="num">Dismissed</th><th scope="col" className="num">Dismiss rate</th></tr>
              </thead>
              <tbody>
                {controls.feedback.map((f) => (
                  <tr key={f.check_id}>
                    <td className="mono">{f.check_id}</td>
                    <td className="num">{f.confirmed}</td>
                    <td className="num">{f.dismissed}</td>
                    <td className="num mono">{Math.round(f.dismiss_rate * 100)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </main>
  );
}
