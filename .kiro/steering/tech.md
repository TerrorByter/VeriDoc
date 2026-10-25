---
inclusion: always
---
# Tech
- Backend: Python 3.13, Pydantic v2, pdfplumber, pypdf, reportlab, boto3 (Bedrock, Textract), FastAPI + uvicorn, python-dotenv, pytest.
- Frontend: React + TypeScript on Vite, plain CSS, in frontend/. It talks to the backend only through /api (gldb/api.py).
- Do NOT use LangGraph, Postgres or PyMuPDF in this prototype. Do not add a UI framework or state library without a reason.
- Money is decimal.Decimal, quantized to 0.01 with ROUND_HALF_UP. Never float for money.
- Currency SGD. GST rate 9%. Amount tolerance SGD 1.00.
- All LLM calls go through gldb/llm_client.py only (AWS Bedrock Converse API; extraction at temperature 0, agent turns send none).
- All configuration comes from gldb/config.py, which loads .env. Secrets live only in .env, which is gitignored.
- Every module has pytest tests in tests/. Tests must not call AWS unless the environment variable RUN_LIVE=1.
- Type hints everywhere. Small pure functions. No global mutable state.
- GLDB's FATCA/CRS forms are fillable AcroForms. Read them deterministically with pypdf get_fields() through gldb/form_maps.py. Never send form contents to an LLM.
- Personal identifiers (NRIC/FIN, passport numbers, personal TINs, SSN) are masked with gldb.privacy.mask_id in Evidence, UI and logs. Full values never appear in logs/.
- Official blank templates live in data/templates/ and are read-only.

## Rule groups and the AI team
- Deterministic checks live in gldb/checks.py, grouped into rule groups (section E): a RuleGroup is a name, a description and a list of checks; run_rule_checks() runs them in parallel and merges Evidence in RULE_GROUPS order. Current groups: Document Integrity, Forensics (reads the raw file via pack.paths), Cross-Document, Registry & Screening, Timeline, Compliance & Completeness. A new check joins a group and gets a signal in gldb/signal_library.py.
- The AI team (gldb/ai_team/) is a Supervisor and four specialists (forensics, transaction, KYC, timeline) on Bedrock with read-only tools. Agents propose, deterministic code decides: a lead must name its subject by id and cite tool results, code sets its evidence status, and nothing the team or the shadow scorer produces changes the score or the route.
