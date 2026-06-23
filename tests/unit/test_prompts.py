"""Unit tests for src/prompts.py — prompt templates."""

import pytest

from src.prompts import (
    EXTRACTION_SYSTEM_PROMPT,
    EXTRACTION_USER_PROMPT,
    VALIDATION_SYSTEM_PROMPT,
    VALIDATION_USER_PROMPT,
)


class TestExtractionPrompts:
    def test_system_prompt_not_empty(self):
        assert len(EXTRACTION_SYSTEM_PROMPT) > 100
        assert "expert" in EXTRACTION_SYSTEM_PROMPT.lower()

    def test_user_prompt_format(self):
        rendered = EXTRACTION_USER_PROMPT.format(
            company_name="Test Corp",
            form_type="SC TO-I",
            file_date="2026-06-22",
            adsh="000-test",
            text="Filing text goes here.",
        )
        assert "Test Corp" in rendered
        assert "SC TO-I" in rendered
        assert "2026-06-22" in rendered
        assert "000-test" in rendered
        assert "Filing text goes here." in rendered

    def test_user_prompt_missing_placeholder_raises_key_error(self):
        with pytest.raises(KeyError):
            EXTRACTION_USER_PROMPT.format(
                company_name="Test",
                form_type="SC TO-I",
                # missing file_date, adsh, text
            )

    def test_user_prompt_unused_placeholders_ok(self):
        """Extra kwargs should be fine with format()."""
        rendered = EXTRACTION_USER_PROMPT.format(
            company_name="X",
            form_type="Y",
            file_date="Z",
            adsh="A",
            text="T",
            extra="unused",
        )
        assert "X" in rendered


class TestValidationPrompts:
    def test_system_prompt_not_empty(self):
        assert len(VALIDATION_SYSTEM_PROMPT) > 100
        assert "audit" in VALIDATION_SYSTEM_PROMPT.lower()

    def test_user_prompt_format(self):
        rendered = VALIDATION_USER_PROMPT.format(
            extraction_json='{"price": "$25.00"}',
            text="Original filing text.",
        )
        assert "$25.00" in rendered
        assert "Original filing text." in rendered

    def test_user_prompt_missing_placeholder_raises_key_error(self):
        with pytest.raises(KeyError):
            VALIDATION_USER_PROMPT.format(extraction_json="{}")
