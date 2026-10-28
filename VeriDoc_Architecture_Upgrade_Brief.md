# VeriDoc --- Architecture Upgrade Brief for Coding Agent

## Objective

Upgrade the current **Agentic Fraud Detection in Corporate Loan
Documentation** prototype into a convincing, enterprise-style banking
fraud investigation platform.

The system should not merely be:

> Documents → LLM agents → fraud score

The target mental model is:

> **Documents → Evidence → Relationships / Contradictions → Risk
> Assessment → Analyst Decision**

Core principle:

> **AI investigates. Rules quantify. Humans decide.**

The system must remain explainable, deterministic where correctness
matters, and fully traceable from every risk signal back to source
evidence.

------------------------------------------------------------------------

# 1. Current Architecture

The current architecture is approximately:

``` text
Upload / RFI docs
        ↓
OCR stage (Textract / SG)
        ↓
Document Agent
(classify + extract + cited quotes)
        ↓
┌─────────────────────────────────────────────────────┐
│ Document Integrity                                  │
│ Forensics                                           │
│ Cross-Document                                     │
│ Compliance                                          │
│ Registry & Screening                                │
│ Semantic Review Agent (later)                       │
└──────────────────────┬──────────────────────────────┘
                       ↓
                Risk Engine
         scoring.py / rules
                       ↓
                Decision Layer
                       ↓
             Investigation Agent
                       ↓
              Analyst Workbench
                       ↓
                 Audit Log
```

Existing design principles that should be preserved:

1.  AI reads and explains; humans make decisions.
2.  Measurement is separated from policy.
3.  Append-only audit log is the source of truth.
4.  Three lines of defence: analyst → MLRO → system.
5.  Reference data is versioned.
6.  The model should not directly determine the lending outcome.

------------------------------------------------------------------------

# 2. Target Architecture

Refactor the architecture around an **Evidence Plane** and
**Investigation Plane**.

``` text
                         DOCUMENTS
                             │
                             ▼
                  ┌─────────────────────┐
                  │ DOCUMENT INTELLIGENCE│
                  │ OCR / Extraction     │
                  │ Classification       │
                  │ Forensics             │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │   EVIDENCE GRAPH    │
                  │                     │
                  │ Claims              │
                  │ Entities            │
                  │ Relationships       │
                  │ Source locations    │
                  │ Confidence          │
                  └──────────┬──────────┘
                             │
            ┌────────────────┼─────────────────┐
            ▼                ▼                 ▼
       Reconciliation   Cross-document    Registry /
       & Finance        Consistency       Compliance
            │                │                 │
            └────────────────┼─────────────────┘
                             ▼
                  ┌─────────────────────┐
                  │   FRAUD SIGNALS     │
                  │                     │
                  │ FIN-*               │
                  │ DOC-*               │
                  │ REG-*               │
                  │ ENT-*               │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ DETERMINISTIC       │
                  │ RISK ENGINE         │
                  │                     │
                  │ Policy version      │
                  │ Rule version        │
                  │ Signal aggregation  │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ SEMANTIC INVESTIGATOR│
                  │                     │
                  │ Synthesises all     │
                  │ identified evidence │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ ANALYST WORKBENCH   │
                  │                     │
                  │ Why flagged?        │
                  │ Evidence            │
                  │ Timeline             │
                  │ Missing information │
                  │ Investigation queue │
                  │ Human decision      │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │    EVIDENCE LEDGER  │
                  │ Append-only         │
                  │ Versioned           │
                  │ Replayable          │
                  └─────────────────────┘
```

The deterministic **Case Orchestrator** should operate horizontally
across the workflow:

``` text
┌─────────────────────────────────────────────────────┐
│ DETERMINISTIC CASE ORCHESTRATOR                     │
│ routing • dependencies • retries • versioning       │
└─────────────────────────────────────────────────────┘
```

Avoid making the orchestrator look like another AI agent.

------------------------------------------------------------------------

# 3. Highest-Priority Upgrade: Evidence Graph

The Evidence Graph should become the backbone of the system.

Instead of agents simply returning findings, they should produce
structured evidence that can be connected.

Example:

``` text
Company ABC
    │
    ├── Director → John Tan
    ├── Bank Account → 8821
    ├── Invoice → INV-1042
    └── Supplier → ABC Trading
```

Evidence relationships can include:

-   supports
-   contradicts
-   matches
-   derived_from
-   belongs_to
-   associated_with
-   precedes
-   follows
-   shared_with

Example contradiction:

