# AI Agent Team — Design

Date: 2026-10-04 · Status: approved

## 1. Purpose

Add a multi-agent AI investigation team on Amazon Bedrock to the existing
pipeline, as a value-add the assessment panel can see working live. The team
investigates what the deterministic checks found, hunts for patterns no rule
encodes, and writes a case memo. It never changes the score, the route or the
case status.

Principle (unchanged, extended): *AI investigates. Rules quantify. Humans decide.*

### What the team adds that rules cannot

1. **Triage.** For each risky finding: does it stand (supported), is there an
   ordinary explanation (refuted), or is it unresolved, with cited tests. (Clearing false positives is the largest
   cost in fraud/AML operations.)
2. **Gap hunting.** Patterns no rule encodes: third-party payment, related
   parties, round-tripping, business-profile mismatch, narrative inconsistency.
3. **Synthesis.** Link scattered findings into one account of the case and
   draft precise questions for the applicant.

### Non-goals

- Agents do not run the deterministic checks or decide which checks run.
- Agents do not compute, adjust or override the score, readiness or route.
- No write tools that change a case.
- No managed agent platform in this build (AgentCore is a future phase).

## 2. Framing

The team runs the case. The 36 checks are **mandatory deterministic tools**:
the harness runs every one on every case before the AI team starts, and agents
cannot skip them. Scoring is a **deterministic, validated tool**: the
Supervisor can read the score (`get_score`) but has no way to change it.
Hard guarantees are enforced in code, never by prompt (OWASP LLM: prompt
injection, excessive agency; MAS proposed AI Risk Management Guidelines:
validation and human oversight for risk-management uses).

## 3. Pipeline placement

Stage 5 "Agents" runs two pipeline steps:

| Step id | What | Always runs |
|---|---|---|
| `agents` | Rule sweep: six rule agents, 36 checks → findings (unchanged) | yes |
| `ai_team` | AI agent team over the sweep's results → leads + memo | when `AGENTS_ENABLED` |

Stage 10 Semantic review is removed; the Supervisor's memo replaces it. The UI
shows 10 stages. `ASSESS_STAGES` is:
`ocr, intake, prompt_screen, extract, agents, score, readiness, route, ai_team, shadow_score, projections`
(+ `record` in `run()`). The UI groups `agents` and `ai_team` under stage 5 and
`score` and `shadow_score` under stage 6.

`ai_team` status: `ran` · `off` (disabled) · `failed` (error; stages 6-10
still complete). `StageReport.status` gains `failed`. The team runs on every
case when enabled, including cases with no findings: gap hunting looks for
what the rules did not flag, so a case the rules call clean is exactly where
it matters.

Score, readiness and route are computed before `ai_team` and are fixed before
any AI runs; the Supervisor's `get_score` tool calls the same pure `score()`
and `route()` functions (the score as a deterministic tool, section 2). Tests
prove the two agree, and that a failed team leaves the score, readiness and
route equal to a team-off run.

## 4. Orchestration: plan → fan out → verify leads → reconcile → verify memo

Fixed shape, model-driven content (orchestrator-worker workflow). Bounded call
count; easy to audit and test.

1. **Plan** — Supervisor tool loop (`get_case_overview`, `get_score`), ending
   with `submit_plan`: one brief per specialist naming the findings to triage
   and what to look at.
2. **Fan out** — our code runs all four specialists in parallel threads (gap
   hunting is mandatory, so none is skipped). Each is a Converse tool loop
   capped at `AGENT_MAX_STEPS` (10) successful evidence tool calls. A failed
   call does not count (a mistyped argument costs nothing) but has its own cap
   of the same size, so a confused agent still stops; recording a lead or
   drafting a question never counts (`AGENT_TOKEN_BUDGET` bounds the rest).
3. **Verify leads** — deterministic gate (section 7), before the Supervisor
   sees them, so an unsupported lead cannot shape the memo.
4. **Reconcile** — Supervisor, given the kept leads and drafted questions,
   ends with `submit_memo`: observations (linking leads across specialists by
   citing several) and applicant questions.
