# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences, equally weighted (confirmed):

- **Bank analyst and MLRO** at a Singapore bank. They work through trade-finance loan applications that rules or AI have flagged, decide what each flag means, ask the applicant questions, and escalate. The MLRO cannot be the analyst who escalated (enforced by the API).
- **Assessment panel** watching a live demo of the same screens over a few minutes: the daily-use screens must hold up, and one walkthrough must also read clearly.

## Product Purpose

VeriDoc reviews trade-finance application packs (invoices, purchase orders, bills of lading, bank statements, company forms). It reads the documents, runs deterministic rule checks, scores risk, and routes each case for review. An AI investigation team (a Supervisor and four specialists on Amazon Bedrock) investigates what the rules cannot, and code verifies what the agents claim. The aim is fewer false flags needing human intervention: a claim an AI supports is not the same as one the evidence verifies.

## Positioning

"AI investigates. Specialists stay in their lanes. Code verifies. Rules score. Humans decide." Evidence status (verified, corroborated, unverified, contradicted) is set only by code from case data, never by an agent. AI output changes no score and no route today. A shadow scorer proposes a route and a log-only gate records what it would do.

## Operating Context

- Cases are opened from an uploaded pack or a demo pack, then run through ten stages: OCR, Intake, Prompt screen, Extract, Agents, Score, Readiness, Route, Investigation, Audit record.
- The analyst reads a finding beside the document page it came from, and confirms or dismisses it with a reason.
- A Model and controls screen compares model setups and shows cost per case.
- Banking and system terms appear as written: STRO, MLRO, UEN, and rule check ids such as XDOC_INVOICE_BANK.

## Capabilities and Constraints

- Frontend: React, TypeScript and Vite in `frontend/`; backend: FastAPI in `gldb/`. Screens are addressed by URL hash (`#/cases/<id>/<stage>`).
- Must hold in the UI (confirmed): AI leads, the shadow score and the gate are always shown as proposals, never as decisions; every flag and memo point sits beside its evidence (document page and code-built facts); banking terms and check ids stay verbatim; the UI meets WCAG AA.
- Known gaps to design for: a rule finding that code has explained (for example an invoice paid in two parts) still shows as an open fraud signal; a `record_lead` call the verifier refused, which the agent then corrected, shows in the same red "Error" style as a real failure.

## Brand Commitments

The name "VeriDoc" and its current mark are a placeholder (confirmed): keep them for now, but nothing in the design should depend on them.

Binding visual constraint volunteered by the user: the interface is used in light mode and must not be blinding (low glare, no stark white), with a professional look in the vein of Hermes / Nous Research (their named reference). Not expanded here; the visual world is decided in new-work.

## Evidence on Hand

- Eleven synthetic demo packs in `data/packs/` and several live runs of the AI team (cases `LIVE-RUN-7` and `LIVE-RUN-9`, among others, with their documents under `data/uploads/`).
- No real bank data, customers, testimonials or benchmarks exist. Future work must not invent any. The header already says "Prototype · synthetic data".

## Product Principles

1. AI proposes and a person decides: nothing the AI produces may look final.
2. Evidence before assertion: a claim is shown with the documents and facts that support or contradict it.
3. Show what code established apart from what an agent said.
4. Keep the analyst's attention on what changes a decision; explained or refuted items stay available but quiet.
5. Preserve audit clarity: every status and cost shown can be traced to a record.

## Accessibility & Inclusion

WCAG AA (confirmed): keyboard operation, visible focus, and sufficient contrast. Status must never rely on colour alone.
