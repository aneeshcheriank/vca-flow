"""
Hard-coded business rules for validating corporate-action extractions.

These rules run **before** the LLM-based validation step.  They perform
deterministic checks that are cheaper and more reliable than an LLM call.

Each rule function returns a list of Discrepancy objects (empty list = pass).
"""

import re
from datetime import date, datetime

from src.schema import Discrepancy


# ---------------------------------------------------------------------------
# Date parsing helpers
# ---------------------------------------------------------------------------

# Common date formats found in SEC filings
_DATE_FORMATS = [
    "%Y-%m-%d",
    "%B %d, %Y",
    "%b %d, %Y",
    "%B %d %Y",
    "%b %d %Y",
    "%m/%d/%Y",
    "%m/%d/%y",
]

# Patterns that indicate a relative / formulaic date rather than a fixed one
_RELATIVE_DATE_PATTERNS = [
    r"business days? after",
    r"business days? from",
    r"calendar days? after",
    r"calendar days? from",
    r"days? after",
    r"days? from",
    r"trading days? after",
    r"business days? following",
    r"within \d+ days?",
]


def _try_parse_date(date_str: str) -> date | None:
    """Attempt to parse *date_str* into a date object.  Returns None on failure."""
    clean = date_str.strip().rstrip(".")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def _is_relative_date(date_str: str) -> bool:
    """Return True if *date_str* is a relative/conditional date expression."""
    lower = date_str.lower()
    return any(re.search(pat, lower) for pat in _RELATIVE_DATE_PATTERNS)


# ---------------------------------------------------------------------------
# Rule 1 — Expiration date validity
# ---------------------------------------------------------------------------

def rule_expiration_date_valid(
    expiration_str: str,
    filing_date_str: str,
) -> list[Discrepancy]:
    """Check whether the expiration date is valid relative to the filing date.

    Flags:
      - Expiration date that is clearly in the past (relative to filing date).
      - Expiration date that has already passed (relative to today).
      - Unparseable date string (unless it is a valid relative-date expression).
    """
    discrepancies: list[Discrepancy] = []

    # Relative dates are acceptable — we can't validate them deterministically
    if _is_relative_date(expiration_str):
        return discrepancies

    exp_date = _try_parse_date(expiration_str)
    if exp_date is None:
        # Could not parse — flag as a warning
        discrepancies.append(Discrepancy(
            rule="Rule 1",
            severity="warning",
            description=(
                f"Could not parse expiration date '{expiration_str}' as a "
                f"fixed date.  If this is intentional (relative date), "
                f"consider using a standard format."
            ),
        ))
        return discrepancies

    # Check against filing date
    filing_date = _try_parse_date(filing_date_str)
    if filing_date is not None and exp_date < filing_date:
        discrepancies.append(Discrepancy(
            rule="Rule 1",
            severity="error",
            description=(
                f"Expiration date {exp_date} is before the filing date "
                f"{filing_date}.  This is likely an extraction error."
            ),
        ))

    # Check against today
    today = date.today()
    if exp_date < today:
        discrepancies.append(Discrepancy(
            rule="Rule 1",
            severity="warning",
            description=(
                f"Expiration date {exp_date} has already passed (today is "
                f"{today}).  The offer may have expired."
            ),
        ))

    return discrepancies


# ---------------------------------------------------------------------------
# Rule 2 — Valid option / offer types
# ---------------------------------------------------------------------------

# Keywords that should appear in valid offer-term descriptions per form type
_FORM_TYPE_KEYWORDS: dict[str, list[str]] = {
    "SC TO-I": [
        "tender offer", "purchase", "issuer tender", "third-party tender",
        "offer to purchase", "shares", "stock", "common stock",
    ],
    "SC TO-I_A": [
        "tender offer", "amendment", "purchase", "shares",
    ],
    "SC 13E3": [
        "going private", "going-private", "rule 13e-3", "13e-3",
        "acquisition", "merger", "squeeze-out", "cash-out",
    ],
    "SC 14D9": [
        "solicitation", "recommendation", "tender offer", "board",
        "recommends", "against", "in favour", "in favor",
    ],
    "SC 14D9_A": [
        "solicitation", "recommendation", "amendment", "board",
    ],
}


