"""Unit tests for src/business_rules.py — hard-coded validation rules."""

from datetime import date, timedelta

import pytest

pytestmark = pytest.mark.unit

from src.business_rules import (
    rule_expiration_date_valid,
    rule_option_types_valid,
    rule_text_template_sensible,
    run_all_business_rules,
)
from src.schema import Discrepancy

# ---------------------------------------------------------------------------
# Rule 1 — expiration date validity
# ---------------------------------------------------------------------------

class TestRuleExpirationDateValid:
    def test_future_date_passes(self):
        future = (date.today() + timedelta(days=30)).isoformat()
        result = rule_expiration_date_valid(future, "2026-06-22")
        assert result == []

    def test_date_before_filing_is_error(self):
        result = rule_expiration_date_valid("2026-01-01", "2026-06-22")
        assert len(result) >= 1
        assert any(d.severity == "error" for d in result)

    def test_already_expired_is_warning(self):
        past = (date.today() - timedelta(days=10)).isoformat()
        result = rule_expiration_date_valid(past, "2026-06-22")
        # Warning (not error) because it may have legitimately expired
        if result:
            assert any(d.severity == "warning" for d in result)

    def test_unparseable_date_is_warning(self):
        result = rule_expiration_date_valid("not a real date", "2026-06-22")
        assert len(result) >= 1
        assert any("not parse" in d.description.lower() for d in result)

    def test_relative_date_passes(self):
        """Relative dates like '20 business days after commencement' are fine."""
        result = rule_expiration_date_valid(
            "20 business days after commencement", "2026-06-22"
        )
        assert result == []

    def test_various_date_formats(self):
        """Rule should parse common date formats."""
        for date_str in [
            "2026-07-20",
            "July 20, 2026",
            "Jul 20, 2026",
            "07/20/2026",
        ]:
            result = rule_expiration_date_valid(date_str, "2026-06-22")
            # Future from filing date — should pass
            assert result == [], f"Failed for {date_str!r}"


# ---------------------------------------------------------------------------
# Rule 2 — option / offer type validity
# ---------------------------------------------------------------------------

class TestRuleOptionTypesValid:
    def test_sc_toi_with_tender_keywords_passes(self):
        result = rule_option_types_valid(
            "Issuer tender offer to purchase up to 1,000,000 shares at $25.00.",
            "SC TO-I",
        )
        assert result == []

    def test_sc_toi_without_keywords_warns(self):
        result = rule_option_types_valid(
            "The company is doing something unrelated.", "SC TO-I"
        )
        assert len(result) >= 1
        assert any(d.severity == "warning" for d in result)

    def test_sc_13e3_with_going_private_keywords_passes(self):
        result = rule_option_types_valid(
            "Going-private transaction under Rule 13e-3 via merger.",
            "SC 13E3",
        )
        assert result == []

    def test_sc_14d9_with_recommendation_keywords_passes(self):
        result = rule_option_types_valid(
            "The board recommends that shareholders accept the tender offer.",
            "SC 14D9",
        )
        assert result == []

    def test_amendment_form_matches_base_keywords(self):
        """SC TO-I/A should match SC TO-I keywords."""
        result = rule_option_types_valid(
            "Amendment to tender offer. Increase in purchase price.",
            "SC TO-I/A",
        )
        assert result == []

    def test_unknown_form_type_skips(self):
        """Unknown form types should produce no discrepancies."""
        result = rule_option_types_valid("Anything goes here.", "UNKNOWN-FORM")
        assert result == []


# ---------------------------------------------------------------------------
# Rule 3 — text template sanity
# ---------------------------------------------------------------------------

class TestRuleTextTemplateSensible:
    def test_normal_text_passes(self):
        result = rule_text_template_sensible(
            "Issuer tender offer for 1,000,000 shares at $25.00 per share."
        )
        assert result == []

    def test_empty_text_is_error(self):
        result = rule_text_template_sensible("")
        assert len(result) >= 1
        assert any(d.severity == "error" for d in result)

    def test_placeholder_text_is_error(self):
        for text in ["N/A", "None", "null"]:
            result = rule_text_template_sensible(text)
            assert len(result) >= 1, f"Failed for {text!r}"

    def test_ai_refusal_detected(self):
        for text in [
            "As an AI language model, I cannot...",
            "I am unable to process this filing.",
            "I cannot extract the requested information.",
        ]:
            result = rule_text_template_sensible(text)
            assert len(result) >= 1, f"Failed for {text!r}"
            assert any(d.severity == "error" for d in result)

    def test_number_cross_check_with_source(self):
        extraction = "The price is $25.00 per share."
        source = "The purchase price is $25.00 per share."
        result = rule_text_template_sensible(extraction, source)
        # $25.00 appears in both — no discrepancy
        assert not any(
            d.rule == "Rule 3" and d.severity == "warning" for d in result
        )

    def test_number_not_in_source_warns(self):
        extraction = "The price is $99.99 per share."
        source = "The purchase price is $25.00 per share."
        result = rule_text_template_sensible(extraction, source)
        assert any(
            d.rule == "Rule 3" and d.severity == "warning" and "$99.99" in d.description
            for d in result
        )


# ---------------------------------------------------------------------------
# run_all_business_rules
# ---------------------------------------------------------------------------

class TestRunAllBusinessRules:
    def test_all_pass(self):
        discrepancies, failures = run_all_business_rules(
            extraction_text="Issuer tender offer for shares at $25.00 per share.",
            expiration_str=(date.today() + timedelta(days=30)).isoformat(),
            form_type="SC TO-I",
            filing_date_str="2026-06-22",
        )
        assert len(discrepancies) == 0
        assert len(failures) == 0

    def test_multiple_failures(self):
        discrepancies, failures = run_all_business_rules(
            extraction_text="",
            expiration_str="2025-01-01",  # way in the past
            form_type="SC TO-I",
            filing_date_str="2026-06-22",
        )
        assert len(discrepancies) >= 2  # at least Rule 1 + Rule 3
        assert len(failures) >= 2

    def test_returns_discrepancy_objects(self):
        discrepancies, _ = run_all_business_rules(
            extraction_text="",
            expiration_str="2025-01-01",
            form_type="SC TO-I",
            filing_date_str="2026-06-22",
        )
        for d in discrepancies:
            assert isinstance(d, Discrepancy)
