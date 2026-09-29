---
inclusion: always
---
# Structure
gldb-prototype/
  gldb/__init__.py
  gldb/config.py         constants and env settings
  gldb/schemas.py        all Pydantic models
  gldb/data_factory.py   generates synthetic PDF packs
  gldb/intake.py         file validation, hashing, metadata, text, classification
  gldb/extract_stub.py   deterministic parser for our own generated layout
  gldb/llm_client.py     the only Bedrock wrapper
  gldb/extract_llm.py    LLM extraction
  gldb/extract.py        dispatcher: llm or stub
  gldb/checks.py         deterministic checks, each returns list[Evidence]
  gldb/scoring.py        rule-based risk score
  gldb/audit.py          append-only JSONL audit log
  gldb/pipeline.py       run(case_id, file_paths) -> CaseResult
  gldb/explain.py        optional LLM summary with citation check
  app.py                 Streamlit UI
  data/packs/clean/  data/packs/tampered/  data/uploads/  data/cases/
  logs/audit_log.jsonl  logs/llm_usage.jsonl
  tests/

Pipeline stage functions are named after future LangGraph nodes: intake -> classify_and_extract -> run_checks -> score -> explain. Keep them as separate functions so the pipeline can later move to LangGraph unchanged.
