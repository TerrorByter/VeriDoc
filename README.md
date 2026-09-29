# gldb-prototype

**PROTOTYPE** — Green Link Digital Bank (GLDB) multi-agent fraud detection system for corporate loan documents. Uses synthetic data only. Not for production use.

## What it does

A loan officer uploads a set of corporate loan documents (invoices, bank statements, financial statements summary). The system extracts fields, runs deterministic fraud checks, and returns an explainable risk assessment. The officer reviews each flag and makes the final decision. The system never approves or rejects a loan.

## Setup

### 1. Prerequisites

- Python 3.11
- AWS account with Bedrock access (Claude model enabled in your region)

### 2. Create a virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and fill in your AWS credentials and Bedrock model ID. `.env` is gitignored and must never be committed.

### 5. Run tests

```bash
pytest
```

Tests do not call AWS by default. To run live Bedrock tests:

```bash
RUN_LIVE=1 pytest
```

### 6. Run the app

```bash
streamlit run app.py
```

## Project structure

```
gldb/               Core package
  config.py         Constants and env settings
  schemas.py        All Pydantic models (Evidence, CaseResult, …)
  data_factory.py   Generates synthetic PDF document packs
  intake.py         File validation, hashing, metadata, text extraction, classification
  extract_stub.py   Deterministic parser for generated document layout
  llm_client.py     The only AWS Bedrock wrapper (Converse API, temperature 0)
  extract_llm.py    LLM-based field extraction
  extract.py        Dispatcher: routes to stub or LLM extractor
  checks.py         Deterministic fraud checks, each returns list[Evidence]
  scoring.py        Rule-based risk score (LLMs never touch the score)
  audit.py          Append-only JSONL audit log
  pipeline.py       run(case_id, file_paths) -> CaseResult
  explain.py        Optional LLM plain-English summary with citation check
app.py              Streamlit UI
data/
  packs/clean/      Synthetic clean document packs
  packs/tampered/   Synthetic tampered document packs
  uploads/          Runtime uploads (gitignored)
  cases/            Processed case outputs (gitignored)
logs/
  audit_log.jsonl   Append-only officer action + pipeline log (gitignored)
  llm_usage.jsonl   LLM call log (gitignored)
tests/              pytest suite
```

## Key constraints

- **No real data.** Synthetic data only.
- **No LLM scoring.** Risk score is fully deterministic (rule-based).
- **Decimal money.** `decimal.Decimal` quantized to `0.01`, never `float`.
- **Single LLM gateway.** All Bedrock calls go through `gldb/llm_client.py`.
- **Audit log is immutable.** Never edited or deleted.
