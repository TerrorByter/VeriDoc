# Evidence Verification and Shadow Scoring — Design

Date: 2026-10-05 · Status: approved with changes (sections 4.6, 5, 11); implemented 2026-10-05, live run pending
Builds on: `2026-10-04-target-architecture.md` (steps 5, 9, 10, 11, 12) and
`2026-10-04-ai-agent-team-design.md`.

> LLM-supported is not evidence-verified.

## 1. Problem

Live run 5 (`CASE-20261004T163428Z-92b7`, pack `third_party_payment`) kept
the planted third-party payment (L12), and also five leads the model marked
"supported" that are normal business: invoice after shipment (L04, L08),
buyer UEN absent from invoices (L02, L14), a director-guarantor who is not a
signatory (L13). All six had the same strength, `unverified`, and the
Supervisor turned the five into applicant questions. Agents now propose
leads well; the bottleneck is deciding in code which proposals are a real
banking-risk pattern. The prompt already said invoice-after-shipment is
normal; the model produced the lead anyway, so the rule goes into code, not
into more prompt text.

## 2. Scope

In this milestone:
1. Two separate lead fields: `investigation_status` (the agent's) and
   `evidence_status` (code's).
2. Typed lead subjects, so checks read case data, never model prose.
3. Code checks for `third_party_payment`, `sequence_anomaly`, `related_party`.
4. Gating: only verified or corroborated leads become flags, memo
   observations or applicant questions.
5. Shadow case scorer (log only) and a log-only disposition gate.
6. Rule score, readiness and route moved before the AI team.
7. Evaluation metrics and run-5 regression fixtures.

Not in this milestone: checks for the other four patterns (they stay
`unverified`, section 4.6), the investigation prioritizer, screening of
metadata and fields (F), the cross-case store (D), applying any AI output to
the live route.

## 3. Lead lifecycle and fields

```
AI proposes lead (record_lead)
      │
      ▼
investigation_status: supported | refuted | unresolved     (the agent's result)
      │
      ▼
structural + provenance verifier (built; at record time and at the end)
      │  fail ──► refused / withheld, reason kept
      ▼
pattern check (code, reads case data through the lead's subject)
      │
      ├── preconditions met, pattern holds ────────────► evidence_status: verified
      │        └── plus an independent deterministic source ► corroborated
      ├── preconditions met, pattern does not hold ────► contradicted
      └── no check for this pattern, or a precondition
          cannot be established ───────────────────────► unverified
```

Fields (rename of today's `status` and `strength`):

| Field | Set by | Values |
|---|---|---|
| `investigation_status` | the specialist | `supported`, `refuted`, `unresolved` |
| `evidence_status` | code only (`gldb/ai_team/checks.py`, new) | `verified`, `corroborated`, `unverified`, `contradicted` |
| `check_facts` | code only | the facts the check read, e.g. `{"buyer": "...", "payer": "...", "transaction": "T14"}` |
| `check_reason` | code only | one line, e.g. "payer HARBOURLINE LOGISTICS differs from buyer Bayview Marine Supplies Pte. Ltd." |

- The pattern check runs on every kept lead whose pattern has a check,
  whatever its `investigation_status`. A lead the agent called `refuted` that
  code verifies is a caught AI miss and becomes a flag; a `supported` lead
  code contradicts is a caught AI false flag and becomes a clear check.
- Gating reads `evidence_status` only. `investigation_status` is shown, and
  never decides anything.
- Stored leads from before the rename load through the existing shim
  (`status` -> `investigation_status`; old strength -> `unverified`).

### Typed subjects

`record_lead` gains closed-world subject fields (enums from the case, as
today's arguments):

| Field | Values | Required for |
|---|---|---|
| `invoice_number` | the case's invoice numbers | `third_party_payment`, `sequence_anomaly` |
| `transaction_ids` | the case's bank rows, `T01`... | `third_party_payment` |
| `counterparty` | counterparty names from `get_parties` | `related_party` |

- Bank rows get a stable id, `T01`..., numbered over statements in pack
  order and rows in statement order; `search_bank_transactions` returns it
  on each row.
- A lead whose pattern has a check but lacks its required subject fields is
  refused at record time ("third_party_payment needs invoice_number and
  transaction_ids").
- Provenance: each subject id must appear in one of the lead's cited
  results (the agent saw it); otherwise refused.

## 4. Pattern checks

Each check is a pure function `(lead subject, case data) -> (evidence_status,
facts, reason)`. It reads the extracted pack (`ExtractedPack`), the register
and the findings, never the lead's prose or the tool result text. Thresholds
live in config.

### 4.1 third_party_payment

An invoice is paid by someone other than its buyer.

Preconditions (any not met: `unverified`, the reason names which):
1. The subject invoice exists in the pack.
2. Each subject transaction exists in the pack.
3. Each is a credit (money in): `credit` set and positive. A debit cannot be
   a buyer paying.
4. Each references this invoice with certainty: its description contains the
   invoice number as a whole token. An amount or date match alone is not
   enough.
5. The invoice names a buyer (`buyer_name` not empty).
6. The payer can be read reliably: the description matches
   `<CHANNEL> CR <PAYER> <INVOICE_NUMBER>[ <suffix>]` with CHANNEL in
   `PAYMENT_CHANNELS` (config: GIRO, FAST, TT, IBG, MEPS), PAYER at least two
   letters, and PAYER not a generic phrase in `GENERIC_PAYERS` (config:
   CUSTOMER RECEIPTS, CASH DEPOSIT, TRANSFER, ...).

Outcome, per transaction, by `similarity(payer, buyer)` (`gldb.screening`,
legal suffixes and punctuation removed):
- at or above `PAYER_SAME_THRESHOLD` (0.90): this row was paid by the buyer.
- at or below `PAYER_DIFFERENT_THRESHOLD` (0.60): a different payer.
- between: `unverified` (cannot tell).

`verified` if every precondition holds and at least one subject transaction
has a different payer; `contradicted` if every subject transaction was paid
by the buyer.

Run-5 check: L12 (INV-1002, row "GIRO CR HARBOURLINE LOGISTICS INV-1002",
credit 18,530.00) is verified, with payer HARBOURLINE LOGISTICS and buyer
Bayview Marine Supplies Pte. Ltd. The INV-1001 row ("GIRO CR ORCHID LANE
RETAIL INV-1001") is contradicted. On `innocent_split` ("... BAYVIEW MARINE
SUPPLIES INV-1002 PART 1") it is contradicted.

### 4.2 sequence_anomaly: one ordering policy

The ordering rules become one table in code (`gldb/ordering.py`, new) that
the deterministic rules (`TEMP_TRADE_SEQUENCE`, `TEMP_BEFORE_INCORPORATION`)
and this check both read, so the rule and the AI check cannot disagree:

| Ordering in one trade | Policy |
|---|---|
| PO, then shipment, then invoice | valid |
| PO, then invoice, then shipment | valid |
| invoice or shipment on the PO date | valid |
| invoice dated before its PO | anomaly (`TEMP_TRADE_SEQUENCE`) |
| shipment dated before its PO | anomaly (`TEMP_TRADE_SEQUENCE`) |
| any trade document dated before incorporation | anomaly (`TEMP_BEFORE_INCORPORATION`) |
| payment before invoice | no policy: advance payment is normal on some terms and the pack has no terms, so `unverified` |
| payment after invoice | valid |

Check: given `invoice_number`, code takes the trade's dates (invoice, its
matching PO via `matching_po`, its bill of lading, its payment) from the
pack and applies the table:
- an anomaly: `verified` (it will also be a rule finding, see section 5);
- only valid orderings: `contradicted`;
- a needed date missing, or only the "no policy" row applies: `unverified`.

The model's own "expected" chronology is never read.

Run-5 check: L04 and L08 (invoice after shipment) are contradicted. They are
valid orderings, so they are not flags and not questions.

### 4.3 related_party

A counterparty shares an officer with the applicant.

Preconditions (any not met: `unverified`):
1. The subject counterparty resolves to one register entry: the best name
   match is at or above `SCREENING_MATCH_THRESHOLD` (0.90) and is the only
   one at that level.
2. The applicant's people are known: register officers of the applicant's
   UEN, plus guarantors and signatories from the application.

Outcome: `verified` if a counterparty officer matches an applicant person
(name similarity at or above 0.90; the matched pair goes in `check_facts`),
otherwise `contradicted`.

Run-5 check: L13 (Daniel Brooks: guarantor and director, not a signatory)
names no counterparty officer in common, so it is contradicted. The
`related_party` pack (the buyer's director is the applicant's director) is
verified.

### 4.4 What a check never does

- It never reads `hypothesis`, `tests`, `missing_evidence` or tool result text.
- It never calls a model.
- It never changes a finding, the score or the route.

### 4.5 The principle for every check

If code cannot establish a condition, the lead is `unverified`, never
"probably true". Every future pattern check keeps this: explicit
preconditions, each unmet one giving `unverified`, and `verified` only when
all of them hold on case data.

### 4.6 Patterns without a check

`round_trip`, `split_payment`, `business_mismatch` and
`document_inconsistency` stay `unverified` in this milestone. Planned next,
with the same section shape:
- `split_payment`: two or more credits referencing one invoice, whose sum
  is within `TOLERANCE_SGD` of its total.
- `round_trip`: a debit and a later credit naming the same counterparty
  within N days and X percent.

Run-5 check: L02 (`document_inconsistency`) and L14 (`business_mismatch`)
stay unverified, so they become analyst observations only.

## 5. Verified and corroborated

- **verified**: the pattern's code check holds on case data.
- **corroborated**: verified **and** at least one independent deterministic
  source points at the same subject from a different data source than the
  check used. Examples: a non-info rule finding on the subject invoice from
  another check family (registry, cross-document, forensics); a cross-case
  hit (planned, D).
- Never corroboration: two agents agreeing, the same agent twice, or a
  triage lead agreeing with the finding it judges. Two models repeating a
  mistake is one source.
- Invariant: corroborated is strictly downstream of verified.
  `unverified` never becomes `corroborated`; `contradicted` never becomes
  `corroborated`; only `verified` may.
- A verified `sequence_anomaly` always coincides with its rule finding (one
  policy, section 4.2). It is shown merged with that finding, worded "AI
  confirmed existing rule finding TEMP_TRADE_SEQUENCE (E07)", never as an
  AI-generated flag, and adds no flag of its own. The risk was determined by
  the rule; the AI only confirmed it. Where a rule exists, the AI cannot
  out-vote it in either direction.
- Triage leads: the finding already counts in the champion score. A triage
  lead adds no evidence status of its own (`unverified`) and is an analyst
  observation. Planned: a verified explanation, such as a verified
  `split_payment` on the invoice an `XDOC_INVOICE_BANK` finding is about,
  attached to that finding as an ordinary explanation for the analyst.
  Never a risk reduction in this milestone.

## 6. Gating

| evidence_status | Flag (rail count, stage 9 "AI leads to review") | Memo observation | Applicant / RFI question | Shadow scorer input | Shown where |
|---|---|---|---|---|---|
| verified, corroborated | yes | yes | yes | yes | AI leads |
| unverified (investigation supported or unresolved) | no | no | no | no | "AI observations, not checked" (stage 5, collapsed) |
| contradicted, or investigation refuted | no | no | no | no | "Checks that came back clear" (stage 5, collapsed) |
| withheld / refused | no | no | no | no | trace and withheld list |

- Refuted and contradicted leads never disappear: they stay in the
  investigation record as evidence that something was checked and found
  normal, out of the analyst's main list.
- The Supervisor's memo task receives verified and corroborated leads only.
  Memo points are verified against E ids plus those L ids only, so a point
  citing an unverified lead is dropped.
- A specialist's drafted question names the lead it follows from
  (`draft_question.lead_id`, one the agent recorded) and reaches the
  Supervisor only if that lead is a flag, the question cites results its agent
  received, and every number and identifier in its text is one the lead's
  code-built facts hold (`check_facts`, `check_reason`, subject ids). Otherwise
  it is withheld with a note. This replaced "every R id it cites is cited by a
  flag" (final review B1): one search result holds many invoices' rows, so
  result ids let the subject of an unverified or contradicted lead ride in.
- The Supervisor's memo task holds the flags' code-built fields only (subject,
  `check_reason`, `check_facts`): never an agent's hypothesis, tests or a tool
  result. A memo point is grounded in the evidence it cites alone: the facts
  of the flags and the records of the findings it names.

## 7. Shadow case scorer

A deliberately boring experiment against the champion.

- **When:** after the AI team, as its own step `shadow_score`, when
  `SHADOW_SCORE_ENABLED`.
- **Input (code-built JSON, data not instructions):**
  - the case context: today's `get_case_overview` data (documents,
    parties, risky findings with check ids and severities, chain breaks,
    date anomalies);
  - verified and corroborated leads as structured records: `lead_id`,
    `pattern`, `evidence_status`, subject ids, `check_facts`.
- **Never in the input:** the memo, `hypothesis`, `tests`, specialist prose,
  unverified or contradicted leads, or the champion's score, band or route
  (the comparison is done afterwards, so the scorer cannot anchor on it).
- **Output** via one strict tool, `submit_disposition`:
  - `route`: `fast_track` | `standard_review` | `enhanced_review`;
  - `reasons[]`, each `{code, evidence_ids}`. `code` is an enum: the
    case's check ids, the verified patterns, and `no_issue`.
    `evidence_ids` are an enum of the case's risky E ids and the
    verified/corroborated L ids.
- **Code validation:**
  - every reason cites at least one id, unless its code is `no_issue`;
  - `no_issue` cannot appear beside another reason;
  - an invalid disposition is stored as `failed` with the reason. No retry
    in this milestone.
- **Model:** `SHADOW_SCORER_MODEL`, default Haiku 4.5 (strict tool use).
  Estimated US$0.005 to 0.01 per case.
- **Stored** as `CaseResult.shadow: ShadowScore | None`: `status`, `route`,
  `reasons`, `model`, `prompt_version`, `agrees_with_champion`.
  `score()` and `route()` never read it; a test proves the champion route
  is identical with the scorer on and off.
- **UI:** the score stage shows "Shadow score (experiment, not used)" beside
  the champion, with an agree or disagree tag.

## 8. Disposition gate (log only)

Code computes the route the gate would take, stores it as
`CaseResult.gate: GateDecision` (`would_route`, `reasons`), and applies
nothing:

1. Champion route above `fast_track`: keep it. AI never lowers a route.
2. Champion `fast_track` and any verified or corroborated lead: would raise
   to `standard_review`.
3. Champion `fast_track` and the shadow scorer proposes review: would raise
   to `standard_review`.
4. Otherwise: `fast_track`.

Applying the gate (rules 2 and 3 only) is a separate, explicit decision
after the shadow evaluation shows a zero miss rate on every planted-fraud
pack and an analyst has reviewed the disagreements. Lowering a route is out
of scope until a verified-explanation design exists and has its own
validation (SR 11-7 style: challenger, evidence, sign-off).

Honest scope: this milestone stops AI output from adding unverified analyst
workload. Taking workload *below* the champion's (clearing rule flags) needs
verified explanations and governance, and is a later phase.

## 9. Pipeline order

`ASSESS_STAGES` becomes:
`ocr, intake, prompt_screen, extract, agents, score, readiness, route, ai_team, shadow_score, projections`

The rule score is fixed before any AI runs. `get_score` still calls the same
pure functions. The rail groups are unchanged; the score-invariance test
stays.

## 10. Evaluation (agent_eval)

Per pack, beside today's metrics:
- `verified_recall`: planted patterns found as verified or corroborated
  leads. Today's `pattern_recall` is kept as `supported_recall`.
- `flag_noise`: verified or corroborated leads matching nothing planted.
  Target 0.
- `observations`: unverified leads (analyst-visible, not flags).
- `contradicted`: AI "supported" leads code contradicted, i.e. caught false
  flags. A measure of model quality, tracked over prompt versions.
- `ai_questions`: applicant-facing questions in the memo. On `clean`, target 0.
- Shadow:
  - `shadow_route`, and agreement with the champion and with ground truth.
  - `miss`: a pack with planted fraud (expected route above `fast_track`,
    or expected leads) that the shadow scorer or the gate would send to
    `fast_track`. Target 0.
  - `auto_clear`: packs the gate would send to `fast_track`.

Synthetic packs prove the pipeline, not detection value. Calibration needs
the sponsor bank's past outcomes (open question 1).

## 11. Acceptance criteria (regression fixtures)

Fixtures are built from run 5, re-expressed with typed subjects, over the
`third_party_payment` pack, plus the existing packs. Tests are offline: the
checks are pure functions, and scripted fakes stand in for Bedrock.

| Fixture | Must be |
|---|---|
| L12: third_party_payment, INV-1002, the Harbourline row | `verified`; a flag; may produce the Harbourline applicant question |
| third_party_payment, INV-1001, the Orchid Lane row | `contradicted` |
| `innocent_split`: third_party_payment, INV-1002, both PART rows | `contradicted` |
| L04, L08: sequence_anomaly, invoice after shipment | `contradicted`; no flag, no question |
| `retro_po`: sequence_anomaly on the retrospective PO's invoice | `verified`, merged with its `TEMP_TRADE_SEQUENCE` finding, no extra flag |
| L13: related_party, applicant director-guarantor | `contradicted`; no flag, no question |
| `related_party` pack: the shared director | `verified` |
| L02 (document_inconsistency), L14 (business_mismatch) | `unverified`; analyst observation only; no memo point, no question |
| A memo point citing L02 or L14 | dropped |
| A drafted question citing only R ids of unverified leads | withheld |
| `clean` pack, any leads | 0 flags, 0 applicant questions |
| a refuted third_party_payment lead that code verifies | `verified` (a caught AI miss) |
| champion route with the scorer and gate on vs off | identical |
| scorer input | contains no memo, hypothesis, tests or unverified lead (asserted on the captured request) |

**False-positive safety invariant** (one named, parametrised test over every
fixture and pack): a lead whose `evidence_status` is not `verified` or
`corroborated` may not
- affect the champion score or route,
- create an applicant question,
- enter the memo,
- enter the shadow scorer's input,
- cause a disposition escalation (`would_route`).

The milestone passes when every row holds and a live run on
`third_party_payment` (about US$0.30, approved separately) shows: L12-like
lead verified; no applicant question from an unverified lead; shadow route
recorded.

## 12. Risks and open questions

Risks:
- **Payer parsing depends on the description format.** Other banks' formats
  give `unverified`, never a false `verified`, by design: a payer that holds
  a digit, or any word of a generic payer ("CASH DEPOSIT ATM", "REVERSAL OF"),
  is not read. Coverage grows by adding formats, each with a fixture. The
  check also reads every other credit naming the invoice, so the agent's
  choice of rows cannot make a third-party payment read as clear.
- **Name similarity thresholds are untested on real names.** The band
  between 0.60 and 0.90 is `unverified` on purpose.
- **Scorer inputs carry applicant-controlled names** (parties, payers).
  They are labelled as data, and screening them (F) is the next milestone.
- **More fields per lead** means more tokens and more record-time refusals
  at first. Watch cost in the live run.

Open questions for the sponsor bank:
1. Do you keep past outcomes (approved, declined, losses, SARs) we could
   calibrate the shadow scorer against?
2. What share of today's analyst flags turn out to be false?
3. Which payment description formats do your statements use?
