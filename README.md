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
4. Turn features on: `EXTRACTION_MODE=llm` (Bedrock extraction), `OCR_ENABLED=true` (Textract for scans), `AGENTS_ENABLED=true` (AI agent team on Bedrock). Restart the backend after editing `.env`.

Check the connection with `aws sts get-caller-identity --profile gldb`, then the live tests below.

### 5. Run tests

```bash
pytest
```

Tests do not call AWS by default. To run the live AWS smoke tests (one Bedrock call, one forced-schema call, one Textract call, and one full AI team run on the `third_party_payment` pack, about US$0.10-0.30):

```bash
RUN_LIVE=1 pytest tests/test_live_aws.py -v
```

To score the pipeline against the ground truth of every demo pack in `data/packs/`:

```bash
python -m gldb.evaluate                        # deterministic parser
EXTRACTION_MODE=llm python -m gldb.evaluate    # Bedrock (about 30 small calls)
python -m gldb.evaluate --save                 # also keep the report for the UI's controls page
```

To score the AI team (champion vs challenger) on the same packs:

```bash
python -m gldb.agent_eval [--supervisor M] [--specialist M] [--save]
# One model setup per run (defaults: the configured models). Calls Bedrock whatever
# AGENTS_ENABLED says; about US$1-3 per run. Run it again with other models for the challenger.
```

### 6. Run the app

```bash
uvicorn mock_gov.app:app --port 8001   # mock ACRA and IRAS records on :8001
uvicorn gldb.api:app --reload          # backend on :8000
cd frontend && npm install && npm run dev   # UI on :5173 (proxies /api)
```

The pipeline fetches each case's ACRA record and the guarantors' Notices of
Assessment from the mock service, as a bank would from ACRA and Myinfo. Without
it running, a case is marked incomplete ("ACRA and IRAS records could not be
fetched"). Its data is `data/mock_gov/acra.json` and `iras.json`, edited by hand
or rebuilt for the demo companies by `python -m gldb.demo_packs`.

Names are screened against the individuals and entities designated on the UN
lists that MAS names (DPRK, Iran, Libya, the Congo, Somalia, Sudan, South Sudan,
Yemen, counter-terrorism), kept in `data/reference/designations.json`. Update it
from MAS's page whenever you like, or leave a watcher running:

```bash
python -m gldb.sanctions_sync             # check MAS and the UN once; replaces the file if a designation changed
python -m gldb.sanctions_sync --every 24  # check every 24 hours until stopped
```

A sync that cannot read MAS's page or any list leaves the file as it was, and
keeps the list it replaces in `data/reference/history/`. A running backend picks
the new file up without a restart. The bank's own hand-kept names are in
`data/reference/sanctions.json` (the fictional demo names) and are screened too.

The UI has four screens: **Cases** (every case, newest first), **New case**
(demo packs or an upload), a **live run** that shows each pipeline stage as it
runs, and the **case workspace**, where each of the 11 stages has its own page
(the arrow keys step through them) beside the document viewer. **Model &
controls** shows the saved evaluation, capabilities, signal library and spend.
Starting a case returns at once (202) and the pipeline runs in the background;
the browser polls `GET /api/cases/{id}/progress`.

## Project structure

```
gldb/               Core package
  config.py         Constants and env settings
  schemas.py        All Pydantic models (Evidence, CaseResult, …)
  demo_packs.py     Builds the demo packs from the team's sample, and the demo companies' ACRA/IRAS records
  data_factory.py   Generates the text-only test packs (tests/fixtures/packs), the only ones with trade documents
  official.py       Fetches a case's ACRA and IRAS records (the mock service in the prototype)
  sanctions_sync.py Updates the UN designations from MAS's page: its lists, each UN page, each XML list
  reference.py      The lists names are screened against: the designations and the bank's own watchlist
  screening.py      Deterministic name screening (names and aliases) against them
  intake.py         File validation (PDF and .xlsx), hashing, metadata, text extraction, classification
  workbook.py       Read-only .xlsx reader (standard library): sheets, cells, file properties
  extract_stub.py   Deterministic parsers: the generated layout, AcroForms, ACRA profile, IRAS notice, financial workbook
  llm_client.py     The only AWS Bedrock wrapper (Converse API; extraction at temperature 0, agent turns send none)
  extract_llm.py    LLM-based field extraction
  extract.py        Dispatcher: routes to stub or LLM extractor
  checks.py         Deterministic checks grouped into agents; run_agents() merges Evidence
  forensics.py      PDF forensics primitives (hidden text, mixed fonts, incremental saves)
  scoring.py        Rule-based risk score (LLMs never touch the score)
  audit.py          Append-only JSONL audit log
  pipeline.py       run(case_id, file_paths) -> CaseResult
  ai_team/          AI agent team: a Supervisor and four specialists on Bedrock
    tools.py        The tools the agents call, and the ledger of what they received
    loop.py         One agent's tool-use loop (step and token limits)
    verify.py       Deterministic gate: withholds leads and memo points that are ungrounded or give a verdict
    prompts.py      Supervisor and specialist prompts, with their versions
    team.py         run_team(): plan, fan out, verify leads, reconcile into a memo
  agent_eval.py     Champion vs challenger evaluation of the AI team on the packs
  api.py            FastAPI app used by the frontend
  jobs.py           Progress of background pipeline runs, for the live run screen
  ocr.py            Textract OCR for scanned PDFs
  viewer.py         Evidence viewer: a page (or workbook sheet) as an image, and where a quote sits on it
mock_gov/           Mock ACRA and IRAS records service (stands in for the ACRA and Myinfo APIs)
frontend/           React + TypeScript (Vite) UI
data/
  packs/            Demo packs in the format of the team's sample (one folder per case)
  mock_gov/         The mock service's ACRA and IRAS records
  reference/        designations.json (UN designations, from the sync; history/ keeps replaced ones) and sanctions.json (the bank's own names)
  templates/sample/ The team's sample documents the demo packs are edited from
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