def rule_option_types_valid(
    offer_terms: str,
    form_type: str,
) -> list[Discrepancy]:
    """Check that the extracted offer terms match the expected form type.

    For each form type we expect certain keywords.  Absence of all expected
    keywords suggests the extraction may have captured the wrong content or
    the filing was misclassified.
    """
    discrepancies: list[Discrepancy] = []

    # Normalise form type for lookup (handle /A amendments).
    # Only replace slashes — dashes are part of the canonical form names.
    base = form_type.upper().replace("/", "_")
    if base.endswith("_A"):
        base_form = base[:-2]
    else:
        base_form = base

    expected = _FORM_TYPE_KEYWORDS.get(base) or _FORM_TYPE_KEYWORDS.get(base_form)

    if expected is None:
        # Unknown form type — can't validate this deterministically
        return discrepancies

    terms_lower = offer_terms.lower()
    matches = [kw for kw in expected if kw.lower() in terms_lower]

    if not matches:
        discrepancies.append(Discrepancy(
            rule="Rule 2",
            severity="warning",
            description=(
                f"Extracted offer terms for {form_type} do not contain any "
                f"expected keywords ({', '.join(expected[:3])}...).  The "
                f"extraction may have misidentified the transaction type."
            ),
        ))

    return discrepancies


# ---------------------------------------------------------------------------
# Rule 3 — Text template / extraction sanity
# ---------------------------------------------------------------------------

# Patterns that suggest hallucination
_HALLUCINATION_MARKERS = [
    r"(\b\w+\b)\s+\1\s+\1",          # same word repeated 3×
    r"\[insert .+?\]",                # template placeholder
    r"\[TODO[:\]]",                   # TODO marker
    r"As an AI",                       # model self-reference
    r"I cannot",                       # model refusal
    r"I am unable",                    # model refusal
    r"not stated in the (provided|given) text",  # potential lazy shortcut
]


def rule_text_template_sensible(
    extraction_text: str,
    source_text: str | None = None,
) -> list[Discrepancy]:
    """Sanity-check the extracted text for hallucination markers and completeness.

    Checks:
      - No hallucination / refusal markers in the extraction.
      - No obviously placeholder values.
      - (If source text is provided) key numeric values from the extraction
        roughly appear in the source.
    """
    discrepancies: list[Discrepancy] = []

    # --- Hallucination markers ---
    for pattern in _HALLUCINATION_MARKERS:
        if re.search(pattern, extraction_text, re.IGNORECASE):
            discrepancies.append(Discrepancy(
                rule="Rule 3",
                severity="error",
                description=(
                    f"Extraction contains text matching hallucination / refusal "
                    f"pattern: '{pattern}'.  The LLM may not have processed the "
                    f"filing correctly."
                ),
            ))
            break  # one is enough

    # --- Empty / placeholder values ---
    if not extraction_text or extraction_text.strip() in ("", "N/A", "None", "null"):
        discrepancies.append(Discrepancy(
            rule="Rule 3",
            severity="error",
            description="Extraction text is empty or a placeholder value.",
        ))

    # --- Number cross-check (lightweight) ---
    if source_text:
        # Find dollar amounts in the extraction (e.g. $12.50, $1,234,567)
        ext_numbers = set(re.findall(r"\$[\d,]+\.?\d*", extraction_text))
        for num in ext_numbers:
            if num not in source_text:
                discrepancies.append(Discrepancy(
                    rule="Rule 3",
                    severity="warning",
                    description=(
                        f"Extracted value '{num}' does not appear verbatim in "
                        f"the source text.  It may be a hallucination or a "
                        f"derived / rounded value."
                    ),
                ))

    return discrepancies


# ---------------------------------------------------------------------------
# Convenience — run all business rules
# ---------------------------------------------------------------------------

def run_all_business_rules(
    extraction_text: str,
    expiration_str: str,
    form_type: str,
    filing_date_str: str,
    source_text: str | None = None,
) -> tuple[list[Discrepancy], list[str]]:
    """Run all three business rules and return combined results.

    Returns:
      (all_discrepancies, failed_rule_names)
    """
    all_discrepancies: list[Discrepancy] = []
    failures: list[str] = []

    # Rule 1
    r1 = rule_expiration_date_valid(expiration_str, filing_date_str)
    if r1:
        all_discrepancies.extend(r1)
        failures.append("rule_expiration_date_valid")

    # Rule 2
    r2 = rule_option_types_valid(extraction_text, form_type)
    if r2:
        all_discrepancies.extend(r2)
        failures.append("rule_option_types_valid")

    # Rule 3
    r3 = rule_text_template_sensible(extraction_text, source_text)
    if r3:
        all_discrepancies.extend(r3)
        failures.append("rule_text_template_sensible")

    return all_discrepancies, failures
