# vca-flow — Voluntary Corporate Action Flow

## Python Environment
This project uses a local virtual environment located in the `./venv` folder.
- Always run Python scripts using the explicit path: `./venv/bin/python` (or `.\venv\Scripts\python.exe` on Windows).
- Always install packages using the explicit path: `./venv/bin/pip install <package>`.
- Never use bare `python`, `python3`, or `pip` commands.

### Dependencies
- Maintain a `requirements.txt` file at the project root listing all required Python libraries.
- Key dependencies: `requests`, `tqdm`, `langchain`, `langchain-openai`, `langgraph`, `pydantic`, `python-dotenv`, `openai` (DeepSeek-compatible SDK), `beautifulsoup4`, `lxml`.

## Overview
This project extracts **voluntary corporate actions** from **SEC EDGAR** filings and parses the available actions using **LangChain** and **LangGraph**.

### Corporate actions of interest
- Tender offers (SC TO-I)
- Going-private transactions (SC 13E3)
- Tender offer solicitations / recommendations (SC 14D9)

These three form types are the primary carriers of voluntary corporate action information in SEC EDGAR filings.

## Project Structure
```
.
├── main.py                    # End-to-end pipeline entry point
├── pyproject.toml             # Ruff linter + pytest config (markers, addopts)
├── .github/
│   └── workflows/
│       └── ci.yml             # GitHub Actions CI pipeline
├── src/
│   ├── __init__.py                        # Package marker
│   ├── download_sec_voluntary_filings.py  # SEC EDGAR filing downloader
│   ├── schema.py                          # Pydantic schemas for agent I/O
│   ├── chain.py                           # LangGraph orchestrator + CLI
│   ├── agents.py                          # Extraction + Validation agents
│   ├── prompts.py                         # Prompt templates for each agent
│   ├── business_rules.py                  # Hard-coded business rules (3 rules)
│   └── tools.py                           # SEC filing I/O, HTML parsing, text prep
├── tests/
│   ├── conftest.py                        # Shared fixtures (sample data, real filing paths)
│   ├── unit/
│   │   ├── test_schema.py                 # Pydantic model validation
│   │   ├── test_tools.py                  # Parsing functions (in-memory data)
│   │   ├── test_prompts.py                # Prompt template formatting
│   │   └── test_business_rules.py         # Business rule logic
│   └── integration/
│       ├── test_tools_integration.py       # Tools against real SEC filings
│       └── test_chain_integration.py       # Dry-run pipeline against real data
├── output/
│   ├── sec_filings/                       # Downloaded filings organized by date/form
│   │   └── YYYY-MM-DD/
│   │       ├── <form_type>/                # e.g. SC TO-I, SC 13E3, SC 14D9
│   │       │   ├── <adsh>.txt             # Full submission text
│   │       │   ├── <adsh>-index.htm        # Filing index page
│   │       │   └── <adsh>-meta.json        # Metadata (CIK, company, form type, etc.)
│   │       └── manifest.json               # Tracks processed filings across runs
│   └── corporate_actions/                  # Final extraction results
│       └── YYYY-MM-DD/
│           └── corporate_action.json       # Validated corporate actions
├── .env                   # API keys (DEEPSEEK_API_KEY) — not committed
├── requirements.txt
└── venv/
```

## Workflow

### Step 1 — Download filings
`src/download_sec_voluntary_filings.py` queries the SEC EDGAR Full-Text Search API for voluntary corporate action filings, then downloads the full submission text and filing index from `www.sec.gov/Archives`.

Key details:
- Target form types: **SC TO-I**, **SC 13E3**, **SC 14D9**
- Respects SEC fair-access requirements: ≤ 10 requests/second (0.12 s delay used) and requires a `USER_AGENT` identifying your organization
- Uses a **manifest** (`manifest.json`) to skip already-downloaded filings across runs
- SEC API caps results at 10,000 hits — narrow date ranges if hitting that limit
- Retries transient server errors (500/502/503) with exponential backoff up to 3 attempts

Usage:
```
./venv/bin/python src/download_sec_voluntary_filings.py                        # today
./venv/bin/python src/download_sec_voluntary_filings.py --date 2026-06-22      # specific date
./venv/bin/python src/download_sec_voluntary_filings.py --date 2026-06-20 --days 5  # date range
./venv/bin/python src/download_sec_voluntary_filings.py --dry-run              # preview only
./venv/bin/python src/download_sec_voluntary_filings.py --cron                 # quiet mode for cron
```

> **Important:** Edit the `USER_AGENT` variable in the script to identify your organization before running.

### Step 2 — Extraction Agent
Implemented in `src/agents.py` (`ExtractionAgent` class).
- Loads the primary filing body from each `.txt` file via `src/tools.py`:
  - Parses the multi-document SEC submission.
  - Selects the first non-exhibit `<DOCUMENT>` matching the target form type.
  - Strips HTML to plain text (BeautifulSoup + lxml).
  - Truncates to ~30K characters for the LLM context window.
