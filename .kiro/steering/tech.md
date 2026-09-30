---
inclusion: always
---
# Tech
- Backend: Python 3.11, Pydantic v2, pdfplumber, pypdf, reportlab, boto3 (Bedrock, Textract), FastAPI + uvicorn, python-dotenv, pytest.
- Frontend: React + TypeScript on Vite, plain CSS, in frontend/. It talks to the backend only through /api (gldb/api.py).
- Do NOT use LangGraph, Postgres or PyMuPDF in this prototype. Do not add a UI framework or state library without a reason.
- Money is decimal.Decimal, quantized to 0.01 with ROUND_HALF_UP. Never float for money.
- Currency SGD. GST rate 9%. Amount tolerance SGD 1.00.
- All LLM calls go through gldb/llm_client.py only (AWS Bedrock Converse API, temperature 0).
- All configuration comes from gldb/config.py, which loads .env. Secrets live only in .env, which is gitignored.
- Every module has pytest tests in tests/. Tests must not call AWS unless the environment variable RUN_LIVE=1.
- Type hints everywhere. Small pure functions. No global mutable state.
- GLDB's FATCA/CRS forms are fillable AcroForms. Read them deterministically with pypdf get_fields() through gldb/form_maps.py. Never send form contents to an LLM.
- Personal identifiers (NRIC/FIN, passport numbers, personal TINs, SSN) are masked with gldb.privacy.mask_id in Evidence, UI and logs. Full values never appear in logs/.
- Official blank templates live in data/templates/ and are read-only.

## Multi-agent direction
- The system is meant to become multi-agent: specialist agents (integrity/forensics, cross-document, compliance, semantic review) each emit Evidence, and an orchestrator merges them.
- Agents propose, deterministic code decides: agents only emit Evidence. Scoring stays rule-based, and an LLM agent's findings must carry a verifiable citation.
- Agents live in checks.py section E: an Agent is a name, a description and a list of checks; run_agents() runs them in parallel and merges Evidence in AGENTS order. Current agents: Document Integrity, Forensics (hidden text, mixed fonts, incremental saves; reads the raw file via pack.paths), Cross-Document, Compliance & Completeness. New agents (e.g. forensics, semantic review) are added to AGENTS and must only emit Evidence.