``` text
Financial Statement
Revenue = $12.4M
        │
        │ contradicts
        ▼
Bank Evidence
Supported revenue = $7.2M
```

The graph should allow the UI to answer:

> Why did the system flag this?

and immediately navigate to the underlying document/page/quote.

------------------------------------------------------------------------

# 4. Claim → Evidence Data Model

Important extracted claims should be structured.

Example:

``` json
{
  "claim": "Company revenue for FY2025 was $12.4M",
  "value": 12400000,
  "document_id": "financial_statement_01",
  "page": 8,
  "bbox": [120, 340, 520, 380],
  "source_text": "...",
  "extraction_confidence": 0.97,
  "evidence_type": "financial_statement",
  "policy_version": "2026.09"
}
```

A second document may produce:

``` json
{
  "claim": "Company revenue for FY2025 was $7.2M",
  "document_id": "bank_statement_03",
  "page": 12
}
```

The system should represent the contradiction explicitly rather than
allowing an LLM to make an unsupported conclusion.

Every important risk finding should be traceable to one or more evidence
objects.

------------------------------------------------------------------------

# 5. Separate Four Different Concepts

Do NOT collapse these into one generic "confidence" score.

### Extraction confidence

How confident are we that the document was read/extracted correctly?

### Evidence strength

How reliable and relevant is the evidence?

### Fraud signal

How strongly does the evidence indicate a suspicious condition?

### Case risk

What is the aggregate policy/rule-based risk assessment?

Example:

``` text
OCR confidence:          98%
Extracted amount:        $850,000

Evidence strength:       HIGH

Fraud signal:            MEDIUM

Case risk contribution:  +18
```

Avoid statements like:

> "The LLM is 92% confident this is fraud."

Prefer:

> "The system identified four high-confidence evidence conflicts
> contributing 31 points to the case risk score."

------------------------------------------------------------------------

# 6. Separate Document-Level Fraud from Case-Level Fraud

Implement two conceptual layers.

## Document-level risk

Determine whether an individual document has suspicious characteristics:

-   altered text
-   inconsistent fonts
-   image manipulation
-   metadata anomalies
-   OCR inconsistencies
-   suspicious PDF structure
-   altered numbers
-   suspicious signatures/images

## Case-level risk

Determine whether the collection of documents forms a coherent story:

-   invoice vs bank statement mismatch
-   financial statement vs transaction evidence mismatch
-   director vs registry mismatch
-   invoice date before company registration
-   bill of lading vs invoice mismatch
-   totals do not reconcile
-   same bank account across supposedly unrelated entities
-   conflicting document versions

Key product message:

> **A document can look authentic in isolation while the loan
> application is suspicious when viewed as a whole.**

------------------------------------------------------------------------

# 7. Add a Reconciliation Agent / Module

Create a dedicated reconciliation capability.

Its job is not primarily to "find fraud". Its job is:

> **Determine whether the applicant's information mathematically and
> logically reconciles.**

Examples:

## Financial reconciliation

``` text
Declared Revenue
        ↓
Invoice Revenue
        ↓
Bank-Supported Revenue
```

Example result:

``` text
Declared Revenue:       $12.4M
Invoice Revenue:        $11.8M
Bank-Supported Revenue: $7.2M

Reconciliation Gap:     $5.2M
```

## Transaction chain

``` text
Invoice
   ↓
Purchase Order
   ↓
Bill of Lading
   ↓
Payment
   ↓
Bank Statement
```

Example:

``` text
Invoice → PO              ✓
Invoice → B/L             ✓
B/L → Shipment             ✓
Invoice → Payment          ✗
```

The system should show exactly where the chain breaks.

This is more valuable than adding another generic LLM agent.

------------------------------------------------------------------------

# 8. Entity Resolution

Implement entity resolution for:

-   companies
-   people
-   bank accounts
-   addresses
-   invoices
-   suppliers
-   customers
-   directors
-   transactions
-   shipment references

Example:

``` text
ABC Trading Pte Ltd
ABC Trading
ABC TRADING PTE. LTD.
        ↓
Same Entity
```

This enables relationship detection.

Example:

``` text
Company A
  ├── Director → John
  └── Bank → 1234

Company B
  ├── Director → Mary
  └── Bank → 1234
```

Potential signal:

> Shared bank account associated with supposedly unrelated entities.

Do not automatically label this as fraud. Produce an evidence-backed
signal requiring analyst review.

------------------------------------------------------------------------

# 9. Fraud Signal Library

Move risk logic into structured, versioned signals rather than hardcoded
scattered conditions.

Example:

