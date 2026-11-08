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
| Rule checks | 48 deterministic checks in six rule groups (section 2), each emitting Evidence | `gldb/checks.py` |
| Score | Rule-based score and band from fraud signals only; no AI sets the score | `gldb/scoring.py`, `gldb/signal_library.py` |
| Readiness | Compliance and completeness findings decide whether the application is complete | `gldb/scoring.py` |
| Route | Fast track, standard review or enhanced review (with enhanced due diligence), from the band, hard flags, extraction confidence and readiness; a QA sample of fast-track cases still gets a review | `gldb/routing.py` |
| AI team | A Supervisor and four specialists on Bedrock investigate with read-only tools and propose leads; code checks each lead against the case data (section 4) | `gldb/ai_team/` |
| Shadow score | A model proposes a route, logged beside the rule route and never used | `gldb/ai_team/shadow.py` |
| Investigation | Transaction chains (invoice, order, shipment, payment), a timeline, and a queue of what to resolve and what resolving it would change | `gldb/reconciliation.py`, `gldb/timeline.py`, `gldb/investigation.py` |
| Audit record | Append-only JSONL log of pipeline runs and every person's action | `gldb/audit.py` |

### Design principles
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
| **Cross-Document** | 13 | Identity (UEN, names) across documents; each invoice against its bank payment, purchase order and bill of lading or delivery order; who paid each sale against its buyer (an undisclosed third-party payer); the workbook against the bank statement and ACRA (cash, debt, revenue); money that only passes through the account; ACRA's entity, signatories and people against the forms |
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

Live runs: in October 2026 the team found the planted third-party payment in `third_party_payment` (code verified it) and raised no false flag on `innocent_split`; both predate the rule changes below. In November it ran on the demo packs on the server (Run evaluation, 3 November, before the split-payment and disclosure changes) and on 25 packs of the blind test, where it found 4 of 5 round trips (section 6). A case costs roughly US$0.20-0.30 of Bedrock.

The third-party payment was the team's least reliable catch: finding it means checking every sale's invoice against the credit that paid it, and specialists working to a step limit sampled a few invoices instead (run 6 searched INV-1001 and never INV-1002). Code reads the payer from the statement exactly, so it is now a rule, `XDOC_THIRD_PARTY_PAYER` (November 2026). A sale paid by someone other than its buyer is a high finding that sends the case for review; a payer the application discloses (a payment agent, a group company) is recorded as info. When the AI team raises the same payment, its lead names the rule's finding and confirms it: it is not a second flag, and the agent eval does not count it as noise. The same reasoning moved the split payment into the rules: `XDOC_INVOICE_BANK` used to flag an invoice its buyer paid in instalments (no single credit matched it), and code in the AI step explained the flag without changing the route. The rule now adds the parts up itself: two or more credits naming the invoice, each from its buyer, within the match window and together making the total, are recorded as info, and the case is fast-tracked. Anything less (a third party, a payer it cannot read, a sum short or over, a part out of time) is still a high finding.

The general principle: a pattern code can establish exactly belongs in the rules, which score, route and are audited; the AI team works where the evidence is spread across documents and needs judgement. Related party and round trip could in principle become rules too; they are kept rule-less on purpose, as the patterns nobody has yet written a rule for, which the AI team must find and code then verifies.

### Scenarios that test the AI team

| Scenario | Packs | What the rules do | What the AI team should do |
|---|---|---|---|
| Undisclosed related party | `related_party` (demo: a supplier; test: a buyer), held-out `related_party_undisclosed` | Nothing: no rule compares the applicant's officers with a counterparty's | The KYC specialist looks the counterparty up in ACRA and finds the applicant's own officer; code confirms it from ACRA |
| Round trip | `round_trip` (demo and test), held-out `round_trip` | Nothing: each row looks normal alone | The transaction specialist links a payment out with nearly the same amount coming back from the same party; code confirms the pair |
| Invoice paid in parts | `split_payment` (demo), `innocent_split` (test), held-out quirk `split_payment` | `XDOC_INVOICE_BANK` adds the parts up and records it as info: fast track | Raise no flag |
| Disclosed directorship | held-out quirk `disclosed_directorship` | Nothing | Raise no flag. A related-party lead raised anyway is contradicted by code, which reads the application's disclosures |
| Third-party payment | `third_party_payment` (demo and test), held-out `third_party_payment` | `XDOC_THIRD_PARTY_PAYER` flags it and sends the case for review | Nothing more is needed; a lead on it confirms the rule's finding |
| Payment agent, intra-group payer | held-out quirks `agent_payment`, `intra_group_payer` | The payer is disclosed: info, no score | No flag |

