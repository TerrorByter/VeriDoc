# gldb-prototype

**PROTOTYPE** — Green Link Digital Bank (GLDB) multi-agent fraud detection system for corporate loan documents. Uses synthetic data only. Not for production use.

> Current status, what is built and what is next: [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md)

## What it does

A loan officer uploads a set of corporate loan documents (invoices, bank statements, financial statements summary). The system extracts fields, runs deterministic fraud checks, and returns an explainable risk assessment. The officer reviews each flag and makes the final decision. The system never approves or rejects a loan.

## Setup

### 1. Prerequisites

- Python 3.11
- Node 20+ (frontend)
- AWS account with Bedrock access (Claude model enabled in your region); Textract if you want to OCR scans

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

Edit `.env`. `.env` is gitignored and must never be committed. Each teammate needs their own.

#### Connecting AWS (optional; the app runs without it using the deterministic parser)

1. Install the CLI (`brew install awscli`) and save an IAM user's keys as a profile: `aws configure --profile gldb` (region `ap-southeast-1`). The keys live in `~/.aws/credentials`, never in the project.
2. The IAM user needs `bedrock:InvokeModel` and `textract:DetectDocumentText` (Resource `*`). Textract also has to be activated once for the account.
3. In `.env`, set `AWS_PROFILE=gldb`, `AWS_REGION` and `BEDROCK_MODEL_ID` (a model ID, inference profile ID or inference profile ARN from the Bedrock console).
4. Turn features on: `EXTRACTION_MODE=llm` (Bedrock extraction), `OCR_ENABLED=true` (Textract for scans), `EXPLAIN_ENABLED=true` (LLM summary). Restart the backend after editing `.env`.

Check the connection with `aws sts get-caller-identity --profile gldb`, then the live tests below.

### 5. Run tests

```bash
pytest
```

Tests do not call AWS by default. To run the live AWS smoke tests (one Bedrock call, one forced-schema call, one Textract call):

```bash
RUN_LIVE=1 pytest tests/test_live_aws.py -v
```

To score the extractor against the ground truth of every pack in `data/packs/`:

```bash
python -m gldb.evaluate                        # deterministic parser
EXTRACTION_MODE=llm python -m gldb.evaluate    # Bedrock (about 30 small calls)
```

### 6. Run the app

```bash
uvicorn gldb.api:app --reload          # backend on :8000
cd frontend && npm install && npm run dev   # UI on :5173 (proxies /api)
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
  checks.py         Deterministic checks grouped into agents; run_agents() merges Evidence
  forensics.py      PDF forensics primitives (hidden text, mixed fonts, incremental saves)
  scoring.py        Rule-based risk score (LLMs never touch the score)
  audit.py          Append-only JSONL audit log
  pipeline.py       run(case_id, file_paths) -> CaseResult
  explain.py        Optional LLM plain-English summary with citation check
  api.py            FastAPI app used by the frontend
  ocr.py            Textract OCR for scanned PDFs
frontend/           React + TypeScript (Vite) UI
data/
  packs/clean/      Synthetic clean document packs
  packs/tampered/   Synthetic tampered document packs
  uploads/          Runtime uploads (gitignored)
  cases/            Processed case outputs (gitignored)
logs/
  audit_log.jsonl   Append-only officer action + pipeline log (gitignored)
  usage.jsonl       Bedrock tokens and Textract pages per case (gitignored)
tests/              pytest suite
```

## Key constraints

- **No real data.** Synthetic data only.
- **No LLM scoring.** Risk score is fully deterministic (rule-based).
- **Decimal money.** `decimal.Decimal` quantized to `0.01`, never `float`.
- **Single LLM gateway.** All Bedrock calls go through `gldb/llm_client.py`.
- **Audit log is immutable.** Never edited or deleted.