``` yaml
signal_id: FIN-003
name: Revenue Reconciliation Gap
category: financial_consistency

trigger:
  bank_supported_revenue < declared_revenue * 0.75

severity: high

score: 20

required_evidence:
  - financial_statement
  - bank_statement

explanation:
  "Declared revenue materially exceeds revenue supported by
   available banking evidence."
```

Potential signal categories:

-   `FIN-*` --- financial inconsistencies
-   `DOC-*` --- document integrity / forensic anomalies
-   `REG-*` --- registry / identity discrepancies
-   `TXN-*` --- transaction inconsistencies
-   `ENT-*` --- entity / relationship anomalies
-   `TEMP-*` --- temporal inconsistencies
-   `COMP-*` --- compliance / screening signals

Risk scores should be decomposable:

``` text
CASE RISK: 67

+20 Revenue reconciliation gap       FIN-003
+15 Invoice/payment mismatch         TXN-002
+12 Registry mismatch                REG-001
+10 PDF structural anomaly           DOC-004
+10 Related-party pattern            ENT-002
-------------------------------------------
 67
```

Every score contribution must be traceable to evidence.

------------------------------------------------------------------------

# 10. "Why This Score?" and "What Would Change It?"

The analyst workbench should not only display:

``` text
Risk Score: 72
```

Instead display:

``` text
CASE RISK: 72

Why?
─────────────────────────────
+20 Revenue reconciliation gap
+15 Invoice/payment mismatch
+12 Registry mismatch
+10 Document manipulation signal
+15 Related-party connection
```

Also provide:

``` text
What would change the assessment?

✓ audited financial statements
✓ supporting bank records
✓ original invoice
✓ explanation for director discrepancy
```

This makes the product an investigation assistant rather than an
automated rejection system.

------------------------------------------------------------------------

# 11. Investigation Timeline

Build a chronological case timeline.

Example:

``` text
15 Jan 2025
Company incorporated
        ↓
02 Mar 2025
Invoice #1042 issued
        ↓
04 Mar 2025
Payment recorded
        ↓
07 Mar 2025
Bill of lading created
        ↓
18 Mar 2025
Loan application submitted
```

Flag temporal anomalies:

``` text
⚠ Invoice issued before company registration
⚠ Bill of lading date inconsistent with invoice
```

This helps analysts see relationships that are difficult to identify by
reviewing documents independently.

------------------------------------------------------------------------

# 12. Semantic Review Agent

Keep the Semantic Review Agent as a later-stage capability.

It should NOT duplicate extraction.

Its job should be:

> **Given all structured evidence and identified conflicts, determine
> whether the overall applicant narrative is internally coherent.**

Example:

> "The applicant claims that the \$850k invoice represents a completed
> shipment. However, the bill of lading indicates a different shipment
> date and the corresponding bank transaction cannot be located."

This is where LLM reasoning adds value: **synthesis of evidence**, not
arbitrary scoring.

------------------------------------------------------------------------

# 13. Analyst Workbench

The final UI should feel like a real banking investigation platform.

Avoid:

``` text
Risk Score: 83
Fraud detected.
```

Prefer:

``` text
CASE GLDB-2026-00124

Risk Assessment
━━━━━━━━━━━━━━━━━━━━
67 / 100
HIGH

12 evidence items
4 unresolved conflicts
2 document anomalies
1 registry discrepancy
```

Then show evidence:

``` text
🔴 HIGH
Revenue mismatch

Financial Statement
Declared revenue: $12.4M

Bank Statements
Supported revenue: $7.2M

Difference: $5.2M

[View evidence]
```

Clicking should show the relevant document, page, quote, and ideally
bounding box/highlight.

------------------------------------------------------------------------

# 14. Investigation Queue

Generate recommended analyst investigation actions from unresolved
evidence.

Example:

``` text
Recommended Investigation

1. Verify FY2025 revenue
2. Request original invoice #1042
3. Verify director identity
4. Investigate shared bank account
```

The system should assist investigation rather than dictate the final
lending decision.

------------------------------------------------------------------------

# 15. Evidence Ledger

Upgrade the current append-only audit log concept into an **Evidence
Ledger**.

Track events such as:

``` text
Document uploaded
      ↓
Document hash generated
      ↓
OCR completed
      ↓
Claim extracted
      ↓
Evidence created
      ↓
Rule triggered
      ↓
Risk score changed
      ↓
Analyst reviewed
      ↓
Analyst override
      ↓
Final decision
```

Each event should contain information such as:

