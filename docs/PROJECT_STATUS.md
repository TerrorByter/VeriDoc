# Project status: what is built and what is next

**Project:** Agentic Fraud Detection in Corporate Loan Documentation (sponsor: Green Link Digital Bank).
**Status:** working prototype, synthetic data only. It never approves or rejects a loan; a human officer decides.

## 1. What the system does

A loan officer uploads a set of corporate loan documents. The system extracts their fields, runs rule checks, in groups, that look for tampering and inconsistencies, produces a rule-based fraud-risk score with explainable evidence, and records every officer action in an audit log.

```
Upload -> Intake -> Extraction -> Agents (parallel) -> Scoring -> Explain -> Officer review
                                                                          (flag decisions, final decision, audit log)
```

| Stage | What it does | Where |
|---|---|---|
| Intake | Validates the PDF, hashes it, reads metadata and text, classifies the document type | `gldb/intake.py` |
| Extraction | Turns documents into structured fields. Deterministic parser (`stub`) or AWS Bedrock (`llm`); Textract OCR for scans | `gldb/extract*.py`, `gldb/ocr.py`, `gldb/llm_client.py` |
| Agents | Four specialists emit Evidence, run in parallel, merged in a fixed order | `gldb/checks.py` (section E) |
| Scoring | Rule-based score and band from fraud signals only; no AI sets the score | `gldb/scoring.py` |
| Explain | Optional plain-English summary; accepted only if every citation and number is grounded, else a template | `gldb/explain.py` |
| API and UI | FastAPI backend and a React + TypeScript frontend | `gldb/api.py`, `frontend/` |
| Audit | Append-only JSONL log of pipeline runs and officer actions | `gldb/audit.py` |
| Evaluation | Scores the pipeline against each pack's ground truth | `gldb/evaluate.py` |

### Design principles (from `.kiro/steering/`)
1. Deterministic Python for anything computable; LLMs only for extraction and explanation.
2. Every finding is an `Evidence` object; nothing exists outside Evidence.
3. The score is rule-based and only fraud signals feed it. Compliance and completeness findings drive a separate Application Readiness status.
4. A human makes the final call.
5. Uploaded document text is untrusted data, never instructions.
6. Synthetic data only. Personal identifiers are masked in Evidence, UI and logs.

## 2. The rule groups

| Rule group | Checks | What it catches |
|---|---|---|
| **Document Integrity** | 6 | Invoice arithmetic, bank running balance and continuity, financial-statement balance, PDF modified after creation, producer mismatch |
| **Forensics** | 3 | Hidden text (white, tiny or off-page, where injected instructions live), figures whose digits mix fonts (edited over the original), PDFs re-saved after creation |
| **Cross-Document** | 3 | Identity (UEN and names) across documents, invoice vs bank credit, invoice vs purchase order (over-billing, no order, billed before ordered) |
| **Compliance & Completeness** | 16 | Missing documents, bank statement coverage, FATCA/CRS entity form validity and reasonableness against the application |

Forensics reads the raw file through `ExtractedPack.paths`, which is excluded from serialisation so local paths never reach results or the API. Official form templates are skipped by forensics because they legitimately use white headings and mixed fonts.

## 3. Document types handled

Invoice, bank statement, financial statements summary, purchase order (trade record), FATCA/CRS entity self-certification (AcroForm, read deterministically and never sent to an LLM). The application data (`application.json`) is the reference for reasonableness checks.

## 4. AWS integration

- **Bedrock (Converse API, temperature 0):** one wrapper, `gldb/llm_client.py`. Structured extraction forces the model to call a tool with the target JSON schema. Money is accepted as printed (commas, `SGD`, `$`) and validated as `Decimal`. Every LLM source quote must appear verbatim in the visible text or it is dropped. Hidden text is removed before the model sees a document. Usage logs record token counts only, never text.
- **Textract:** `gldb/ocr.py` OCRs scanned PDFs (pages with no text layer) when `OCR_ENABLED=true`.
- **Configuration:** `.env` (gitignored). Credentials come from an AWS profile (`aws configure --profile gldb`), never from the repo. See the README section "Connecting AWS".

**Live status:** Bedrock works from a developer machine (Sonnet 4.6 inference profile; plain and forced-schema calls pass). Textract is blocked by an account activation error (`SubscriptionRequiredException`) that the account owner has to clear. The first live LLM extraction run found and led to the money-formatting fix; a full live evaluation has not yet completed.

## 5. Synthetic data and evaluation

`data/packs/` holds three fictional packs, each with a `ground_truth.json`:

| Pack | Purpose | Expected |
|---|---|---|
| `clean` | Benign pack (includes a legitimate rounding difference) | Low risk, no findings |
| `tampered` | Inflated invoice, wrong UEN, edited metadata, altered closing balance, hidden prompt injection | High risk; 8 fraud checks fire |
| `incomplete` | Missing April statement and a badly completed FATCA/CRS form | Low risk, readiness Incomplete |

