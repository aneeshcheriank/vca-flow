"""
I/O tools for reading, parsing, and preparing SEC EDGAR filing dumps.

The downloaded .txt files are multi-part SGML+HTML hybrids:
  - <SEC-HEADER> block with structured key-value metadata
  - One or more <DOCUMENT> sections, each containing <TYPE>, <SEQUENCE>,
    <FILENAME>, <DESCRIPTION>, and a <TEXT> block with inline HTML.
"""

import re
from pathlib import Path
from typing import Optional

from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# SEC header parsing
# ---------------------------------------------------------------------------

def parse_sec_header(text: str) -> dict[str, str]:
    """Extract key-value pairs from the <SEC-HEADER> block of a filing.

    The SEC header uses indentation-based hierarchy (SGML-like).  This parser
    extracts the top-level fields and nested values into a flat dict using
    normalised keys.

    Returns a dict with keys like 'accession_number', 'conformed_submission_type',
    'company_conformed_name', 'central_index_key', 'filed_as_of_date', etc.
    """
    header_match = re.search(r"<SEC-HEADER>(.*?)</SEC-HEADER>", text, re.DOTALL)
    if not header_match:
        return {}

    header = header_match.group(1)
    result: dict[str, str] = {}

    # Match every line of the form  KEY_AND_INDENT:  VALUE
    # Keys may appear at any indentation level (tabs are common).
    for match in re.finditer(
        r"^\s*([A-Z][A-Za-z0-9 /&()_-]+?):\s+(.+)$", header, re.MULTILINE
    ):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if not value:
            continue

        # Normalise key: lowercase, underscores for spaces
        norm_key = key.lower().replace(" ", "_").replace("/", "_").replace("-", "_")
        # Collapse multiple underscores
        norm_key = re.sub(r"_+", "_", norm_key)

        # Don't overwrite an existing value (first occurrence wins —
        # typically the SUBJECT COMPANY entry, not the FILED BY duplicate)
        if norm_key not in result:
            result[norm_key] = value

    return result


# ---------------------------------------------------------------------------
# HTML → text
# ---------------------------------------------------------------------------

def extract_text_from_html(html: str) -> str:
    """Strip HTML tags, CSS, and scripts; return readable plain text.

    Uses BeautifulSoup with lxml for fast, tolerant parsing.  Removes
    <script> and <style> blocks, then extracts visible text with
    reasonable whitespace normalization.
    """
    soup = BeautifulSoup(html, "lxml")

    # Remove non-content elements
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()

    # Extract text — separator=" " avoids words from adjacent blocks
    # being concatenated without a space
    text = soup.get_text(separator=" ", strip=True)

    # Normalise whitespace: collapse multiple spaces / blank lines
    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text


# ---------------------------------------------------------------------------
# Multi-document filing handling
# ---------------------------------------------------------------------------

def _extract_tag(text: str, tag: str) -> str:
    """Extract content associated with an SGML-style tag.

    SEC filings use two conventions:
      1. Paired:   <TYPE>SC TO-I</TYPE>
      2. Unpaired:  <TYPE>SC TO-I
                    <SEQUENCE>1        (next tag closes implicitly)

    We first try to find </TAG>.  If absent, we take content up to the
    next opening tag or newline — whichever comes first.
    """
    start_tag = f"<{tag}>"
    start = text.find(start_tag)
    if start == -1:
        return ""
    start += len(start_tag)

    # Try paired form first
    end_tag = f"</{tag}>"
    end = text.find(end_tag, start)
    if end != -1:
        return text[start:end].strip()

    # Unpaired form — content runs to the next opening tag or a newline
    remaining = text[start:]
    # Find the next "<" that isn't part of the current content
    next_tag_pos = remaining.find("\n<")
    if next_tag_pos == -1:
        # No more tags — take everything
        return remaining.strip()
    else:
        return remaining[:next_tag_pos].strip()


def extract_document_sections(full_text: str) -> list[dict[str, str]]:
    """Split a multi-document SEC submission into individual document records.

    Each record is a dict with keys: 'type', 'sequence', 'filename',
    'description', 'text' (the raw content inside <TEXT>...</TEXT>).

    Uses simple string scanning rather than a monolithic regex, which
    avoids issues with HTML content inside <TEXT> that can confuse
    regex engines (e.g. stray angle brackets, inline XBRL).
    """
    documents: list[dict[str, str]] = []

    # Split on <DOCUMENT> boundaries.  The first chunk before any
    # <DOCUMENT> is the SEC-HEADER (discarded).
    chunks = full_text.split("<DOCUMENT>")

    for chunk in chunks[1:]:  # skip content before the first <DOCUMENT>
        # Each chunk runs from after <DOCUMENT> to before the next <DOCUMENT>
        # or end of file.  Trim trailing </SEC-DOCUMENT> if present.
        end = chunk.find("</DOCUMENT>")
        if end != -1:
            chunk = chunk[:end]

        doc = {
            "type": _extract_tag(chunk, "TYPE"),
            "sequence": _extract_tag(chunk, "SEQUENCE"),
            "filename": _extract_tag(chunk, "FILENAME"),
            "description": _extract_tag(chunk, "DESCRIPTION"),
            "text": _extract_tag(chunk, "TEXT"),
        }
        documents.append(doc)

    return documents