``` json
{
  "timestamp": "...",
  "case_id": "...",
  "actor": "document_agent",
  "action": "claim_created",
  "evidence_id": "...",
  "policy_version": "...",
  "model_version": "..."
}
```

The ledger should support **case replay**.

------------------------------------------------------------------------

# 16. Case Replay

Every case should ideally record:

-   case ID
-   document hashes
-   model version
-   prompt version
-   policy version
-   rule version
-   reference-data version
-   agent outputs
-   risk calculations
-   analyst actions
-   overrides
-   final outcome

A future "Replay Case" capability should reconstruct the analysis as it
occurred.

This supports auditability and reproducibility.

------------------------------------------------------------------------

# 17. Adversarial Fraud Simulation / Evaluation Harness

Create a controlled evaluation dataset.

Start with authentic/mock cases, then introduce known attacks.

Examples:

``` text
Attack 1 — Change invoice amount
Attack 2 — Change director name
Attack 3 — Change bank account
Attack 4 — Modify PDF text
Attack 5 — Replace signature
Attack 6 — Change invoice date
Attack 7 — Create cross-document inconsistency
```

Pipeline:

``` text
Original Case
      ↓
Controlled Tampering Engine
      ↓
Fraudulent / inconsistent Case
      ↓
VeriDoc
      ↓
Detection + Evidence
```

Build an evaluation table:

  Attack                      Detected?   Evidence                   Signal
  --------------------------- ----------- -------------------------- ---------
  Invoice amount alteration   ✓           Financial reconciliation   FIN-\*
  Director alteration         ✓           Registry mismatch          REG-\*
  PDF modification            ✓           Document forensics         DOC-\*
  Bank account substitution   ✓           Transaction mismatch       TXN-\*
  Date manipulation           ✓           Temporal analysis          TEMP-\*

Only claim results that are actually measured.

------------------------------------------------------------------------

# 18. Evaluation Metrics

Do not rely only on "AI accuracy".

Measure several dimensions.

## Extraction

-   field extraction accuracy
-   OCR accuracy
-   citation/source accuracy

## Fraud detection

-   precision
-   recall
-   F1
-   false-positive rate

## Cross-document reasoning

-   contradiction detection accuracy
-   reconciliation accuracy
-   entity resolution accuracy

## Explainability

A useful metric:

``` text
Evidence Coverage =
risk findings with verifiable source evidence
───────────────────────────────────────────
total risk findings
```

Target high/ideally complete traceability for the demo dataset.

## Operations

-   average processing time
-   analyst review time
-   number of manual checks eliminated

## Governance

-   reproducibility
-   policy-version consistency
-   audit completeness

------------------------------------------------------------------------

# 19. Keep AI and Deterministic Logic Separate

Do NOT turn every component into an LLM agent.

Recommended design:

  Component                                           AI? Reason
  -------------------------- ---------------------------- --------------------------
  OCR                                No / managed service Deterministic extraction
  Document classification                             Yes Unstructured documents
  Information extraction                              Yes Complex layouts
  PDF forensics                                   No / ML Structural evidence
  Financial reconciliation                             No Exact arithmetic
  Cross-document matching                          Hybrid Entity resolution
  Registry screening           Controlled / deterministic Reference data
  Semantic investigation                              Yes Evidence synthesis
  Risk engine                                      **No** Deterministic policy
  Final decision                                **Human** Governance

The architecture should communicate:

> **Use AI where reasoning is useful, and deterministic systems where
> correctness matters.**

------------------------------------------------------------------------

# 20. Do Not Optimize for Agent Count

Do not add agents merely to make the system sound more "multi-agent".

The differentiator should be:

> **How specialized components cooperate through structured evidence.**

Not:

> **How many agents exist.**

Prefer fewer, clearly scoped components with strong interfaces.

------------------------------------------------------------------------

# 21. Recommended Implementation Priority

If implementation time is limited, prioritize in this order.

## P0 --- Highest priority

### 1. Evidence Graph / Claim-Evidence model

Make this the central data model.

### 2. Financial + document reconciliation

Demonstrate that fraud detection is a cross-document consistency
problem.

### 3. Explainable risk decomposition

Every score contribution must link to evidence.

### 4. Adversarial evaluation harness

Generate controlled tampering and inconsistency cases.

### 5. Analyst Workbench

Build an interface that feels like a real investigation tool.

------------------------------------------------------------------------

## P1 --- High value

6.  Entity resolution\
7.  Investigation timeline\
8.  Fraud Signal Library\
9.  "Why this score?"\
10. "What would change it?"\
11. Investigation queue\
12. Evidence Ledger improvements