5. **Verify memo** — same gate for memo points; withheld points are counted.

**Hub-and-spoke.** Specialists receive only their brief, a code-built case
index (the documents' exact doc_ids and the invoice numbers: case data, not
another agent's output) and their own tool results. They never see each
other's messages, tool results or leads, and cannot message each other. Only
the Supervisor's reconcile step combines specialist output. Our code is the
switchboard: the Supervisor does not call specialists directly, so every
handoff is a structured, logged record.

## 5. Agents and tools

All tools are read-only Python functions over the stored case. Every call
gets an id `R01…`; leads must cite them. Each specialist sees only its own
tools (least privilege). Tool schemas use JSON Schema in the shape AgentCore
Gateway (MCP) expects, so a later move to AgentCore is deployment work.

**Closed-world arguments.** Each agent's tool schemas are built per case
(`specs_for(agent, finish, case)`): every argument whose valid values the case
knows is an `enum` of exactly those values — `doc_id` (non-form documents for
`get_page_text`, all for `get_forensic_facts`, invoice/PO/BL ids and numbers for
`get_document_fields`), invoice numbers (`get_chain`, `get_timeline.ref`), the
UENs `get_parties` lists (`registry_lookup.uen`; `name` stays open), and in
`record_lead` the specialist's own patterns, and the case's risky finding ids
and verdicts (with no risky finding, `kind` is `pattern` only and `finding_ids`
and `verdict` are left out). One table (`tools._CASE_VALUES`) is the source of
both the enums and `call_tool`'s check. An empty enum is never sent: the
argument is dropped, or the tool when the argument is required. Citations stay
free text (the verifier checks them). Without a case the schemas are open, so
prompt versions do not change from case to case. Every schema object has
`additionalProperties: false` and no length or range keywords.

| Agent | Tools |
|---|---|
| Supervisor | `get_case_overview` (includes the `parties` block, so it can brief KYC), `get_score` (read-only) |
| Forensics | `get_page_text(doc_id, page)` · `get_forensic_facts(doc_id)` (the document's existing forensic and OCR findings, PDF producer and dates, OCR confidence; read from the case, never a re-run) |
| Transaction | `get_chain(invoice_number)` · `search_bank_transactions(amount?, tolerance_pct?, date_from?, date_to?, text?)` · `get_document_fields(doc_id)` (the doc_id or the document's own invoice, PO or BL number) |
| KYC & Registry | `get_parties()` → applicant name and UEN, guarantor and signatory names (no id numbers), and the seller, buyer, shipper and consignee of each invoice, PO and bill of lading (UEN where extracted), each with its doc_id · `registry_lookup(uen? \| name?)` → name, status, incorporated, officers (name, role) · `sanctions_search(name)` |
| Timeline | `get_timeline(ref?)` · `get_incorporation()` |
| All specialists | `record_lead(...)` · `draft_question(text, cites)` |

### Specialist jobs

- **Triage** every risky finding in its domain → verdict.
- **Gap hunt** its domain's pattern list:
  - Transaction: `third_party_payment`, `round_trip`, `split_payment`
  - KYC & Registry: `related_party`, `business_mismatch`
  - Forensics: `document_inconsistency`
  - Timeline: `sequence_anomaly`

## 6. Records

Leads are a **separate record type from Evidence**. `score()` takes findings
only, so a lead has no route into the score. Enforced by a test: score and
route are identical with the AI team on and off.

```
Lead:        lead_id L01…, agent, kind ("triage"|"pattern"), finding_ids (triage),
             pattern (pattern), hypothesis, tests [{cites [R…], expected, observed}],
             status ("supported"|"refuted"|"unresolved"), missing_evidence,
             outcome ("kept"|"withheld"), withheld_reason,
             strength ("corroborated"|"unverified", set by code); cites = its tests' R ids
             (the InvestigationLead contract: target architecture spec, section 3)
ToolCall:    call_id R01…, agent, tool, args, summary (short, masked), duration_ms
AgentTeam:   status, plan {specialist: brief|None}, tool_calls, leads,
             memo {observations[ReviewPoint], questions[ReviewPoint]},  # cites E… and L… ids
             models {supervisor, specialist}, prompt_versions, error?
```

- `CaseResult.review` is replaced by `CaseResult.ai_team: AgentTeam | None`.
- Full tool results (may contain page text) live server-only in
  `cases/<id>/agent_trace.json`, like `ocr_lines.json`; the API exposes only
  summaries.

## 7. Safety controls (code-enforced)

- **Screened text only.** `get_page_text` returns `intake.model_view` text (what
  the guardrail screened in stage 3) and refuses any document the prompt screen
  flagged: "quarantined: prompt attack detected".
- **Tool results are untrusted data**, wrapped and labelled as such in prompts.
- **Closed-world arguments** (§5): a document id, invoice number, UEN, finding
  id, pattern or verdict the case does not have cannot be expressed in the
  schema, and is refused by `call_tool` with "<arg> must be one of: …".
- **Limits:** `AGENT_MAX_STEPS` successful evidence calls (and as many failed
  ones) per specialist; `AGENT_MAX_RESULT_CHARS` per tool
  result; `AGENT_TOKEN_BUDGET` per case (team stops, records "budget reached").
- **Masking:** tool results pass identifiers through `privacy.mask_id`.
- **Verifier withholds a lead** that has no tests or a test without citations,
  cites an `R` id its agent did not receive or that is not evidence, cites an
  unknown finding, has a pattern outside its specialist's list, is supported
  with no test whose observed differs from its expected, is unresolved with no
  missing evidence, uses verdict words (fraud, crim*, launder, guilt, approv*,
  reject, declin*, recommend), or states a number not in what it cites (each
  test: its own citations) or an identifier (INV-1001, BL-3002, a UEN) in
  neither its citations nor the case index (documents, invoice, purchase
  order and bill of lading numbers). The same rules run when `record_lead` is
  called: a failing lead is refused with the reason as a tool error, so the
  agent can correct it, and the refusal stays in the trace. The team verifies
  every lead again at the end; withheld leads stay visible with the reason.
  Kept supported leads get a code-assigned strength.
- **Isolation:** any exception in `ai_team` → status `failed` with reason;
  pipeline continues on rule findings.

## 8. Bedrock and models

- Real Bedrock at runtime. `.env`: `AGENTS_ENABLED=true`,
  `AGENT_SUPERVISOR_MODEL=global.anthropic.claude-sonnet-4-6`,
  `AGENT_SPECIALIST_MODEL=global.anthropic.claude-haiku-4-5-20251001-v1:0`
  (the models this account can call, probed 2026-10-04).
- `llm_client` gains a multi-turn `converse_turn(model_id, system, messages,
  tools, ...)` — still the only Bedrock wrapper; meters every call
  (`log_usage("bedrock", "ai_team_<agent>", case_id, ...)`).
- Request shape portable across Claude and Amazon Nova (no provider-specific
  fields); `maxTokens` always set.
- **Strict tool use** (`toolSpec.strict`) on supporting models only:
  `AGENT_STRICT_TOOL_MODELS` (comma-separated, default Haiku 4.5's profile);
  Sonnet 4.6 and Nova do not support it and would reject the request. Strict
  mode makes the model's tool input always match the closed schema. The models
  that ran strict are recorded as `ai_team.strict_tools` in the audit.
- `usage.stage_of` maps `ai_team_*` → `ai_team`. Price table adds Haiku 4.5,
  Opus 4.6 and Nova Pro from the AWS Price List API.
- Data residency: these are global/APAC profiles; acceptable for synthetic data.
  Singapore-only needs Sonnet 5 enabled by AWS (outstanding request).

## 9. Audit

`pipeline_completed.details.ai_team`: status, models per role, prompt versions,
tool-call count, leads kept/withheld, error, strict_tools. `model_ledger` covers agent prompts.

## 10. UI

- **Stage 5:** "Mandatory tools: rule sweep" (agent table + findings, unchanged)
  then "AI agent team": status line (models, time, cost, calls) · Supervisor
  plan cards · per-specialist trace tabs (tool, args, summary; page-text calls
  link to the document pane) · leads (labelled "AI lead", kind, status pill,
  strength; each test's expected and observed with its cited `R` ids jumping
  to the trace) · clear checks (refuted, collapsed, never counted as flags) ·
  withheld leads (collapsed, with reasons) · memo.
- **Rail:** stage 5 count "findings · AI leads"; 10 stages.
- **Stage 9:** "AI leads to review" under the rule tasks.
- **Live run:** stage 5 row shows current activity ("Transaction specialist:
  search_bank_transactions"). `jobs` progress gains an optional `detail` string.
- **Controls:** AI team models under capabilities; "Agent evaluation" table
  (one row per model setup).
- Semantic review panel, stage and catalogue entry removed.

## 11. Test packs ("beyond rules")

Generated by `data_factory`; ground truth gains `expected_leads` /
`expected_triage`. The rule evaluation runs these packs too (no `requires`),
which proves the rules really are silent; only `agent_eval` scores the leads.

| Pack | Planted | Rules | AI team should |
|---|---|---|---|
| `third_party_payment` | Credit of the exact invoice amount from a non-buyer company | silent (rule matches amount + date only) | pattern `third_party_payment` |
| `related_party` | Buyer's registered director = applicant's director (registry data) | silent | pattern `related_party` |
| `round_trip` | Debit to a supplier returning days later as "customer receipts" of a similar amount | silent | pattern `round_trip` |
| `innocent_split` | Invoice paid in two instalments | `XDOC_INVOICE_BANK` fires | triage `refuted` (an ordinary explanation) citing both credits; pattern `split_payment` |

Each pack's rule expectations are verified while implementing; if a rule fires
unexpectedly, the pack is adjusted to test what it claims.

## 12. Agent evaluation (champion vs challenger)

`python -m gldb.agent_eval [--supervisor M] [--specialist M] [--save]` runs all
packs with the team on. Metrics: pattern recall and noise (kept supported
pattern leads matching nothing planted, notably on `clean`; a clear check is
neither), triage accuracy (status on the expected check), withheld rate, cost and
time per case. `--save` appends a row keyed by model setup to
`logs/agent_eval.json`; Controls shows champion (Sonnet 4.6 + Haiku 4.5) next to
challenger (Sonnet 4.6 + Nova Pro). Estimated $1-3 per live setup; measured and
approved before running.

## 13. Testing

Offline, with a scripted fake Bedrock client that plays back tool calls:
loop (step cap, budget stop, tool error), each tool, quarantine, every verifier
rule, failure isolation, score invariance (on vs off), hub-and-spoke isolation
(each specialist's requests hold only its own brief, the case index and its `R` ids), API fields, removal of
the review; `npm run build`. Live tests opt-in (`RUN_LIVE=1`).

## 14. Removed

`gldb/semantic_review.py` (grounding logic moved to the verifier),
`SEMANTIC_REVIEW_ENABLED`, `REVIEW_PROMPT_VERSION`, `SemanticReview` on the case,
its tests, the stage, panel and frontend types. `ReviewPoint` is kept for the memo.

## 15. Future phases (not in this build)

1. **Trained fraud model, shadow mode first.** Gradient-boosted model on
   existing features with per-case reason codes; SageMaker (training, Model
   Registry, Clarify, Model Monitor); model card and validation report; scores
   beside the rules without feeding the decision until validated. Synthetic
   data proves the governed pipeline only; real detection value needs
   anonymised labelled cases from the sponsor bank (ask early). Unsupervised
   anomaly detection as a label-free alternative.
2. **Strands Decider 2B gate** (local, open-source decision model) for triage
   and tool-call gating, measured as a challenger.
3. **AgentCore deployment** (Runtime, Gateway, Policy) of the same agents.
4. **Promote lead to finding** by an analyst, re-scoring under their name.