No pack yet plants a `business_mismatch` (a trade at odds with the company's registered activity), the KYC specialist's other pattern.

## 5. AWS integration

- **Bedrock (Converse API):** one wrapper, `gldb/llm_client.py`. Extraction forces the model to call a tool with the target JSON schema at temperature 0; every quote the model returns must appear verbatim in the visible text or it is dropped; hidden text never reaches a model. Usage logs record token counts only, never text.
- **Textract:** `DetectDocumentText`, one page per call, 5 workers by default (the Singapore quota is 10 per second).
- **Guardrails:** the prompt screen's guardrail, defined in `infra/guardrail.yaml`.
- **Hosting:** one EC2 server in ap-southeast-1 runs the backend, the mock ACRA and IRAS service and the built UI, reached at https://veridoc.tail4bd12e.ts.net through Tailscale Funnel and a Caddy proxy that asks for one shared sign-in. GitHub Actions deploys `main` after the tests pass, and can start the server and run the evaluation, through an OIDC role limited to two fixed Systems Manager commands (`infra/github-deploy.yaml`). The server stops at 1am Singapore time and after two idle hours (`infra/server-schedule.yaml`).
- **Configuration:** `.env` (gitignored). Credentials come from `aws login --profile <name>`, never from the repo. See the README section "Connecting AWS".
- **Where data is processed:** Textract and the guardrail run in ap-southeast-1. The Claude models are called through `global.` cross-Region profiles, so a request may be processed outside Singapore: acceptable for synthetic data, not for real applicants. The only Claude model Bedrock runs in Singapore itself, Claude Sonnet 5, is not yet enabled for the project's account (AWS Sales gates it); neither are the other Claude 5-generation models.

## 6. Synthetic data and evaluation

`data/packs/` holds twelve demo cases in the format of the team's sample, built by `python -m gldb.demo_packs`. Each has a `ground_truth.json`, and each carries an invoice, purchase order and shipping document for every sale and goods purchase on its statement (`gldb/demo_trade.py`): sales are zero-rated exports to Malaysia shipped under a bill of lading, purchases are from Singapore suppliers with 9% GST and a delivery order. Each company has its own customers, suppliers and goods (`gldb/demo_partners.py`). The mock ACRA and IRAS service holds the demo companies and their suppliers.

| Pack | Company | What is planted | Expected |
|---|---|---|---|
| `clean` | ACMA | Nothing | Low, fast track, complete |
| `incomplete` | ACMA | Three months of statements; a guarantor IRAS has no notice for | Low, standard review, incomplete |
| `identity_mismatch` | Duskfield | A signatory who is no officer, another office, a guarantor's changed name | High, enhanced review, incomplete |
| `inflated_financials` | Brightwave | Submitted financials double what was filed with ACRA | High, enhanced review |
| `invoice_mismatch` | Oakridge | Two overbilled invoices, goods billed but not shipped, an order never issued | High, enhanced review |
| `sanctioned` | Eastgate | A shareholder on the sanctions list | Medium, enhanced review |
| `struck_off` | Fernleaf | The company is struck off at ACRA | Medium, standard review |
| `tampered_statement` | Corvid | A raised sales credit and a re-saved file | High, enhanced review |
| `third_party_payment` | Kestrel | A sale paid by a company with no part in it and none the application discloses | Medium, standard review |
| `related_party` | Lumen | Its biggest supplier is run by its own CEO, per ACRA (AI team only) | Low, fast track |
| `round_trip` | Northstar | A payment to a supplier comes back as a customer receipt (AI team only) | Low, fast track |
| `split_payment` | Kestrel | Nothing wrong: an invoice paid in two parts by its buyer, which the rule records as info | Low, fast track |

The two packs marked AI team only are clean to the rules, which would fast-track them; each plants a pattern only the AI team looks for, and a code check confirms it from the documents and ACRA (`tests/test_demo_packs.py`).

`python -m gldb.evaluate` reports, per pack, band, readiness and route against the ground truth, each planted fault and whether it was found, precision, recall and false positives. With the deterministic parser it scores 1.00 on every measure and finds all 15 planted faults. `python -m gldb.agent_eval` scores the AI team the same way (it calls Bedrock), as many runs as asked. On the server, the GitHub workflow Run evaluation runs both and saves them for the Model & controls page, which shows counts with 95% intervals (15 of 15 is consistent with a true rate as low as 80%) and which planted frauds reach review with the rules alone and with the AI team. The packs are synthetic and were built knowing the checks: these figures show the pipeline catches what was planted, not a detection rate on real applications.

`tests/fixtures/packs/` holds the older text-layout test packs (`data_factory.py`) used by the test suite and the live AI-team runs.

A **blind held-out test** (`gldb/heldout.py`) renders packs from an independent
author's scenario sheet, seals the answer key, runs the system without the key,
and scores per pack after the seal is verified. It is the detection-rate claim
the demo packs cannot make; the pre-registration and author guide are in
`docs/`. A pilot sheet (`tests/fixtures/heldout/pilot.json`) exercises the
renderer and scoring; its numbers are development data.

### Blind held-out test `heldout-v1` (November 2026)

**The set.** GPT-6, a model outside the build, wrote 60 applications from the
author brief without seeing the code: 30 fraudulent (5 each of invoice
overbilled, goods not shipped, order never issued, financials inflated,
third-party payment and round trip) and 30 legitimate (15 clean, and 3 each of
split payment, supplier refund, payment agent, intra-group payer and disclosed
directorship). The system was frozen at tag `eval-v1` (the merge of PR #6), the
key's hash was committed before the run (`2e89bb8`), each pack ran once, and
every result is reported. Everything is in `data/heldout/`: the sheet, the key,
the seal, both runs and the scored report the Model & controls page shows.

**Rules alone, all 60 packs (the route the system applies).** 20 of 30 frauds
sent for review (95% interval 49-81%); 0 of 30 legitimate packs sent for review
(0-11%), the 15 look-alikes included.

| Fraud type | Sent for review | Why |
|---|---|---|
| Invoice overbilled, goods not shipped, financials inflated, third-party payment | 20 of 20 | Each has a rule |
| Order never issued | 0 of 5 | `XDOC_PO_INVOICE` found all five, but as one medium finding (15 points) under the review threshold (20), so each stayed on fast track. The demo packs never tested this fault alone: in `invoice_mismatch` it sat beside three others |
| Round trip | 0 of 5 | No rule looks for it, by design: it is the AI team's case |

**With the AI team (log-only, not applied), on 25 packs:** the 10 frauds the
rules sent through and the 15 look-alikes, the packs where the AI team could
change the outcome (US$6.92 of Bedrock).

- 9 of the 10 frauds would reach review. The agents found 4 of the 5 round
  trips and code confirmed each; one (H-041) was missed. The shadow scorer
  proposed review for all 5 missing orders: it judged the rule's medium finding
  worth a person, which says more about the threshold than about AI detection.
- 2 of the 15 look-alikes were flagged (a payment agent and a split payment),
  each on a code-verified undisclosed related party. The cause is the renderer:
  it gave many generated companies the same two officers, so those applicants'
  registry records really do show an officer shared with a company they trade
  with. By the pre-registered rule they count as false alarms.
- With the AI team's route, 29 of 30 frauds would reach review.

**What it does and does not show.** Every planted fault was detected or was a
case left to the AI team by design, and no legitimate pack was flagged by the
rules. It is not a rate on real applications: the packs are synthetic, drawn
only from typologies the renderer knows (nine of eleven have a rule), in the
document layouts the deterministic parser was written for (extraction `stub`;
OCR and Bedrock extraction were off), with a perfectly consistent mock
registry. Real documents would cost recall and, above all, the zero false-alarm
rate. Thirty frauds is a small sample.

**Follow-ups, none applied to this set:** a single missing-order finding should
reach review (raise its severity or lower the threshold, then measure on a new
set); the renderer must draw unique officer names and company names, and fit
long names to the letterhead; rerun a sample with OCR and Bedrock extraction on;
see why the AI team missed H-041.

## 7. API and frontend

`uvicorn mock_gov.app:app --port 8001`, `uvicorn gldb.api:app --reload`, and `cd frontend && npm install && npm run dev` (UI on http://localhost:5173).

Screens: **Cases**; **New case** (a demo pack or an upload, each document in its own slot); a **live run** showing each stage as it runs; the **case workspace**, twelve stage pages (OCR, Intake, Prompt screen, Extract, Official records, Rule checks, Agents, Score, Readiness, Route, Investigation, Audit record) beside a document viewer; and **Model & controls** (evaluation, the blind test, capabilities, signal library, spend). The viewer highlights a finding on its page, and its forensic overlay boxes every finding on a page at once, coloured by severity.

The analyst confirms or dismisses each flag (a dismissal needs a reason code), can request information, escalate, approve or decline. An enhanced-review case cannot be approved until the MLRO, a different person from the analyst who escalated, has reviewed it. The API returns masked Evidence only, never the raw extracted pack.

## 8. Testing

`pytest` runs the offline suite (about 1,600 tests, no AWS calls). Live AWS tests are opt-in: `RUN_LIVE=1 pytest tests/test_live_aws.py -v` (about US$0.10-0.30).

## 9. Known limitations

- Thresholds (amount tolerance, statement recency, score bands, name-match similarity) are our assumptions: the sponsor says none are fixed in practice. They live in `gldb/config.py` and are labelled as such.
- Detection is validated only on synthetic packs; mixed-font detection in particular may false-alarm on real bank PDFs, and name screening flags about 1.25% of common two-word names.
- The Claude calls may be processed outside Singapore (section 5).
- The AI team's evaluation on the demo packs predates the November rule changes, and its blind-test run covered 25 packs once each; the team is not deterministic, so these are single samples.
- The analyst confirms or dismisses rule findings, not AI leads: a lead is a proposal with no decision recorded against it.
- The test packs' trade documents are still the plain text layout, and the styled demo documents are read from their text layer, not yet from a scan of them.
- Images, photos and screenshots of documents (which the sponsor says arrive in practice) are not handled; there is no image forensics.
- One shared sign-in guards the hosted server; inside the app people are identified by the name they enter. Storage is local files (uploads, cases, audit log), so it is not multi-user or tamper-proof. `data/cases/<id>/result.json` keeps the full extracted pack, unmasked, on local disk (not exposed by the API).
- The frontend has no unit tests; it is type-checked, built and checked in a browser by hand.

## 10. What is next

Highest value first.

1. **Model access.** Ask AWS (a Service Quotas case for `L-B46CFAB8`) to enable Claude Sonnet 5 in ap-southeast-1, which gives in-Singapore processing; the code then needs a small change, since newer models reject the temperature and forced tool choice it sends.
2. **A fresh AI-team evaluation** on the demo packs (Actions > Run evaluation, about US$3), since the saved one predates the November rule changes; then see why the team missed blind-test pack H-041.
3. **Explaining the score:** each check's severity, points and whether it forces enhanced review, shown in one register.
4. **Images and scans:** photographed and screenshotted documents, styled invoices from scans (or Textract `AnalyzeExpense`), and image forensics (error-level analysis, copy-move, signatures) tuned on a hand-made tamper set.
5. **Blind-test follow-ups:** let a single missing-order finding reach review, and draw unique officer and company names in the renderer (section 6), then measure on a new set.
6. **Risk report** in the shape the sponsor described: key findings, risks with evidence, follow-up actions.
7. **Production storage and login (the prototype is hosted on one server today):** S3 for uploads, DynamoDB for cases, S3 Object Lock for the audit log, Cognito for a login per person.
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
infra/               CloudFormation: the guardrail, GitHub's deploy role, the server's stop schedule
.github/workflows/   CI and deploy, Start server, Run evaluation
data/packs/          Demo packs and their ground truth
data/mock_gov/       The mock service's records
data/reference/      Sanctions designations, the bank's own names, FATF high-risk countries
data/templates/      The team's sample documents and the official blank forms (read-only)
data/heldout/        The blind test heldout-v1: sheet, key, seal, results, report
tests/               Offline pytest suite; tests/fixtures/packs holds the test packs
docs/                This status, the architecture and explainer pages, the blind-test plan and author guide
```