------------------------------------------------------------------------

## P2 --- Later / stretch goals

13. Semantic Review Agent\
14. Case Replay\
15. More advanced graph visualisation\
16. Image-level forensic analysis\
17. More sophisticated related-party detection

------------------------------------------------------------------------

# 22. Suggested Product Narrative

The pitch should NOT be:

> "We built a multi-agent AI fraud detector."

Use a stronger narrative:

> **"We don't ask an LLM whether a loan application is fraudulent."**

Then explain:

``` text
1. We extract claims and evidence from every submitted document.

2. We connect entities and relationships across documents.

3. We identify contradictions, reconciliation gaps,
   temporal anomalies, registry discrepancies and
   document tampering signals.

4. Deterministic, versioned rules quantify those signals.

5. An AI investigator synthesises the evidence into a
   grounded investigation narrative.

6. The analyst sees exactly why the case was flagged,
   what evidence supports each finding, and what
   additional information is required.

7. The human makes the final decision.
```

Core message:

> **AI investigates. Rules quantify. Humans decide.**

------------------------------------------------------------------------

# 23. Demo Flow

The ideal final demo should tell a story.

## Step 1 --- Upload

Upload a realistic loan application containing:

-   financial statements
-   bank statements
-   invoices
-   contracts
-   identity documents
-   bill of lading / trade records
-   registry data

## Step 2 --- Automated analysis

Show the system processing the case.

## Step 3 --- Evidence graph

Show entities, claims and relationships appearing.

## Step 4 --- Detection

Surface several deliberately planted inconsistencies:

``` text
Revenue mismatch
Invoice/payment mismatch
Registry mismatch
PDF manipulation
Temporal anomaly
Shared entity/account relationship
```

## Step 5 --- Risk decomposition

Show exactly how the signals contribute to the score.

## Step 6 --- Evidence drill-down

Click a finding and show:

-   document
-   page
-   quote
-   bounding box/highlight
-   extraction confidence
-   evidence relationship

## Step 7 --- Investigation queue

Show recommended next steps.

## Step 8 --- Human decision

Analyst chooses:

-   approve
-   decline
-   request information
-   escalate to MLRO

The system should not make this decision autonomously.

## Step 9 --- Audit trail

Show the Evidence Ledger containing the complete case history.

------------------------------------------------------------------------

# 24. Coding Guidelines for the Agent

When modifying the codebase:

### Preserve existing working functionality

Do not rewrite working modules unnecessarily.

### Prefer incremental refactors

Introduce new interfaces/models first, then migrate existing agents.

### Keep business logic deterministic

Risk scoring, reconciliation, thresholds and policy rules should be
implemented in deterministic code/configuration.

### Keep LLM output structured

Use schemas for:

-   claims
-   evidence
-   findings
-   entities
-   relationships
-   investigation narratives

### Require source references

Every LLM-generated finding that affects the case should include source
evidence.

### Version everything relevant

At minimum:

-   model version
-   prompt version
-   policy version
-   rule version
-   reference-data version

### Avoid hallucinated evidence

The system must never create a finding without a source
document/evidence reference.

### Maintain backward compatibility

Existing tests should continue to pass unless intentionally changed.

### Add tests for new functionality

Especially:

-   evidence relationships
-   reconciliation
-   scoring
-   signal triggering
-   policy versioning
-   case replay
-   citation/source tracking

------------------------------------------------------------------------

# 25. What "Done" Looks Like

A strong implementation should allow this complete flow:

``` text
Loan documents
      ↓
OCR + classification
      ↓
Structured claims
      ↓
Evidence objects
      ↓
Entities + relationships
      ↓
Cross-document reconciliation
      ↓
Fraud signals
      ↓
Deterministic risk score
      ↓
Evidence-grounded investigation narrative
      ↓
Analyst workbench
      ↓
Human decision
      ↓
Append-only evidence ledger
```

For every risk score contribution, the system should be able to answer:

> **What triggered it?**

> **Which document supports it?**

> **Where in the document?**

> **Which rule generated it?**

> **Which policy version was used?**

> **Which model/prompt produced the underlying extraction?**

> **What can the analyst do to resolve it?**

That traceability is the central design goal.

------------------------------------------------------------------------

# 26. Guiding Principle

The project should feel less like:

> **"A chatbot with several agents."**

and more like:

> **"An evidence-driven investigation platform for corporate lending."**

The strongest differentiator is not the number of agents.

It is the combination of:

**structured evidence + cross-document reasoning + deterministic risk
policy + explainability + human-in-the-loop governance + auditability.**
