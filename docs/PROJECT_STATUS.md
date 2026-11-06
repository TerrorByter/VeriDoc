# Project status: what is built and what is next

**Project:** Agentic Fraud Detection in Corporate Loan Documentation (sponsor: Green Link Digital Bank).
**Product name in the UI:** VeriDoc (a placeholder).
**Status:** working prototype, synthetic data only. It never approves or rejects a loan; a person decides.

## 1. What the system does

An analyst opens a trade-finance application pack: a bank statement, the FATCA/CRS entity and individual forms, a financial workbook (`.xlsx`), and the invoices, purchase orders, bills of lading and delivery orders behind the company's trade. The system reads the documents, fetches the company's official records, runs deterministic rule checks, scores the fraud risk, routes the case for review, and lets an AI team investigate what the rules cannot. Every finding is shown beside the document page it came from, and every action is written to an append-only audit log.

```
OCR -> Intake -> Prompt screen -> Extract -> Official records -> Rule checks
    -> Score -> Readiness -> Route -> AI team -> Shadow score -> Investigation -> Audit record
```

| Stage | What it does | Where |
|---|---|---|
| OCR | Textract reads every page of every PDF except the FATCA/CRS forms (optional) | `gldb/ocr.py` |
| Intake | Validates PDFs and workbooks, hashes them, reads metadata and text, classifies the document type | `gldb/intake.py`, `gldb/workbook.py` |
| Prompt screen | A Bedrock guardrail checks each page for instructions aimed at AI models (optional); a hit is a hard flag | `gldb/prompt_screen.py`, `infra/guardrail.yaml` |
| Extract | Structured fields: a deterministic parser (`stub`) or Bedrock (`llm`). The bank statement, forms and workbook are always read deterministically and never sent to a model | `gldb/extract*.py`, `gldb/form_maps.py` |
| Official records | Fetches the applicant's ACRA record and each guarantor's IRAS Notice of Assessment from the mock service (as a bank would from ACRA and Myinfo) | `gldb/official.py`, `mock_gov/` |
| Rule checks | 47 deterministic checks in six rule groups (section 2), each emitting Evidence | `gldb/checks.py` |
| Score | Rule-based score and band from fraud signals only; no AI sets the score | `gldb/scoring.py`, `gldb/signal_library.py` |
| Readiness | Compliance and completeness findings decide whether the application is complete | `gldb/scoring.py` |
| Route | Fast track, standard review or enhanced review (with enhanced due diligence), from the band, hard flags, extraction confidence and readiness; a QA sample of fast-track cases still gets a review | `gldb/routing.py` |
| AI team | A Supervisor and four specialists on Bedrock investigate with read-only tools and propose leads; code checks each lead against the case data (section 4) | `gldb/ai_team/` |
| Shadow score | A model proposes a route, logged beside the rule route and never used | `gldb/ai_team/shadow.py` |
| Investigation | Transaction chains (invoice, order, shipment, payment), a timeline, and a queue of what to resolve and what resolving it would change | `gldb/reconciliation.py`, `gldb/timeline.py`, `gldb/investigation.py` |
| Audit record | Append-only JSONL log of pipeline runs and every person's action | `gldb/audit.py` |

### Design principles (from `.kiro/steering/`)
1. Deterministic Python for anything computable. Models are used for extraction, for the AI team's investigation and for the shadow score; none of them sets the score or the route.
2. Every finding is an `Evidence` object; nothing exists outside Evidence.
3. The score is rule-based and only fraud signals feed it. Compliance and completeness findings drive a separate Application Readiness status.
4. AI proposes, code verifies, a person decides.
5. Uploaded document text is untrusted data, never instructions.
6. Synthetic data only. Personal identifiers are masked in Evidence, UI and logs.

## 2. The rule groups

