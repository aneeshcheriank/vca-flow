"""
Pydantic schemas for the corporate action extraction pipeline.

Defines the structured output types used by the Extraction Agent
and the Validation & Verification Agent.
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Confidence score
# ---------------------------------------------------------------------------

class ConfidenceScore(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


# ---------------------------------------------------------------------------
# Extraction output
# ---------------------------------------------------------------------------

class CorporateActionExtraction(BaseModel):
    """Structured output from the Extraction Agent.

    Each field maps to the key corporate-action details we need to surface
    from a tender-offer, going-private, or solicitation/recommendation filing.
    """

    company_name: str = Field(
        description="Name of the subject company (the issuer whose securities "
                    "are the subject of the corporate action)."
    )
    form_type: str = Field(
        description="SEC form type of the filing that was processed "
                    "(e.g. 'SC TO-I', 'SC 13E3', 'SC 14D9')."
    )
    offer_terms: str = Field(
        description="Concise summary of the material terms of the offer: "
                    "type of transaction (issuer tender, third-party tender, "
                    "going-private), number of shares / amount sought, any "
                    "conditions, and key mechanics (e.g. proration, odd-lot "
                    "provisions). 2-5 sentences."
    )
    price: str = Field(
        description="Price per share or pricing formula.  For fixed-price "
                    "offers give the dollar amount.  For formula-based offers "
                    "(e.g. 'net asset value as of <date>') describe the formula "
                    "and reference date."
    )
    expiration_date: str = Field(
        description="Expiration date of the offer.  Prefer ISO format "
                    "(YYYY-MM-DD) when an exact date is stated.  If the filing "
                    "only gives a relative date (e.g. '20 business days after "
                    "commencement'), preserve that description."
    )
    filing_adsh: str = Field(
        description="SEC accession number (ADSH) of the source filing, "
                    "e.g. '0001702510-26-000064'."
    )


# ---------------------------------------------------------------------------
# Validation output
# ---------------------------------------------------------------------------

class Discrepancy(BaseModel):
    """A specific issue found during validation."""

    rule: str = Field(
        description="Which check flagged this discrepancy "
                    "(e.g. 'Rule 1', 'Rule 2', 'Rule 3', 'LLM')."
    )
    severity: str = Field(
        description="Severity level: 'error' (likely wrong), "
                    "'warning' (suspicious), or 'info' (noteworthy)."
    )
    description: str = Field(
        description="Human-readable explanation of the discrepancy."
    )


class ValidationResult(BaseModel):
    """Structured output from the Validation & Verification Agent.

    Produced by running hard-coded business rules followed by LLM-based
    verification against the original filing text.
    """

    adsh: str = Field(
        description="Accession number of the filing being validated."
    )
    confidence: ConfidenceScore = Field(
        description="Overall confidence in the extraction quality after "
                    "business-rule checks and LLM review."
    )
    discrepancies: list[Discrepancy] = Field(
        default_factory=list,
        description="Specific issues found during validation."
    )
    business_rule_failures: list[str] = Field(
        default_factory=list,
        description="Names of hard-coded business rules that flagged issues "
                    "(e.g. ['rule_expiration_date_valid'])."
    )
    notes: str = Field(
        default="",
        description="Additional free-text context from the validator, e.g. "
                    "why confidence was downgraded or what looks correct."
    )
