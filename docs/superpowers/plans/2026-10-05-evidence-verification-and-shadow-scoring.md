# Evidence Verification and Shadow Scoring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Code decides which AI leads are real evidence (verified, corroborated), only those reach analysts, memo, applicant questions and a log-only shadow scorer, and the rule score is fixed before any AI runs.

**Architecture:** Leads gain typed subjects (invoice, bank rows, counterparty) from closed per-case enums. Pure pattern checks in `gldb/ai_team/checks.py` read the extracted pack, register and findings through those subjects and set `evidence_status`. The team gates the memo and questions on it. A new `shadow_score` step asks a model for a route from structured inputs only, and a log-only gate records what it would do.

**Tech Stack:** Python 3.13, pydantic, pytest, Bedrock Converse (scripted fakes in tests), React + TypeScript (Vite).

**Spec:** `docs/superpowers/specs/2026-10-05-evidence-verification-and-shadow-scoring-design.md`

## Global Constraints

- No AWS calls in tests unless `RUN_LIVE=1`; never run anything that loads the real `.env` outside pytest; do not start or stop servers.
- `gldb/config.py` and `.env.example` hold the user's uncommitted `OCR_MAX_WORKERS` edits: stage only your own hunks (`git add -p`); `git diff --cached` must not contain `OCR_MAX_WORKERS`, and `git diff` must still show it afterwards. Never touch `.env`.
- No `# type: ignore` / `# ty: ignore`. Keep AGENTS.md and CLAUDE.md identical (not touched here).
- Thresholds and lists live in `gldb/config.py`: `PAYER_SAME_THRESHOLD = 0.90`, `PAYER_DIFFERENT_THRESHOLD = 0.60`, `PAYMENT_CHANNELS = ("GIRO", "FAST", "TT", "IBG", "MEPS")`, `GENERIC_PAYERS = ("CUSTOMER RECEIPTS", "CASH DEPOSIT", "TRANSFER", "INTEREST", "REVERSAL")`, `SHADOW_SCORE_ENABLED` (env, default on when `AGENTS_ENABLED`), `SHADOW_SCORER_MODEL` (env, default `global.anthropic.claude-haiku-4-5-20251001-v1:0`).
- Pattern checks never read lead prose (`hypothesis`, `tests`, `missing_evidence`) or tool result text, and never call a model.
- An unmet precondition gives `unverified`, never a guess.
- Commits end with a blank line and `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Verification per task: the task's tests, then `.venv/bin/pytest -q` and `cd frontend && npm run build` clean.

## Review Focus

1. A bank description in an unknown format, or a generic payer: must be `unverified`, never `verified` (test in Task 3).
2. A payer name close to the buyer's (similarity between 0.60 and 0.90): `unverified` (Task 3).
3. A counterparty matching two register entries equally: `unverified` (Task 3).
4. A lead whose subject id never appeared in its cited results: refused at record time (Task 1).
5. A stored case from before this change (old `status`, `strength`): still loads and shows (Task 1).

---

### Task 1: Lead fields, typed subjects, bank row ids

**Files:**
- Modify: `gldb/schemas.py` (Lead, LeadStatus, Strength, the legacy shim)
- Create: `gldb/ai_team/checks.py` (only `bank_rows` and `PATTERN_SUBJECTS` in this task)
- Modify: `gldb/ai_team/tools.py` (search rows, record_lead schema, `_CASE_VALUES`, `_record_lead`)
- Modify: `gldb/ai_team/verify.py` (`strength` -> sets `evidence_status`), `gldb/ai_team/team.py`, `gldb/agent_eval.py`, `gldb/ai_team/prompts.py`
- Modify: `frontend/src/types.ts`, `catalogue.ts`, `components/ui.tsx`, `components/stages/ai-team/Leads.tsx`, `components/workspace/stageStatus.ts`
- Test: `tests/test_ai_team_tools.py`, `tests/test_ai_team_verify.py`, `tests/test_ai_team.py`, `tests/test_agent_eval.py`, `tests/test_api.py`

**Interfaces:**
- Produces:
  - `InvestigationStatus = Literal["supported", "refuted", "unresolved"]`.
  - `EvidenceStatus = Literal["verified", "corroborated", "unverified", "contradicted"]`.
  - `Lead` fields `investigation_status`, `evidence_status: EvidenceStatus | None`, `check_facts: dict[str, str] = {}`, `check_reason: str | None`, `invoice_number: str | None`, `transaction_ids: list[str] = []`, `counterparty: str | None`. `LeadStatus` and `Strength` are removed.
  - `checks.bank_rows(pack: ExtractedPack) -> list[tuple[str, BankStatement, Transaction]]`: ids `T01`... over statements in pack order and rows in statement order.
  - `checks.PATTERN_SUBJECTS: dict[Pattern, tuple[str, ...]]` = `{"third_party_payment": ("invoice_number", "transaction_ids"), "sequence_anomaly": ("invoice_number",), "related_party": ("counterparty",)}`.

- [ ] **Step 1: Write failing tests**
  - `test_search_rows_carry_stable_transaction_ids`: rows from `search_bank_transactions` have `"id"` `T01`...; the same row has the same id in two searches.
  - `test_record_lead_subjects_are_closed_per_case`: `invoice_number` enum equals the case's invoice numbers; `transaction_ids.items.enum` equals the `bank_rows` ids; `counterparty` enum equals the distinct counterparty names of `get_parties`.
  - `test_a_checked_pattern_needs_its_subject`: `third_party_payment` without `transaction_ids` gives error `"third_party_payment needs invoice_number and transaction_ids"`.
  - `test_a_subject_id_must_appear_in_a_cited_result`: `transaction_ids=["T05"]` where no cited result contains `"T05"` gives error `"subject T05 is not in its cited results"`.
  - `test_lead_stored_before_the_rename_still_loads`: `{"status": "supported", "strength": "corroborated", ...}` loads with `investigation_status == "supported"` and `evidence_status == "unverified"`; the pre-contract shape from the existing test still loads.
  - Rename assertions in the existing tests (`status` to `investigation_status`, `strength` to `evidence_status`; the corroborated/unverified expectations become `"unverified"` for every kept lead in this task).
- [ ] **Step 2: Run them; they fail.** `.venv/bin/pytest -q tests/test_ai_team_tools.py tests/test_ai_team_verify.py`
- [ ] **Step 3: Implement.**
  - Subject provenance: `transaction_ids` and `invoice_number` must each appear in the lead's cited results; `counterparty` too.
  - Required-subject and provenance checks go into `verify._contract_reason`, so they run at record time and at the end.
  - Prompt: one line per checked pattern naming its subject fields.
  - UI: rename fields; `LeadTags` shows `evidence_status` (labels: Verified, Corroborated, Not checked, Contradicted by code).
- [ ] **Step 4: Full suite and frontend build pass.**
- [ ] **Step 5: Commit** `AI team: investigation and evidence status, typed lead subjects`

### Task 2: One ordering policy for rules and checks

**Files:**
- Create: `gldb/ordering.py`
- Modify: `gldb/checks.py` (`check_temp_trade_sequence`, `check_temp_before_incorporation` read the policy)
- Test: `tests/test_ordering.py`; existing rule tests unchanged and passing

**Interfaces:**
- Produces:
  - `TradeDates(po: date | None, shipped: date | None, invoice: date, payment: date | None, incorporated: date | None)`.
  - `Violation(rule: str, earlier: str, later: str)`, where `rule` is the check id the deterministic rule reports.
  - `violations(d: TradeDates) -> list[Violation]`.
  - `undetermined(d: TradeDates) -> list[str]`: orderings with no policy, e.g. `"payment before invoice"`.
  - `trade_dates(invoice: Invoice, pack: ExtractedPack, incorporated: date | None) -> TradeDates`, using `matching_po`, the bill of lading whose `invoice_reference` is the invoice number, and the earliest credit whose description contains the invoice number.

- [ ] **Step 1: Failing tests:**
  - `test_valid_orderings`: PO, shipment, invoice; PO, invoice, shipment; invoice on the PO date. Each gives `violations == []`.
  - `test_invoice_or_shipment_before_po_is_temp_trade_sequence`.
  - `test_anything_before_incorporation_is_temp_before_incorporation`.
  - `test_payment_before_invoice_is_undetermined_not_a_violation`.
  - `test_rules_still_fire_as_before`: the `retro_po` and `tampered` packs give the same `TEMP_*` findings as at HEAD (compare check ids and documents).
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement** `gldb/ordering.py`; rewrite the two rules to call `violations`. Their messages stay byte-identical.
- [ ] **Step 4: Full suite passes.**
- [ ] **Step 5: Commit** `Ordering policy shared by the rules and the AI checks`

### Task 3: Pattern checks and evidence status

**Files:**
- Modify: `gldb/ai_team/checks.py`
- Modify: `gldb/config.py` (thresholds and lists from Global Constraints; own hunk only)
- Modify: `gldb/ai_team/team.py` (`_verified` sets `evidence_status`, `check_facts`, `check_reason`); remove `verify.strength`
- Test: `tests/test_ai_team_checks.py` (new), plus fixtures in `tests/fixtures/run5_leads.json` (the run-5 leads re-expressed with subjects)

**Interfaces:**
- Consumes: `bank_rows`, `PATTERN_SUBJECTS` (Task 1); `ordering.trade_dates`, `violations`, `undetermined` (Task 2); `screening.similarity`; `reference.registry()`; `checks.incorporation`.
- Produces:
  - `Outcome(status: EvidenceStatus, facts: dict[str, str], reason: str)`.
  - `check_pattern(lead: Lead, case: CaseData) -> Outcome`, which runs for every kept lead whatever its investigation status; `unverified` with the reason `"no code check for <pattern>"` when none exists.
  - `corroborate(lead: Lead, outcome: Outcome, case: CaseData) -> Outcome`, which turns only `verified` into `corroborated`.
  - `rule_confirmed(lead: Lead, case: CaseData) -> str | None`: the E id of the rule finding a verified `sequence_anomaly` coincides with.

- [ ] **Step 1: Failing tests:**
  - `third_party_payment`:
    - `test_tpp_l12_harbourline_is_verified`: facts `payer == "HARBOURLINE LOGISTICS"`, `buyer == "Bayview Marine Supplies Pte. Ltd."`.
    - `test_tpp_inv1001_orchid_lane_is_contradicted`.
    - `test_tpp_innocent_split_parts_are_contradicted`.
    - `test_tpp_debit_row_is_unverified`.
    - `test_tpp_row_without_the_invoice_number_is_unverified`.
    - `test_tpp_unknown_description_format_is_unverified`.
    - `test_tpp_generic_payer_is_unverified`.
    - `test_tpp_similarity_between_bands_is_unverified`.
  - `sequence_anomaly`:
    - `test_seq_l04_l08_invoice_after_shipment_is_contradicted`.
    - `test_seq_retro_po_is_verified_and_rule_confirmed`: `rule_confirmed` returns the `TEMP_TRADE_SEQUENCE` E id.
    - `test_seq_payment_before_invoice_only_is_unverified`.
  - `related_party`:
    - `test_rp_l13_director_guarantor_is_contradicted`.
    - `test_rp_related_party_pack_is_verified`: facts name the shared officer.
    - `test_rp_ambiguous_register_match_is_unverified`.
  - Others:
    - `test_unchecked_patterns_are_unverified` (L02, L14).
    - `test_a_refuted_lead_code_verifies_is_verified`.
    - `test_corroborated_only_from_verified`: unverified and contradicted never become corroborated; verified plus a non-info finding from another check family on the subject invoice becomes corroborated; two agents' leads on the same subject do not.
    - `test_checks_never_read_lead_prose`: two leads with the same subject and different `hypothesis`/`tests` give the same outcome.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement** the checks exactly as spec sections 4.1 to 4.3 and 5. The payer parse is a regex built from `PAYMENT_CHANNELS` and the escaped invoice number.
- [ ] **Step 4: Full suite passes.**
- [ ] **Step 5: Commit** `AI team: code checks for third-party payment, sequence and related party`

### Task 4: Gating of flags, memo and questions

**Files:**
- Modify: `gldb/ai_team/team.py` (memo task and `known_ids` from verified/corroborated leads only; question gating), `gldb/ai_team/prompts.py` (memo prompt: leads given are code-verified), `gldb/pipeline.py` (`team_ledger` counts by evidence status)
- Modify: `frontend/src/components/workspace/stageStatus.ts`, `components/stages/AiTeam.tsx`, `components/stages/ai-team/Leads.tsx`, `components/stages/InvestigationStage.tsx`, `styles.css`
- Test: `tests/test_ai_team.py`, `tests/test_gating.py` (new)

**Interfaces:**
- Consumes: `check_pattern`, `corroborate`, `rule_confirmed` (Task 3).
- Produces:
  - `team.flagged(lead) -> bool`: `evidence_status in {"verified", "corroborated"}` and not rule-confirmed.
  - Frontend helpers `aiLeads` (flagged), `observations` (unverified, investigation not refuted), `clearChecks` (contradicted, or investigation refuted), `ruleConfirmed` (shown on the finding as "AI confirmed existing rule finding <CHECK_ID> (<E id>)").

- [ ] **Step 1: Failing tests:**
  - `test_memo_task_holds_only_verified_and_corroborated_leads`.
  - `test_memo_point_citing_an_unverified_lead_is_dropped`.
  - `test_question_citing_only_unverified_evidence_is_withheld` (note `"<agent>: question withheld (no verified lead rests on it)"`).
  - `test_rule_confirmed_sequence_lead_is_not_a_flag`.
  - `test_false_positive_safety_invariant`, parametrised over every pack in `data/packs` and the run-5 fixtures with a scripted team that records them. For each non-verified/corroborated lead:
    - champion score and route are equal to the team-off run;
    - it is in no memo point;
    - no question cites it;
    - it is not in the shadow input (assert after Task 6; mark the shadow part `xfail(strict=True)` until then);
    - it causes no `would_route` raise (likewise).
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.** UI sections are, in order: "AI leads" (flags), "AI observations, not checked" (collapsed), "Checks that came back clear" (collapsed), "Withheld by the verifier". The rail count and stage 9 use flags only.
- [ ] **Step 4: Full suite and frontend build pass.**
- [ ] **Step 5: Commit** `AI team: only code-verified leads become flags, memo points and questions`

### Task 5: Rule score before the AI

**Files:**
- Modify: `gldb/pipeline.py` (`ASSESS_STAGES` order and `assess` body), `gldb/schemas.py` (`StageId` gains `shadow_score`), `frontend/src/types.ts`, `frontend/src/catalogue.ts` (stage 6 Score steps: `["score", "shadow_score"]`; `stageOf` maps `shadow_score` to `score`)
- Test: `tests/test_pipeline.py` (or the file that asserts `ASSESS_STAGES`), `tests/test_ai_team.py` (score invariance stays)

**Interfaces:**
- Produces: `ASSESS_STAGES = ("ocr", "intake", "prompt_screen", "extract", "agents", "score", "readiness", "route", "ai_team", "shadow_score", "projections")`.

- [ ] **Step 1: Failing test** `test_rule_score_is_fixed_before_any_ai_runs`: the stage callback order has `score`, `readiness`, `route` before `ai_team`; an `ai_team` that raises leaves the score and route equal to a team-off run.
- [ ] **Step 2: Run; it fails.**
- [ ] **Step 3: Implement.** `shadow_score` is recorded `off` until Task 6.
- [ ] **Step 4: Full suite and frontend build pass.**
- [ ] **Step 5: Commit** `Pipeline: rule score, readiness and route before the AI team`

### Task 6: Shadow scorer and log-only gate

**Files:**
- Create: `gldb/ai_team/shadow.py`
- Modify: `gldb/schemas.py` (`ShadowReason`, `ShadowScore`, `GateDecision`; `CaseResult.shadow`, `CaseResult.gate`), `gldb/pipeline.py` (`shadow_score` step; audit ledger entry), `gldb/api.py` (`CaseView.shadow`, `CaseView.gate`; capability row), `gldb/config.py` (own hunk), `gldb/ai_team/prompts.py` (`SHADOW_SCORER` prompt, versioned)
- Modify: `frontend/src/types.ts`, the score stage component (shadow card: "Shadow score (experiment, not used)", agree or disagree tag, gate's would-route)
- Test: `tests/test_shadow.py` (new), `tests/test_api.py`

**Interfaces:**
- Consumes: kept leads with `evidence_status` (Tasks 3 and 4); `tools._case_overview`; `llm_client.converse_turn`, `tool_spec(strict=...)`; `loop.run_agent` with `finish="submit_disposition"`.
- Produces:
  - `shadow_input(case: CaseData, leads: list[Lead]) -> dict`: only the case overview and flagged leads as `{lead_id, pattern, evidence_status, invoice_number, transaction_ids, counterparty, check_facts}`.
  - `run_shadow(case, leads, model_id, *, client=None) -> ShadowScore` (status `ran` or `failed`, never raises).
  - `gate(champion: Routing, leads: list[Lead], shadow: ShadowScore | None) -> GateDecision`.

- [ ] **Step 1: Failing tests:**
  - `test_shadow_input_excludes_memo_prose_and_unverified_leads`: the captured Bedrock request contains no `hypothesis`, `tests`, memo text, unverified or contradicted lead ids, or champion band/route.
  - `test_submit_disposition_enums_are_closed_per_case`: reason codes are the case's check ids, verified patterns and `no_issue`; evidence ids are its risky E ids and flagged L ids.
  - `test_invalid_disposition_is_stored_failed`: `no_issue` beside another reason, or a reason citing no id.
  - `test_shadow_never_changes_the_champion`: route and score equal with shadow on and off.
  - `test_gate_never_lowers_and_raises_only_on_verified_or_shadow`: the four rules of spec section 8, each a case.
  - `test_api_case_view_has_shadow_and_gate`.
  - Flip the Task 4 invariant's xfail parts to passing.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.** Strict tool use when `SHADOW_SCORER_MODEL in AGENT_STRICT_TOOL_MODELS`. A failure is stored as `ShadowScore(status="failed", error=...)` and the case completes.
- [ ] **Step 4: Full suite and frontend build pass.**
- [ ] **Step 5: Commit** `Shadow case scorer and log-only disposition gate`

### Task 7: Evaluation metrics and the spec's acceptance table

**Files:**
- Modify: `gldb/agent_eval.py` (`AgentPackResult` gains `verified_recall` inputs, `flag_noise`, `observations`, `contradicted`, `ai_questions`, `shadow_route`, `would_route`, `miss`; `AgentEvalRow` gains `verified_recall`, `flag_noise`, `miss_rate`, `auto_clear_rate`; `pattern_recall` renamed `supported_recall`, with `load_saved` tolerating the old name), `frontend` Controls agent-eval table columns
- Test: `tests/test_agent_eval.py`, `tests/test_acceptance_run5.py` (new: one test per row of spec section 11)

- [ ] **Step 1: Failing tests:** the metric tests (scripted team over `clean`, `third_party_payment` and `innocent_split`) and every row of the spec's acceptance table.
- [ ] **Step 2: Run; they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Full suite and frontend build pass.** Update both specs' status lines (Built / In progress) and the target-architecture labels for steps 5, 9, 11 and 12.
- [ ] **Step 5: Commit** `agent_eval: verified recall, flag noise, shadow miss and auto-clear rates`

### After the plan

One live run on `third_party_payment`, about US$0.30, only after the user approves. Pass:
- the planted payment is a verified lead;
- no applicant question comes from an unverified lead;
- a shadow route and a gate decision are recorded.