# ---------------------------------------------------------------------------
# Filing loading
# ---------------------------------------------------------------------------

def load_filing_text(filing_path: Path) -> str:
    """Read the full content of a .txt SEC filing."""
    return filing_path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Filing → LLM-ready text
# ---------------------------------------------------------------------------

# Form types we actually process (including amendments, which the SEC API
# maps under the base form's root_forms field).
TARGET_FORM_PREFIXES = ("SC TO-I", "SC 13E3", "SC 14D9")

# Documents whose TYPE starts with these prefixes are exhibits and should be
# skipped when selecting the primary filing body.
EXHIBIT_PREFIXES = ("EX-", "EX_")


def _is_primary_document(doc: dict[str, str]) -> bool:
    """Return True if *doc* looks like the primary filing body.

    The primary document is one whose TYPE starts with a target form prefix
    and is NOT an exhibit.
    """
    dtype = doc["type"].upper()
    for prefix in TARGET_FORM_PREFIXES:
        if dtype.startswith(prefix.upper()) and not dtype.startswith(EXHIBIT_PREFIXES):
            return True
    return False


def prepare_filing_for_llm(
    filing_path: Path,
    max_chars: int = 30_000,
) -> str:
    """Load a filing and prepare it for the LLM context window.

    Strategy:
      1. Parse the multi-document submission.
      2. Select the **primary** document (the one whose TYPE matches a target
         form and is not an exhibit).  This avoids feeding 22 MB of exhibits
         into the LLM.
      3. Fall back to the first document if no primary match is found.
      4. Strip HTML → plain text.
      5. Truncate to *max_chars*.

    Returns the prepared text.
    """
    raw = load_filing_text(filing_path)
    documents = extract_document_sections(raw)

    # --- Select the primary document ---
    primary: Optional[dict[str, str]] = None

    for doc in documents:
        if _is_primary_document(doc):
            primary = doc
            break

    if primary is None and documents:
        # Fall back to first non-exhibit document
        for doc in documents:
            if not doc["type"].upper().startswith(EXHIBIT_PREFIXES):
                primary = doc
                break

    if primary is None and documents:
        primary = documents[0]

    if primary is None:
        # No documents at all — return the raw text (or a snippet)
        text = raw
    else:
        text = primary["text"]

    # --- Strip HTML ---
    if "<html" in text.lower() or "<body" in text.lower():
        text = extract_text_from_html(text)

    # --- Truncate ---
    if len(text) > max_chars:
        # Keep the beginning and end — key terms are often near the top
        # (Item 1–4), but expiration / signature details can be at the end.
        head_size = int(max_chars * 0.85)
        tail_size = max_chars - head_size
        text = text[:head_size] + "\n\n[... truncated ...]\n\n" + text[-tail_size:]

    return text


# ---------------------------------------------------------------------------
# File-system helpers
# ---------------------------------------------------------------------------

def list_filings_for_date(date_dir: Path) -> list[Path]:
    """Return all .txt filing paths under a date directory.

    Only returns filings whose form-type subdirectory matches one of the
    target form prefixes (including amendments like 'SC TO-I_A').
    """
    filings: list[Path] = []
    if not date_dir.is_dir():
        return filings

    for form_dir in sorted(date_dir.iterdir()):
        if not form_dir.is_dir():
            continue
        # Check form type matches a target prefix
        form_name = form_dir.name
        if not any(
            form_name.upper().startswith(p.upper()) for p in TARGET_FORM_PREFIXES
        ):
            continue
        # Collect .txt files (not -index.htm, not -meta.json)
        for txt_file in sorted(form_dir.glob("*.txt")):
            filings.append(txt_file)

    return filings


def is_target_form_type(form_type: str) -> bool:
    """Check whether *form_type* matches one of our target forms.

    Matches base forms and amendments alike:
      'SC TO-I'   → True
      'SC TO-I/A' → True
      '8-K'       → False
    """
    upper = form_type.upper().replace("/", "_").replace("-", "_")
    for prefix in TARGET_FORM_PREFIXES:
        if upper.startswith(prefix.upper().replace("/", "_").replace("-", "_")):
            return True
    return False
