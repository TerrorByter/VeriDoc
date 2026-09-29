---
inclusion: always
---
# Tech
- Python 3.11, Pydantic v2, pdfplumber, pypdf, reportlab, streamlit, boto3, python-dotenv, pytest.
- Do NOT use LangGraph, FastAPI, React, Postgres or PyMuPDF in this prototype.
- Money is decimal.Decimal, quantized to 0.01 with ROUND_HALF_UP. Never float for money.
- Currency SGD. GST rate 9%. Amount tolerance SGD 1.00.
- All LLM calls go through gldb/llm_client.py only (AWS Bedrock Converse API, temperature 0).
- All configuration comes from gldb/config.py, which loads .env. Secrets live only in .env, which is gitignored.
- Every module has pytest tests in tests/. Tests must not call AWS unless the environment variable RUN_LIVE=1.
- Type hints everywhere. Small pure functions. No global mutable state.
- GLDB's FATCA/CRS forms are fillable AcroForms. Read them deterministically with pypdf get_fields() through gldb/form_maps.py. Never send form contents to an LLM.
- Personal identifiers (NRIC/FIN, passport numbers, personal TINs, SSN) are masked with gldb.privacy.mask_id in Evidence, UI and logs. Full values never appear in logs/.
- Official blank templates live in data/templates/ and are read-only.
