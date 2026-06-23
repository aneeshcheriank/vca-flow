"""Unit tests for src/schema.py — Pydantic models."""

import pytest
from pydantic import ValidationError

from src.schema import (
    ConfidenceScore,
    CorporateActionExtraction,
    Discrepancy,
    ValidationResult,
)


# ---------------------------------------------------------------------------
# ConfidenceScore
# ---------------------------------------------------------------------------

class TestConfidenceScore:
    def test_valid_values(self):
        assert ConfidenceScore.LOW.value == "Low"
        assert ConfidenceScore.MEDIUM.value == "Medium"
        assert ConfidenceScore.HIGH.value == "High"

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            ConfidenceScore("Invalid")


# ---------------------------------------------------------------------------
# CorporateActionExtraction
# ---------------------------------------------------------------------------

class TestCorporateActionExtraction:
    def test_valid_extraction(self, valid_extraction_dict):
        obj = CorporateActionExtraction(**valid_extraction_dict)
        assert obj.company_name == "Test Corp"
        assert obj.form_type == "SC TO-I"
        assert obj.price == "$25.00 per share"
        assert obj.expiration_date == "2026-07-20"

    def test_missing_required_field_raises(self, valid_extraction_dict):
        del valid_extraction_dict["price"]
        with pytest.raises(ValidationError):
            CorporateActionExtraction(**valid_extraction_dict)

    def test_empty_strings_accepted(self, valid_extraction_dict):
        """Empty strings are technically valid — business rules catch them later."""
        valid_extraction_dict["price"] = ""
        obj = CorporateActionExtraction(**valid_extraction_dict)
        assert obj.price == ""

    def test_extra_fields_ignored(self, valid_extraction_dict):
        valid_extraction_dict["extra_field"] = "should be ignored"
        obj = CorporateActionExtraction(**valid_extraction_dict)
        assert not hasattr(obj, "extra_field")

    def test_all_fields_present_in_schema(self, valid_extraction_dict):
        obj = CorporateActionExtraction(**valid_extraction_dict)
        fields = obj.model_dump()
        assert set(fields.keys()) == {
            "company_name", "form_type", "offer_terms",
            "price", "expiration_date", "filing_adsh",
        }


# ---------------------------------------------------------------------------
# Discrepancy
# ---------------------------------------------------------------------------

class TestDiscrepancy:
    def test_valid_discrepancy(self):
        d = Discrepancy(
            rule="Rule 1",
            severity="error",
            description="Expiration date is in the past.",
        )
        assert d.rule == "Rule 1"
        assert d.severity == "error"

    def test_missing_fields_raises(self):
        with pytest.raises(ValidationError):
            Discrepancy(rule="Rule 1")  # missing severity, description


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_valid_high_confidence(self, valid_validation_dict):
        obj = ValidationResult(**valid_validation_dict)
        assert obj.confidence == ConfidenceScore.HIGH
        assert obj.discrepancies == []
        assert obj.business_rule_failures == []

    def test_with_discrepancies(self, valid_validation_dict):
        valid_validation_dict["discrepancies"] = [
            {"rule": "Rule 1", "severity": "error", "description": "Bad date."},
        ]
        obj = ValidationResult(**valid_validation_dict)
        assert len(obj.discrepancies) == 1
        assert obj.discrepancies[0].rule == "Rule 1"

    def test_default_factories(self):
        obj = ValidationResult(adsh="000-test", confidence=ConfidenceScore.LOW)
        assert obj.discrepancies == []
        assert obj.business_rule_failures == []
        assert obj.notes == ""

    def test_model_dump(self, valid_validation_dict):
        obj = ValidationResult(**valid_validation_dict)
        dumped = obj.model_dump()
        assert dumped["confidence"] == "High"
        assert isinstance(dumped["discrepancies"], list)

    def test_serialization_roundtrip(self, valid_validation_dict):
        obj = ValidationResult(**valid_validation_dict)
        json_str = obj.model_dump_json()
        rehydrated = ValidationResult.model_validate_json(json_str)
        assert rehydrated.confidence == obj.confidence
        assert rehydrated.adsh == obj.adsh
