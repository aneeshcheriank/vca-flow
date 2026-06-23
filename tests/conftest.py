"""
Shared test fixtures for the corporate-action extraction pipeline.

Provides:
  - Paths to real SEC filing data for integration tests.
  - Sample SGML/HTML content for unit tests.
  - Mock Pydantic extraction and validation objects.
"""

import sys
from pathlib import Path

import pytest

# Ensure the project root is on sys.path for `from src.…` imports.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def project_root() -> Path:
    return _PROJECT_ROOT


@pytest.fixture(scope="session")
def sec_filings_dir(project_root: Path) -> Path:
    return project_root / "output" / "sec_filings"


@pytest.fixture(scope="session")
def date_dir(sec_filings_dir: Path) -> Path:
    return sec_filings_dir / "2026-06-22"


# ---------------------------------------------------------------------------
# Real filing paths (integration tests)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sc_14d9a_filing(date_dir: Path) -> Path:
    """Small SC 14D9/A filing — 17 KB, ideal for fast integration tests."""
    return date_dir / "SC 14D9_A" / "0001140361-26-025974.txt"


@pytest.fixture(scope="session")
def sc_toi_filing(date_dir: Path) -> Path:
    """Medium SC TO-I filing — ~338 KB."""
    return date_dir / "SC TO-I" / "0001398344-26-011079.txt"


@pytest.fixture(scope="session")
def sc_toi_large_filing(date_dir: Path) -> Path:
    """Large SC TO-I filing — ~22 MB (includes all exhibits)."""
    return date_dir / "SC TO-I" / "0001702510-26-000064.txt"


# ---------------------------------------------------------------------------
# Sample text fixtures (unit tests — no disk I/O)
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_sec_header() -> str:
    return """<SEC-HEADER>0001702510-26-000064.hdr.sgml : 20260622
<ACCEPTANCE-DATETIME>20260622160048
ACCESSION NUMBER:		0001702510-26-000064
CONFORMED SUBMISSION TYPE:	SC TO-I
PUBLIC DOCUMENT COUNT:		32
FILED AS OF DATE:		20260622
DATE AS OF CHANGE:		20260622

SUBJECT COMPANY:

	COMPANY DATA:
		COMPANY CONFORMED NAME:			Carlyle Credit Solutions, Inc.
		CENTRAL INDEX KEY:			0001702510
		STATE OF INCORPORATION:			MD
		FISCAL YEAR END:			1231

	FILING VALUES:
		FORM TYPE:		SC TO-I
		SEC ACT:		1934 Act
		SEC FILE NUMBER:	005-90234

	BUSINESS ADDRESS:
		STREET 1:		ONE VANDERBILT AVENUE
		CITY:			NEW YORK
		STATE:			NY
		ZIP:			10017

FILED BY:

	COMPANY DATA:
		COMPANY CONFORMED NAME:			Carlyle Credit Solutions, Inc.
		CENTRAL INDEX KEY:			0001702510
</SEC-HEADER>"""


@pytest.fixture
def sample_document_block() -> str:
    """A minimal single-document SEC submission."""
    return """<SEC-DOCUMENT>0001702510-26-000064.txt : 20260622
<SEC-HEADER>0001702510-26-000064.hdr.sgml : 20260622
ACCESSION NUMBER:		0001702510-26-000064
CONFORMED SUBMISSION TYPE:	SC TO-I
</SEC-HEADER>
<DOCUMENT>
<TYPE>SC TO-I
<SEQUENCE>1
<FILENAME>filing.htm
<DESCRIPTION>SC TO-I
<TEXT>
<html><body>
<h1>Tender Offer</h1>
<p>The purchase price is $25.00 per share.</p>
<p>The offer expires on July 20, 2026.</p>
</body></html>
</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""


@pytest.fixture
def sample_html() -> str:
    """Inline HTML with CSS, as found in SEC filings."""
    return """<html><head><title>Test</title>
<style>.bold { font-weight: bold; }</style></head>
<body>
<div style="font-family:'Times New Roman',serif;font-size:10pt;">
<div style="font-weight:700;">UNITED STATES<br>SECURITIES AND EXCHANGE COMMISSION</div>
<p>The purchase price is <b>$25.00</b> per share.</p>
<p>The offer expires on July 20, 2026.</p>
<script>console.log('ignore me')</script>
</div>
</body></html>"""


# ---------------------------------------------------------------------------
# Schema fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def valid_extraction_dict() -> dict:
    return {
        "company_name": "Test Corp",
        "form_type": "SC TO-I",
        "offer_terms": "Issuer tender offer to purchase up to 1,000,000 shares.",
        "price": "$25.00 per share",
        "expiration_date": "2026-07-20",
        "filing_adsh": "0001702510-26-000064",
    }


@pytest.fixture
def valid_validation_dict() -> dict:
    return {
        "adsh": "0001702510-26-000064",
        "confidence": "High",
        "discrepancies": [],
        "business_rule_failures": [],
        "notes": "All checks passed.",
    }
