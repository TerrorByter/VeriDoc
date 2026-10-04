# Target Architecture

Date: 2026-10-04 · Status: locked (build towards it; no new boxes before the
contracts below are solid)

> AI investigates. Specialists stay in their lanes. Code verifies.
> Rules score. Humans decide.

Status labels: **Built** (in the code and tested), **In progress** (this
milestone), **Planned** (designed, not built). The diagram must never imply a
component works when it is only planned.

## 1. Diagram

```
 Application pack (PDFs)
        │
 1  INTAKE + OCR ................................ Built
        │   Textract pages / fields / PDF metadata; SHA-256 per document
 2  SCREENING .................................... Built (page text) / Planned (metadata, fields, names)
        │   Bedrock Guardrail on all text that can reach a model;
        │   a hit is a finding and the content is marked untrusted
 3  DETERMINISTIC CHECKS ......................... Built (one module) / Planned (split per family)
        │   forensics / chain / transactions / registry / timeline:
        │   pure functions, case -> [Finding]
 4  CROSS-CASE INTELLIGENCE ...................... Planned
        │   fingerprint store + entity index, this bank's cases only:
        │   "BL number seen in another case" -> Finding
        │
    FINDINGS ──────────────────────────────► 5  RULE SCORE (champion) .... Built
        │                                        score(findings) -> band + route
 6  CASE CONTEXT BUILDER ...... Planned          fixed before any AI runs;
        │   one structured view: claims,         AI cannot change it
        │   entities, timeline, findings,                │
        │   contradictions                               │
 7  INVESTIGATION PRIORITIZER . Planned                  │
        │   sets DEPTH, never whether AI runs            │
        │   (clean -> light sweep, flagged -> deep)      │
 8  AI TEAM, HUB AND SPOKE .... Built                    │
        │        Supervisor (Sonnet)                     │
        │   ┌─────────┬────┴────┬─────────┐              │
        │ Forensics Transaction KYC  Timeline (Haiku)    │
        │   └─────────┴────┬────┴─────────┘              │
        │   each reports only to the Supervisor          │
 9  VERIFIER (code) ........... Built                    │
        │   provenance, numbers, identifiers, lead       │
        │   contract, pattern checks (3 of 7 patterns)   │
 10 SYNTHESIS ................. Built (memo, questions) │
        │   verified leads, memo, missing evidence,      │
        │   RFI questions                                │
 11 SHADOW SCORE .............. Built (log only)         │
        │   reads 6 + verified leads, never the memo     │
 12 DISPOSITION GATE (code) ... Built (log only) ◄────────┘
        │   rule score + verified lead strength + shadow verdict
 13 HUMAN REVIEW .............. Built
        │   approve / decline / request information / MLRO
 14 FEEDBACK .................. Built (agent_eval) / Planned (outcome calibration)
```

Build note: the pipeline steps `score`, `readiness` and `route` run before
`ai_team`, so the rule score is fixed before any AI runs (Built, tested). It
is also a pure function of findings, and a test proves score and route are
identical with the team on and off.

## 2. Invariants

**Rule score**
- Computed from deterministic findings only.
- Computed before AI execution (step order Planned; independence Built and tested).
- AI cannot modify it: no tool writes it, and leads are a separate record type `score()` never reads.

**Specialists**
- Exactly four: Forensics, Transaction, KYC, Timeline.
- Each has its own permitted tools (`TOOLSETS`), with closed-world arguments.
- Specialists never call one another; each reports only to the Supervisor,
  and code is the switchboard.

**Verifier**
- Code, not another LLM. No generic "logical validity" model.
- Validates provenance, numbers, identifiers, the lead contract (section 3)
  and, when built, pattern-specific evidence rules.
- Produces verified leads; withheld leads stay on the record with the reason.

**Shadow score**
- Never influences the champion during the shadow phase.
- Reads the case context (6) and verified leads only; never the memo.
- May later raise a case to review; it never lowers a case the rules flagged.
  Auto-clear needs rule score Low, no supported verified lead, and a shadow
  "clear", and is promoted only on measured auto-clear and miss rates.

**No free-form claim influences downstream logic.** Downstream code reads
enums, ids and code-assigned fields only; prose is for people.

## 3. InvestigationLead contract (Built)

```
Lead
├── lead_id            L01..., assigned on record
├── agent              the specialist (code, not the model)
├── kind               triage (judges risky findings) | pattern (one of its own patterns)
├── finding_ids[]      triage: the risky findings it judges
├── pattern            pattern: one of the specialist's patterns
├── hypothesis         what was tested, e.g. "INV-1002 was paid by its buyer"
├── tests[]            each: cites[R...], expected, observed
├── status             supported | refuted | unresolved     (proposed by the model)
├── missing_evidence[] required when unresolved
├── strength           set by code, kept supported leads only
├── outcome            kept | withheld (+ withheld_reason)  (set by code)
└── cites              derived: every R id its tests cite
```

- **Tests come from recorded calls.** Every test cites results of the
  agent's own successful evidence calls in the Ledger; the tool and arguments
  shown for a test are the Ledger's record, never the model's account. A lead
  with no test is withheld.
- **Status.** For triage: supported (the finding stands), refuted (an ordinary
  explanation), unresolved. For a pattern: supported (found), refuted (checked,
  clear), unresolved. A refuted lead is a record of a check done: shown as
  "checked, clear", never as a flag. This is where "sanctions clear" belongs.
- **Narrow logic checks in code:** a supported lead needs a test whose
  observed differs from its expected; an unresolved lead needs missing
  evidence; a triage lead needs a risky finding; a pattern lead names its
  pattern, and it must be the specialist's own.
- **Checked when recorded, and again at the end.** `record_lead` runs the
  verifier and returns its reason as a tool error, so the agent corrects the
  lead in the same run (validate and retry); the refusal stays in the trace.
- **Strength (code):** `corroborated` for a supported triage lead (a rule
  finding plus AI evidence that it stands); `unverified` for a supported
  pattern lead until its pattern's evidence check exists, which then adds
  `verified`. Refuted and unresolved leads have no strength. The gate reads
  strength, never the model's wording.
- **Numbers and identifiers.** Identifiers (INV-1001, BL-3002, 202205551D,
  invoice_INV-1001) are not quantities: each must appear in the lead's cited
  results or the case index. Every other number must appear in the cited
  results (or the findings a triage lead judges).

## 4. Order of work

1. This milestone: target architecture, lead contract, identifier-aware
   verifier. Unit tests, no AWS calls; then one approved live run.
2. Screening of metadata, fields and names (F).
3. Scoring spec: case context builder, pattern evidence checks (`verified`
   strength), shadow scorer, disposition gate, score step moved first.
4. Cross-case store and entity index (D); split of `checks.py` (C).
