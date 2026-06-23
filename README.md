[![CI](https://github.com/aneeshcheriank/vca-flow/actions/workflows/ci.yml/badge.svg)](https://github.com/aneeshcheriank/vca-flow/actions/workflows/ci.yml)

# vca-flow — Voluntary Corporate Action Flow

Automated pipeline that downloads voluntary corporate action filings from the SEC EDGAR system, then extracts and validates key details (offer terms, price, expiration date) using a LangChain + LangGraph pipeline powered by DeepSeek.

## What It Does

1. **Download** — Fetches SC TO-I, SC 13E3, and SC 14D9 filings from the SEC EDGAR Full-Text Search API.
2. **Extract** — An LLM agent reads each filing and extracts structured corporate action details (company, price, terms, expiration).
3. **Validate** — Hard-coded business rules + a second LLM pass audit the extraction, assign a confidence score, and flag discrepancies.
4. **Output** — Results are written as JSON to `output/corporate_actions/YYYY-MM-DD/corporate_action.json`.

### Target Form Types

| Form | Description |
|---|---|
| **SC TO-I** | Tender Offer Statement (Issuer) |
| **SC 13E3** | Going Private Transaction |
| **SC 14D9** | Tender Offer Solicitation / Recommendation |

These three forms are the primary carriers of voluntary corporate action information in SEC EDGAR filings.

## Project Structure

```
.
├── main.py                  # End-to-end pipeline entry point
├── README.md
├── requirements.txt
├── pyproject.toml            # Ruff linter + pytest config
├── .github/
│   └── workflows/
│       └── ci.yml            # GitHub Actions CI pipeline
├── .env                      # DEEPSEEK_API_KEY (not committed)
├── src/
│   ├── download_sec_voluntary_filings.py  # SEC filing downloader
│   ├── schema.py                          # Pydantic data models
│   ├── tools.py                           # Filing I/O + HTML parsing
│   ├── prompts.py                         # LLM prompt templates
│   ├── business_rules.py                  # Hard-coded validation rules
│   ├── agents.py                          # Extraction + Validation agents
│   └── chain.py                           # LangGraph orchestrator
├── tests/
│   ├── conftest.py                        # Shared test fixtures
│   ├── unit/                              # Fast tests (no network/disk)
│   └── integration/                       # Tests against real filings
└── output/
    ├── sec_filings/                       # Raw downloaded filings
    └── corporate_actions/                 # Final extraction results
```

## Setup

### Prerequisites

- Python 3.12+
- A [DeepSeek API key](https://platform.deepseek.com/)

### Installation

```bash
# Clone the repository
git clone <repo-url>
cd <project-directory>

# Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
./venv/bin/pip install -r requirements.txt
```

### Configuration

Create a `.env` file in the project root with your DeepSeek API key:

```bash
echo 'DEEPSEEK_API_KEY=sk-your-key-here' > .env
```

Edit the `USER_AGENT` variable in `src/download_sec_voluntary_filings.py` to identify your organization to the SEC (required for fair access).

## Usage

### Quick Start

Run the full pipeline for today's filings:

```bash
./venv/bin/python main.py
```

### End-to-End Pipeline (main.py)

```bash
./venv/bin/python main.py                              # today (download + process)
./venv/bin/python main.py --date 2026-06-22             # specific date
./venv/bin/python main.py --dry-run                     # preview filings only
./venv/bin/python main.py --download-only               # only download, skip LLM
./venv/bin/python main.py --process-only                # only process existing data
./venv/bin/python main.py --max-filings 5               # limit for testing
./venv/bin/python main.py --cron                        # quiet mode
```

### Individual Steps

**Download only:**
```bash
./venv/bin/python src/download_sec_voluntary_filings.py                    # today
./venv/bin/python src/download_sec_voluntary_filings.py --date 2026-06-22  # specific date
./venv/bin/python src/download_sec_voluntary_filings.py --days 5            # date range
./venv/bin/python src/download_sec_voluntary_filings.py --dry-run           # preview
```

**Process only (extraction + validation):**
```bash
./venv/bin/python src/chain.py --date 2026-06-22             # all filings for date
./venv/bin/python src/chain.py --adsh 0001702510-26-000064  # single filing
./venv/bin/python src/chain.py --dry-run                     # preview
```

## Output

Results are written to `output/corporate_actions/YYYY-MM-DD/corporate_action.json`:

```json
[
  {
    "adsh": "0001702510-26-000064",
    "company": "Carlyle Credit Solutions, Inc.",
    "form_type": "SC TO-I",
    "file_date": "2026-06-22",
    "extraction": {
      "company_name": "Carlyle Credit Solutions, Inc.",
      "form_type": "SC TO-I",
      "offer_terms": "Issuer tender offer to purchase up to 4,878,153 shares...",
      "price": "Net asset value as of June 30, 2026",
      "expiration_date": "2026-07-20",
      "filing_adsh": "0001702510-26-000064"
    },
    "validation": {
      "adsh": "0001702510-26-000064",
      "confidence": "High",
      "discrepancies": [],
      "business_rule_failures": [],
      "notes": "All checks passed."
    }
  }
]
```

### Confidence Scores

| Score | Meaning |
|---|---|
| **High** | All extracted details are correct and well-supported by the filing text. |
| **Medium** | Minor issues or ambiguities exist, but core facts appear correct. |
| **Low** | A major detail is wrong, missing, or unsupported — needs human review. |

## Validation Rules

The pipeline applies three deterministic business rules before LLM-based verification:

1. **Expiration Date Validity** — Is the date parseable, not in the past, and not before the filing date?
2. **Option Type Validity** — Do the extracted terms contain keywords expected for the form type?
3. **Text Template Sanity** — Are there hallucination markers, placeholder values, or numbers not found in the source?

Business-rule errors can downgrade the LLM-assigned confidence score but never upgrade it.

## Testing

Tests use pytest markers to separate fast unit tests from integration tests. By default, only unit tests run:

```bash
# Unit tests only (default — fast, no disk I/O)
./venv/bin/python -m pytest tests/ -v

# All tests
./venv/bin/python -m pytest tests/ -v -m "unit or integration"

# Integration tests (requires downloaded filings)
./venv/bin/python -m pytest tests/ -v -m integration
```

Integration tests use real filings from `output/sec_filings/` in dry-run mode (no LLM calls). Run `./venv/bin/python main.py --download-only` first to download the test data. If filing data isn't available, integration tests skip gracefully.

## Linting & Formatting

Ruff is configured in `pyproject.toml` (line length 100, Python 3.11+):

```bash
./venv/bin/python -m ruff check src/ tests/ main.py
./venv/bin/python -m ruff check --fix src/ tests/ main.py   # auto-fix
```

## CI Pipeline

GitHub Actions runs on every push and PR:
- **Lint** — ruff check
- **Unit tests** — Python 3.11 + 3.12
- **Integration tests** — downloads SEC filings, runs integration suite. The SEC EDGAR API may block CI IP ranges (403); if so, tests skip gracefully.

## SEC Fair Access

This project queries the SEC EDGAR system. Per SEC requirements:
- Identify your organisation by setting the `USER_AGENT` in the downloader script.
- Rate limit: ≤ 10 requests/second (the downloader uses 0.12 s delay, ~8.3 req/s).
- Results are capped at 10,000 hits per query — narrow your date range if needed.

## Dependencies

- **LangChain** + **LangGraph** — Agent framework and graph orchestration
- **OpenAI SDK** — DeepSeek API access (OpenAI-compatible endpoint)
- **Pydantic** — Structured output schemas
- **BeautifulSoup4** + **lxml** — HTML parsing
- **Requests** — SEC EDGAR HTTP client
- **pytest** — Test framework (with unit/integration markers)
- **ruff** — Fast Python linter and formatter