`python -m gldb.evaluate` reports per-pack band and readiness, missed and unexpected findings, precision, recall and false positives. The deterministic parser currently scores 1.00 on all metrics with no false positives on the clean and incomplete packs. Run with `EXTRACTION_MODE=llm` to compare Bedrock against the same ground truth.

## 6. API and frontend

`uvicorn gldb.api:app --reload` and `cd frontend && npm install && npm run dev` (UI on http://localhost:5173).

The UI has: an upload panel and demo-pack buttons; a risk-score dial; an application-readiness checklist; an analysis pipeline panel with each agent's findings and timings; findings by category, sorted by severity, filterable by agent, with confirm and dismiss per finding; a documents panel; an officer decision panel; and the audit trail. The API returns masked Evidence only, never the raw extracted pack.

## 7. Testing

`pytest` runs the offline suite (no AWS calls). Live AWS tests are opt-in: `RUN_LIVE=1 pytest tests/test_live_aws.py -v`.

## 8. Known limitations

- Detection thresholds are validated only on synthetic packs; mixed-font detection in particular may false-alarm on real bank PDFs.
- Purchase orders are matched to invoices by buyer name; a real PO would carry an invoice reference, which is a stronger match.
- Hidden text is stripped only on the LLM path; the stub parser and classifier still read the full text.
- `data/cases/<id>/result.json` stores the full extracted pack, including unmasked identifiers, on local disk (not exposed by the API).
- No authentication: every action is recorded as `officer`.
- Storage is local files (uploads, cases, audit log), so it is not multi-user or tamper-proof.
- The evaluation harness aborts on an extraction error instead of reporting it per pack.
- The React UI has been type-checked and built, and its API flow tested, but not yet reviewed visually in a browser or covered by frontend tests.
- Bedrock extraction for purchase orders and the explain step are tested only with fakes so far.
- Textract and Bedrock share one `AWS_REGION` setting.

## 9. What is next

Highest value first.

1. **Finish live verification.** Run the offline suite after the money-format fix, then `EXTRACTION_MODE=llm python -m gldb.evaluate`, and fix whatever else the real model exposes. Turn on and check the explain step.
2. **Activate Textract** (account owner opens the service in the console; see the README). Optionally add a `TEXTRACT_REGION` setting.
3. **Scanned and image inputs.** Generate scanned and image-tampered pack variants, and add the text-layer vs OCR mismatch check (Textract cannot see hidden text, so any difference between what it reads and the PDF text layer exposes overlays and edits).
4. **Image forensics.** Error-level analysis, copy-move detection and signature checks, tuned on the team's hand-made tamper set rather than guessed thresholds.
5. **More document types.** Contracts and identity documents (and invoice-reference matching for purchase orders), each following the purchase-order pattern: schema, generator, stub parser, LLM schema, cross-document check, ground truth.
6. **Semantic review agent.** Bedrock reads contracts and trade records for qualitative red flags; every finding must carry a verifiable citation and it only emits Evidence.
7. **Frontend polish and tests.** Browser QA, highlighting the exact region of a finding in the source document, frontend tests.
8. **Hosting, storage and auth (only if the sponsor wants a live demo).** One container (FastAPI plus the built React app) on App Runner or ECS with an IAM role; S3 for uploads, DynamoDB for cases, S3 Object Lock for the audit log, Cognito for login.
9. **Technical report** (a project deliverable): architecture, agent design, detection logic and validation rules, evaluation metrics and results, ablation of each agent's contribution.

### Tasks for teammates (no coding needed)
- Ask the sponsor (Patrick Sze) how officers review today, red flags they have seen, amount tolerances, statement recency rules, and the report they want. Send answers as a doc.
- Make a hand-tampered set: edit clean PDFs in Word, Canva or Preview (amounts, names, pasted signatures, edited scans) and log each change in a spreadsheet.
- Build a fraud-type catalogue from MAS notices, ACFE reports and news cases: fraud type, document, what is altered, how a human spots it.
- Generate fictional contracts, identity documents and trade records with AI, for the same fictional company, with a row per file saying what is wrong (fictional data only).
- Draft report sections from this document and the README; write the demo script; test the app and log bugs and misleading UI text.

## 10. Repository map

```
gldb/                Backend package (see table in section 1 and section 2)
  schemas.py         Pydantic models: documents, Evidence, results, audit event
  config.py          All constants and environment settings
  data_factory.py    Generates the synthetic packs
  scoring.py         Rule-based risk score and application readiness
  forensics.py       PDF forensics primitives
  form_maps.py       FATCA/CRS AcroForm field maps
  privacy.py         Identifier masking
frontend/            React + TypeScript (Vite) UI
data/packs/          Synthetic packs and ground truth
data/templates/      Official blank FATCA/CRS forms (read-only)
tests/               Offline pytest suite; tests/test_live_aws.py is opt-in
.kiro/steering/      Product, structure and tech rules the code follows
docs/                This document
```
