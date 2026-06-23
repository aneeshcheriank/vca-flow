"""
LangChain agents for the corporate-action extraction pipeline.

Provides:
  - create_llm()          — DeepSeek LLM factory (OpenAI-compatible endpoint)
  - ExtractionAgent       — extracts corporate-action details from filing text
  - ValidationAgent       — validates extraction against source text
"""

import json
import os
from typing import Optional

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

from src.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
    VALIDATION_SYSTEM_PROMPT,
    VALIDATION_USER_PROMPT,
)
from src.schema import (
    ConfidenceScore,
    CorporateActionExtraction,
    Discrepancy,
    ValidationResult,
)
from src.business_rules import run_all_business_rules

# Load .env on module import
load_dotenv()


# ---------------------------------------------------------------------------
# LLM factory
# ---------------------------------------------------------------------------

def create_llm(
    temperature: float = 0.0,
    max_tokens: int = 2048,
) -> ChatOpenAI:
    """Create a DeepSeek ChatOpenAI instance.

    Requires DEEPSEEK_API_KEY in the environment (or .env file).
    Uses the OpenAI-compatible DeepSeek API endpoint.
    """
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError(
            "DEEPSEEK_API_KEY is not set.  Add it to your .env file or export "
            "it in your shell."
        )

    return ChatOpenAI(
        model="deepseek-chat",
        temperature=temperature,
        max_tokens=max_tokens,
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


# ---------------------------------------------------------------------------
# Extraction Agent
# ---------------------------------------------------------------------------

class ExtractionAgent:
    """Extracts corporate-action details from SEC filing text.

    Uses LangChain's with_structured_output to force the LLM response into
    a CorporateActionExtraction Pydantic model.
    """

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm
        # LangChain's with_structured_output uses function-calling under the
        # hood with DeepSeek's OpenAI-compatible API.
        self.structured_llm = llm.with_structured_output(
            CorporateActionExtraction,
            method="function_calling",
        )

    def extract(
        self,
        filing_text: str,
        company: str,
        form_type: str,
        file_date: str,
        adsh: str,
    ) -> CorporateActionExtraction | None:
        """Run extraction on a single filing.

        Returns a CorporateActionExtraction on success, or None if the LLM
        call fails (network error, schema rejection, etc.).
        """
        user_prompt = EXTRACTION_USER_PROMPT.format(
            company_name=company,
            form_type=form_type,
            file_date=file_date,
            adsh=adsh,
            text=filing_text,
        )

        try:
            result: CorporateActionExtraction = self.structured_llm.invoke([
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
            return result
        except Exception as exc:
            # The LLM call can fail for many reasons: network, rate-limiting,
            # schema rejection, etc.  Return None so the caller can handle it.
            print(f"  [ERROR] Extraction failed for {adsh}: {exc}")
            return None


# ---------------------------------------------------------------------------
# Validation Agent
# ---------------------------------------------------------------------------

class ValidationAgent:
    """Validates extracted corporate-action data against the source filing.

    Runs hard-coded business rules first, then an LLM-based verification
    pass.  The results are merged: business-rule failures can downgrade the
    LLM-assigned confidence score but never upgrade it.
    """

    def __init__(self, llm: ChatOpenAI) -> None:
        self.llm = llm
        self.structured_llm = llm.with_structured_output(
            ValidationResult,
            method="function_calling",
        )

    def validate(
        self,
        extraction: CorporateActionExtraction,
        source_text: str,
    ) -> ValidationResult:
        """Validate an extraction against source text.

        Steps:
          1. Run hard-coded business rules.
          2. Run LLM-based verification.
          3. Merge business-rule discrepancies into the LLM result.
          4. Downgrade confidence if business rules flagged errors.
        """
        # --- Step 1: business rules ---
        biz_discrepancies, biz_failures = run_all_business_rules(
            extraction_text=(
                f"Terms: {extraction.offer_terms}\n"
                f"Price: {extraction.price}\n"
                f"Expiration: {extraction.expiration_date}"
            ),
            expiration_str=extraction.expiration_date,
            form_type=extraction.form_type,
            filing_date_str="",  # We don't have filing_date here — extract from metadata?
            source_text=source_text,
        )

        # --- Step 2: LLM verification ---
        extraction_json = extraction.model_dump_json(indent=2)
        user_prompt = VALIDATION_USER_PROMPT.format(
            extraction_json=extraction_json,
            text=source_text,
        )

        llm_result: Optional[ValidationResult] = None
        try:
            llm_result = self.structured_llm.invoke([
                {"role": "system", "content": VALIDATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ])
        except Exception as exc:
            print(f"  [ERROR] Validation LLM call failed for {extraction.filing_adsh}: {exc}")

        # --- Step 3: merge ---
        if llm_result is None:
            # LLM failed — build a result from business rules alone
            confidence = (
                ConfidenceScore.HIGH
                if not biz_failures
                else ConfidenceScore.LOW
            )
            return ValidationResult(
                adsh=extraction.filing_adsh,
                confidence=confidence,
                discrepancies=biz_discrepancies,
                business_rule_failures=biz_failures,
                notes="LLM validation unavailable; business rules only.",
            )

        # Merge business-rule discrepancies into the LLM result
        all_discrepancies = biz_discrepancies + llm_result.discrepancies
        all_failures = biz_failures + llm_result.business_rule_failures

        # Downgrade confidence if business rules found errors
        confidence = llm_result.confidence
        has_biz_errors = any(d.severity == "error" for d in biz_discrepancies)
        if has_biz_errors and confidence == ConfidenceScore.HIGH:
            confidence = ConfidenceScore.MEDIUM
        elif has_biz_errors and confidence == ConfidenceScore.MEDIUM:
            confidence = ConfidenceScore.LOW

        return ValidationResult(
            adsh=extraction.filing_adsh,
            confidence=confidence,
            discrepancies=all_discrepancies,
            business_rule_failures=all_failures,
            notes=llm_result.notes,
        )