- Extracts corporate action details using a DeepSeek LLM with `with_structured_output(method="function_calling")`.
- Information extracted (enforced by `CorporateActionExtraction` Pydantic schema in `src/schema.py`):
  - **Company name**
  - **Form type**
  - **Offer terms** (transaction type, share count, conditions)
  - **Price** (dollar amount or formula)
  - **Expiration date** (ISO format or relative-date description)
  - **Filing ADSH**

### Step 3 — Validation and Verification Agent
Implemented in `src/agents.py` (`ValidationAgent` class) and `src/business_rules.py`.
- Runs **hard-coded business rules first** (deterministic, no LLM cost):
  - **Rule 1** (`rule_expiration_date_valid`): Parses the expiration date, checks it is not before the filing date or already passed.
  - **Rule 2** (`rule_option_types_valid`): Verifies extracted offer terms contain keywords expected for the form type.
  - **Rule 3** (`rule_text_template_sensible`): Detects hallucination markers, placeholder values, and cross-checks dollar amounts against the source text.
- Then runs **LLM-based verification** — compares the extraction against the original filing text and returns a `ValidationResult` (Pydantic model) with:
  - **Confidence score** — Low / Medium / High.
  - **Discrepancies** — specific issues found (rule name, severity, description).
- **Confidence downgrade**: Business-rule errors can downgrade the LLM-assigned confidence but never upgrade it.
- **Output**: Results are merged and written to `corporate_action.json` in the date folder.

### Pipeline orchestration
`src/chain.py` wires Steps 2–3 together with a **LangGraph** StateGraph:
1. `extract` node → ExtractionAgent
2. Conditional edge: if extraction failed → skip validation (record error)
3. `validate` node → ValidationAgent
4. Results collected and written to `output/corporate_actions/YYYY-MM-DD/corporate_action.json`

**`main.py`** is the top-level entry point that runs the full pipeline end-to-end:
1. Download today's SEC filings → `output/sec_filings/`
2. Run the LangGraph extraction + validation pipeline
3. Write final results → `output/corporate_actions/YYYY-MM-DD/corporate_action.json`

CLI usage:
```
./venv/bin/python main.py                              # today (download + process)
./venv/bin/python main.py --date 2026-06-22             # specific date
./venv/bin/python main.py --dry-run                     # preview only
./venv/bin/python main.py --download-only               # only download, skip pipeline
./venv/bin/python main.py --process-only                # only process already-downloaded data
./venv/bin/python main.py --max-filings 5               # limit LLM calls for testing
./venv/bin/python main.py --cron                        # quiet mode
```

Individual steps can also be run directly:
```
./venv/bin/python src/download_sec_voluntary_filings.py --date 2026-06-22
./venv/bin/python src/chain.py --date 2026-06-22
./venv/bin/python src/chain.py --adsh 0001702510-26-000064  # single filing
```

## Testing
Run the full test suite (unit + integration) with pytest:
```
./venv/bin/python -m pytest tests/ -v
```
- **Unit tests** (`tests/unit/`) — fast, no network/disk dependencies beyond sample fixtures. Tagged with `@pytest.mark.unit`.
- **Integration tests** (`tests/integration/`) — run against real downloaded filings in `output/sec_filings/`. Dry-run only; no LLM calls. Tagged with `@pytest.mark.integration`.
- Integration tests require at least one date of filings to be downloaded first (`./venv/bin/python main.py --download-only`).
- If filing data isn't on disk, integration-test fixtures call `pytest.skip()` so the suite exits cleanly instead of crashing.

### Test markers
Pytest is configured in `pyproject.toml` (`[tool.pytest.ini_options]`) to run only unit tests by default:
```
./venv/bin/python -m pytest tests/                # unit only (default -m unit)
./venv/bin/python -m pytest tests/ -m unit        # explicit unit
./venv/bin/python -m pytest tests/ -m integration # integration only
```

## Linting
Ruff is configured in `pyproject.toml` (line length 100, target Python 3.11+). Run it before committing:
```
./venv/bin/python -m ruff check src/ tests/ main.py
./venv/bin/python -m ruff check --fix src/ tests/ main.py   # auto-fix
```

## CI Pipeline
GitHub Actions (`.github/workflows/ci.yml`) runs on every push and PR to `main`/`master`:
- **Lint** — ruff check across the codebase
- **Unit tests** — matrix across Python 3.11 and 3.12
- **Integration tests** — attempts to download SEC filings for the test date, then runs integration tests. The SEC EDGAR API may block CI IP ranges (403); the download step is marked `continue-on-error: true`, and integration tests will skip gracefully when no filing data is on disk.

## LLM Configuration
- **Provider**: DeepSeek (via the OpenAI-compatible SDK).
- **API key**: Stored in `.env` as `DEEPSEEK_API_KEY`. Load with `python-dotenv`.
- The `.env` file is **never** committed to version control.

## SEC EDGAR Reference
- Full-Text Search API: `https://efts.sec.gov/LATEST/search-index`
- Archives base URL: `https://www.sec.gov/Archives/edgar/data`
- Form types documentation: https://www.sec.gov/forms