| Rule group | Checks | What it catches |
|---|---|---|
| **Document Integrity** | 6 | Invoice arithmetic, bank running balance and continuity, financial-statement balance, PDF modified after creation, producer mismatch within one issuer's documents |
| **Forensics** | 5 | Hidden text (white, tiny or off-page), figures whose digits mix fonts, PDFs re-saved after creation, visible figures that differ from the stored text (OCR comparison), instructions aimed at AI models (the guardrail) |
| **Cross-Document** | 12 | Identity (UEN, names) across documents; each invoice against its bank payment, purchase order and bill of lading or delivery order; the workbook against the bank statement and ACRA (cash, debt, revenue); money that only passes through the account; ACRA's entity, signatories and people against the forms |
| **Registry & Screening** | 6 | ACRA and IRAS records fetched or missing, uploaded copies that differ from the originals, companies struck off or not registered, signatories who are not officers, sanctions (UN lists named by MAS plus the bank's own names), FATF high-risk countries |
| **Timeline** | 2 | Trading before incorporation; orders, shipments and invoices out of sequence |
| **Compliance & Completeness** | 16 | Missing documents, bank statement coverage, FATCA/CRS entity form validity and consistency with the application |

Each check is a named, versioned signal in `gldb/signal_library.py`. The trade checks know which way a trade runs: an invoice the applicant issues is a sale, paid by a credit; a supplier's invoice addressed to the applicant is a purchase, paid by a debit.

Forensics reads the raw file through `ExtractedPack.paths`, which is excluded from serialisation so local paths never reach results or the API. The FATCA/CRS forms are skipped by forensics because the official templates legitimately use white headings and mixed fonts.

## 3. Documents handled

Bank statement (the fillable statement of the team's sample; its figures are form fields), FATCA/CRS entity and individual self-certifications (AcroForms, read field by field and never sent to a model), the submitted financial workbook (`.xlsx`, read-only), invoices (the styled tax invoice of `gldb/invoice_pdf.py` and the older plain layout), purchase orders, bills of lading and delivery orders. The ACRA profile and the Notice of Assessment are fetched, not uploaded; an uploaded copy is compared with the original. The application data (`application.json`) is the reference for consistency checks.

## 4. The AI team

A Supervisor (Claude Sonnet 4.6) plans and briefs four specialists (Claude Haiku 4.5): forensics, transaction, KYC and timeline. They call read-only tools (search the bank statement, trace an invoice's chain, look a company up in ACRA) and record leads: a claim that names its subject by id and cites the tool results it rests on. Code then checks each lead against the case data and marks it verified, corroborated, unverified or contradicted; an agent cannot set that status. The Supervisor writes a memo of observations and questions for the applicant from the leads that survive.

Nothing the team produces changes the score or the route. The shadow scorer (Claude Haiku 4.5) proposes a route from the case and the leads; it is compared with the rule route and a log-only gate records what it would have changed.

Live runs (October 2026, on the test packs): the planted third-party payment in `third_party_payment` was found and verified by code, and on `innocent_split` no false flag was raised. A case costs roughly US$0.20-0.30 of Bedrock. The team has not yet been run live on the demo packs.

## 5. AWS integration

- **Bedrock (Converse API):** one wrapper, `gldb/llm_client.py`. Extraction forces the model to call a tool with the target JSON schema at temperature 0; every quote the model returns must appear verbatim in the visible text or it is dropped; hidden text never reaches a model. Usage logs record token counts only, never text.
- **Textract:** `DetectDocumentText`, one page per call, 5 workers by default (the Singapore quota is 10 per second).
- **Guardrails:** the prompt screen's guardrail, defined in `infra/guardrail.yaml`.
- **Configuration:** `.env` (gitignored). Credentials come from `aws login --profile <name>`, never from the repo. See the README section "Connecting AWS".
- **Where data is processed:** Textract and the guardrail run in ap-southeast-1. The Claude models are called through `global.` cross-Region profiles, so a request may be processed outside Singapore: acceptable for synthetic data, not for real applicants. The only Claude model Bedrock runs in Singapore itself, Claude Sonnet 5, is not yet enabled for the project's account (AWS Sales gates it); neither are the other Claude 5-generation models.

## 6. Synthetic data and evaluation

`data/packs/` holds twelve demo cases in the format of the team's sample, built by `python -m gldb.demo_packs`. Each has a `ground_truth.json`, and each carries an invoice, purchase order and shipping document for every sale and goods purchase on its statement (`gldb/demo_trade.py`): sales are zero-rated exports to Malaysia shipped under a bill of lading, purchases are from Singapore suppliers with 9% GST and a delivery order. Each company has its own customers, suppliers and goods (`gldb/demo_partners.py`). The mock ACRA and IRAS service holds the demo companies and their suppliers.

| Pack | Company | What is planted | Expected |
|---|---|---|---|
| `clean` | ACMA | Nothing; its new CEO, who signs, is not yet on ACRA | Medium, standard review, complete |
| `incomplete` | ACMA | Three months of statements; a guarantor IRAS has no notice for | Medium, standard review, incomplete |
| `identity_mismatch` | Duskfield | A signatory who is no officer, another office, a guarantor's changed name | High, enhanced review, incomplete |
| `inflated_financials` | Brightwave | Submitted financials double what was filed with ACRA | High, enhanced review |
| `invoice_mismatch` | Oakridge | Two overbilled invoices, goods billed but not shipped, an order never issued | High, enhanced review |
| `sanctioned` | Eastgate | A shareholder on the sanctions list | Medium, enhanced review |
| `struck_off` | Fernleaf | The company is struck off at ACRA | Medium, standard review |
| `tampered_statement` | Corvid | A raised sales credit and a re-saved file | High, enhanced review |
| `third_party_payment` | Kestrel | A sale paid by a company with no part in it (AI team only) | Low, fast track |
| `related_party` | Lumen | Its biggest supplier is run by its own CEO, per ACRA (AI team only) | Low, fast track |
| `round_trip` | Northstar | A payment to a supplier comes back as a customer receipt (AI team only) | Low, fast track |
| `split_payment` | Kestrel | An invoice paid in two parts by its buyer: a rule flags it, code explains it | Medium, standard review |

The three packs marked AI team only are clean to the rules, which would fast-track them; each plants a pattern only the AI team looks for, and a code check confirms it from the documents and ACRA (`tests/test_demo_packs.py`).

`python -m gldb.evaluate` reports, per pack, band, readiness and route against the ground truth, each planted fault and whether it was found, precision, recall and false positives. With the deterministic parser it scores 1.00 on every measure and finds all 14 planted faults. `python -m gldb.agent_eval` scores the AI team the same way (it calls Bedrock), as many runs as asked. On the server, the GitHub workflow Run evaluation runs both and saves them for the Model & controls page, which shows counts with 95% intervals (14 of 14 is consistent with a true rate as low as 78%) and which planted frauds reach review with the rules alone and with the AI team. The packs are synthetic and were built knowing the checks: these figures show the pipeline catches what was planted, not a detection rate on real applications.

`tests/fixtures/packs/` holds the older text-layout test packs (`data_factory.py`) used by the test suite and the live AI-team runs.

A **blind held-out test** (`gldb/heldout.py`) renders packs from an independent
author's scenario sheet, seals the answer key, runs the system without the key,
and scores per pack after the seal is verified. It is the detection-rate claim
the demo packs cannot make; the pre-registration and author guide are in
`docs/`. A pilot sheet (`tests/fixtures/heldout/pilot.json`) exercises the
renderer and scoring; its numbers are development data.

## 7. API and frontend

`uvicorn mock_gov.app:app --port 8001`, `uvicorn gldb.api:app --reload`, and `cd frontend && npm install && npm run dev` (UI on http://localhost:5173).

Screens: **Cases**; **New case** (a demo pack or an upload, each document in its own slot); a **live run** showing each stage as it runs; the **case workspace**, twelve stage pages (OCR, Intake, Prompt screen, Extract, Official records, Rule checks, Agents, Score, Readiness, Route, Investigation, Audit record) beside a document viewer; and **Model & controls** (evaluation, capabilities, signal library, spend). The viewer highlights a finding on its page, and its forensic overlay boxes every finding on a page at once, coloured by severity.

The analyst confirms or dismisses each flag (a dismissal needs a reason code), can request information, escalate, approve or decline. An enhanced-review case cannot be approved until the MLRO, a different person from the analyst who escalated, has reviewed it. The API returns masked Evidence only, never the raw extracted pack.

## 8. Testing

`pytest` runs the offline suite (about 1,300 tests, no AWS calls). Live AWS tests are opt-in: `RUN_LIVE=1 pytest tests/test_live_aws.py -v` (about US$0.10-0.30).

## 9. Known limitations

- Thresholds (amount tolerance, statement recency, score bands, name-match similarity) are our assumptions: the sponsor says none are fixed in practice. They live in `gldb/config.py` and are labelled as such.
- Detection is validated only on synthetic packs; mixed-font detection in particular may false-alarm on real bank PDFs, and name screening flags about 1.25% of common two-word names.
- The Claude calls may be processed outside Singapore (section 5).
- The AI team is live-tested on two test packs only.
- Purchase orders and bills of lading are still the plain text layout; a styled invoice is read from its text layer, not yet from a scan of it.
- Images, photos and screenshots of documents (which the sponsor says arrive in practice) are not handled; there is no image forensics.
- No authentication: people are identified by the name they enter. Storage is local files (uploads, cases, audit log), so it is not multi-user or tamper-proof. `data/cases/<id>/result.json` keeps the full extracted pack, unmasked, on local disk (not exposed by the API).
- The frontend has no unit tests; it is type-checked, built and checked in a browser by hand.

## 10. What is next

Highest value first.

1. **Model access.** Ask AWS (a Service Quotas case for `L-B46CFAB8`) to enable Claude Sonnet 5 in ap-southeast-1, which gives in-Singapore processing; the code then needs a small change, since newer models reject the temperature and forced tool choice it sends.
2. **Live AI-team runs on the demo packs**, `invoice_mismatch` first, to check the team on purchases as well as sales.
3. **Explaining the score:** each check's severity, points and whether it forces enhanced review, shown in one register.
4. **Images and scans:** photographed and screenshotted documents, styled invoices from scans (or Textract `AnalyzeExpense`), and image forensics (error-level analysis, copy-move, signatures) tuned on a hand-made tamper set.
5. **Document polish:** purchase orders and bills of lading in the styled layout of the invoices.
6. **Risk report** in the shape the sponsor described: key findings, risks with evidence, follow-up actions.
7. **Hosting, storage and login (only if the sponsor wants a live demo):** one container on App Runner or ECS with an IAM role, S3 for uploads, DynamoDB for cases, S3 Object Lock for the audit log, Cognito for login.
8. **Technical report** (a deliverable): architecture, the AI team's design, detection logic, evaluation results, and each rule group's contribution.

### Tasks for teammates (no coding needed)
- The sponsor's answers on review practice, red flags and thresholds were received on 7 October; follow up on the application record format and our assumed thresholds.
- Make a hand-tampered set: edit clean PDFs in Word, Canva or Preview (amounts, names, pasted signatures, edited scans) and log each change in a spreadsheet.
- Build a fraud-type catalogue from MAS notices, ACFE reports and news cases: fraud type, document, what is altered, how a person spots it.
- Draft report sections from this document and the README; write the demo script; test the app and log bugs and misleading UI text.

## 11. Repository map

See the README's "Project structure" for every module.

```
gldb/                Backend package (stages in section 1, rule groups in section 2)
  ai_team/           The AI team: tools, agent loop, prompts, lead checks, shadow scorer
mock_gov/            Mock ACRA and IRAS service
frontend/            React + TypeScript (Vite) UI
infra/               The prompt screen's guardrail (CloudFormation)
data/packs/          Demo packs and their ground truth
data/mock_gov/       The mock service's records
data/reference/      Sanctions designations, the bank's own names, FATF high-risk countries
data/templates/      The team's sample documents and the official blank forms (read-only)
tests/               Offline pytest suite; tests/fixtures/packs holds the test packs
.kiro/steering/      Product, structure and tech rules the code follows
docs/                This document and the design notes in docs/superpowers/ (historical)
```
