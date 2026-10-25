---
inclusion: always
---
# Structure
gldb-prototype/
  gldb/config.py          constants and env settings (thresholds are our assumptions, kept here)
  gldb/schemas.py         all Pydantic models
  gldb/pipeline.py        run(case_id, file_paths) -> CaseResult; assess() runs the stages
  gldb/intake.py          file validation (PDF, .xlsx), hashing, metadata, text, classification
  gldb/workbook.py        read-only .xlsx reader
  gldb/ocr.py             Textract OCR of every page except the FATCA/CRS forms
  gldb/prompt_screen.py   Bedrock guardrail screen for instructions aimed at AI models
  gldb/extract.py         dispatcher: llm or stub
  gldb/extract_stub.py    deterministic parsers (our layouts, AcroForms, ACRA profile, IRAS notice, workbook)
  gldb/extract_llm.py     LLM extraction
  gldb/llm_client.py      the only Bedrock wrapper
  gldb/form_maps.py       FATCA/CRS AcroForm field maps
  gldb/official.py        fetches ACRA and IRAS records (the mock service in the prototype)
  gldb/checks.py          deterministic checks in rule groups, each returns list[Evidence]
  gldb/forensics.py       PDF forensics primitives
  gldb/screening.py       deterministic name screening
  gldb/reference.py       versioned reference data: sanctions lists, FATF high-risk countries
  gldb/sanctions_sync.py  updates the UN designations from MAS's page
  gldb/ordering.py        the ordering policy of a trade (order, shipment, invoice, payment)
  gldb/signal_library.py  every check as a named, versioned signal
  gldb/scoring.py         rule-based risk score and application readiness
  gldb/routing.py         review path from the score, hard flags, confidence and readiness
  gldb/reconciliation.py  transaction chains: invoice, order, shipment, payment
  gldb/timeline.py        every dated fact in order
  gldb/investigation.py   the analyst's queue, and what resolving each task would change
  gldb/coverage.py        share of risk findings traceable to their source
  gldb/ai_team/           AI team: team.py (Supervisor and specialists), loop.py, tools.py, prompts.py,
                          verify.py (gate on AI output), checks.py (code checks of leads), shadow.py
  gldb/lifecycle.py       case lifecycle as an event-sourced state machine (MLRO gate)
  gldb/audit.py           append-only JSONL audit log
  gldb/feedback.py        how often analysts override each check, and why
  gldb/usage.py           usage metering and cost per case
  gldb/privacy.py         identifier masking
  gldb/viewer.py          a page as an image, and where a quote sits on it
  gldb/jobs.py            progress of background pipeline runs
  gldb/api.py             FastAPI app used by the frontend
  gldb/evaluate.py        scores the pipeline against each pack's ground truth
  gldb/agent_eval.py      champion vs challenger evaluation of the AI team
  gldb/demo_packs.py      builds the demo packs from the team's sample
  gldb/demo_trade.py      the invoices, orders and bills of lading behind each demo pack's trade
  gldb/invoice_pdf.py     prints a styled tax invoice whose figures add up
  gldb/data_factory.py    generates the text-only test packs (tests/fixtures/packs)
  mock_gov/               mock ACRA and IRAS service (port 8001)
  frontend/               React + TypeScript (Vite) UI
  infra/guardrail.yaml    the prompt screen's guardrail
  data/packs/  data/mock_gov/  data/reference/  data/templates/  data/uploads/  data/cases/
  logs/audit_log.jsonl  logs/usage.jsonl
  tests/  tests/fixtures/packs/

Pipeline stage functions are named after future LangGraph nodes: ocr -> intake -> prompt_screen -> extract -> records -> rule_checks -> score -> readiness -> route -> ai_team -> shadow_score -> projections -> record (ASSESS_STAGES in gldb/pipeline.py). Keep them as separate functions so the pipeline can later move to LangGraph unchanged.
