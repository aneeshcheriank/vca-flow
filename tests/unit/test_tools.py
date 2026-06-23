"""Unit tests for src/tools.py — SEC filing I/O and parsing.

Tests use in-memory sample data; see test_tools_integration.py for
tests against real filing files.
"""

import pytest

from src.tools import (
    TARGET_FORM_PREFIXES,
    _extract_tag,
    _is_primary_document,
    extract_document_sections,
    extract_text_from_html,
    is_target_form_type,
    parse_sec_header,
)


# ---------------------------------------------------------------------------
# _extract_tag
# ---------------------------------------------------------------------------

class TestExtractTag:
    def test_paired_tags(self):
        result = _extract_tag("<TYPE>SC TO-I</TYPE>", "TYPE")
        assert result == "SC TO-I"

    def test_unpaired_tags_newline_separated(self):
        result = _extract_tag("<TYPE>SC TO-I\n<SEQUENCE>1", "TYPE")
        assert result == "SC TO-I"

    def test_tag_not_found(self):
        result = _extract_tag("<OTHER>x</OTHER>", "TYPE")
        assert result == ""

    def test_paired_tags_multiline_content(self):
        result = _extract_tag("<DESCRIPTION>Line 1\nLine 2</DESCRIPTION>", "DESCRIPTION")
        assert result == "Line 1\nLine 2"


# ---------------------------------------------------------------------------
# parse_sec_header
# ---------------------------------------------------------------------------

class TestParseSecHeader:
    def test_parses_top_level_keys(self, sample_sec_header):
        header = parse_sec_header(sample_sec_header)
        assert header["accession_number"] == "0001702510-26-000064"
        assert header["conformed_submission_type"] == "SC TO-I"
        assert header["filed_as_of_date"] == "20260622"

    def test_parses_indented_company_data(self, sample_sec_header):
        header = parse_sec_header(sample_sec_header)
        assert header["company_conformed_name"] == "Carlyle Credit Solutions, Inc."
        assert header["central_index_key"] == "0001702510"
        assert header["state_of_incorporation"] == "MD"

    def test_normalises_key_names(self, sample_sec_header):
        header = parse_sec_header(sample_sec_header)
        # Keys should be lowercase with underscores
        for key in header:
            assert key == key.lower()
            assert " " not in key

    def test_empty_text_returns_empty_dict(self):
        assert parse_sec_header("") == {}

    def test_no_sec_header_returns_empty_dict(self):
        assert parse_sec_header("<HTML><body>No header here</body></HTML>") == {}

    def test_first_occurrence_wins(self):
        """SUBJECT COMPANY and FILED BY both have COMPANY CONFORMED NAME.
        The first one (SUBJECT COMPANY) should be kept."""
        text = """<SEC-HEADER>
SUBJECT COMPANY:
    COMPANY DATA:
        COMPANY CONFORMED NAME:     First Company, Inc.
FILED BY:
    COMPANY DATA:
        COMPANY CONFORMED NAME:     Same Company, Inc.
</SEC-HEADER>"""
        header = parse_sec_header(text)
        assert header["company_conformed_name"] == "First Company, Inc."


# ---------------------------------------------------------------------------
# extract_document_sections
# ---------------------------------------------------------------------------

class TestExtractDocumentSections:
    def test_single_document(self, sample_document_block):
        docs = extract_document_sections(sample_document_block)
        assert len(docs) == 1
        assert docs[0]["type"] == "SC TO-I"
        assert docs[0]["sequence"] == "1"
        assert docs[0]["filename"] == "filing.htm"
        assert "Tender Offer" in docs[0]["text"]

    def test_multiple_documents(self):
        text = """<SEC-DOCUMENT>test.txt
<SEC-HEADER>...</SEC-HEADER>
<DOCUMENT>
<TYPE>SC TO-I
<SEQUENCE>1
<FILENAME>main.htm
<TEXT>Main filing text</TEXT>
</DOCUMENT>
<DOCUMENT>
<TYPE>EX-99.1
<SEQUENCE>2
<FILENAME>exhibit.htm
<TEXT>Exhibit text</TEXT>
</DOCUMENT>
</SEC-DOCUMENT>"""
        docs = extract_document_sections(text)
        assert len(docs) == 2
        assert docs[0]["type"] == "SC TO-I"
        assert docs[1]["type"] == "EX-99.1"

    def test_no_documents(self):
        docs = extract_document_sections("Just plain text, no SGML tags.")
        assert docs == []

    def test_description_optional(self):
        text = """<DOCUMENT>
<TYPE>SC TO-I
<SEQUENCE>1
<FILENAME>f.htm
<TEXT>Body</TEXT>
</DOCUMENT>"""
        docs = extract_document_sections(text)
        assert len(docs) == 1
        assert docs[0]["description"] == ""


# ---------------------------------------------------------------------------
# extract_text_from_html
# ---------------------------------------------------------------------------

class TestExtractTextFromHtml:
    def test_strips_tags(self, sample_html):
        text = extract_text_from_html(sample_html)
        assert "<div" not in text
        assert "<script>" not in text
        assert "console.log" not in text

    def test_extracts_visible_text(self, sample_html):
        text = extract_text_from_html(sample_html)
        assert "UNITED STATES" in text
        assert "SECURITIES AND EXCHANGE COMMISSION" in text
        assert "$25.00" in text
        assert "July 20, 2026" in text

    def test_removes_style_blocks(self, sample_html):
        text = extract_text_from_html(sample_html)
        assert "font-weight: bold" not in text
        assert ".bold" not in text

    def test_empty_html(self):
        assert extract_text_from_html("") == ""

    def test_plain_text_passthrough(self):
        text = "This is already plain text.\nNo HTML tags."
        result = extract_text_from_html(text)
        assert "plain text" in result


# ---------------------------------------------------------------------------
# _is_primary_document
# ---------------------------------------------------------------------------

class TestIsPrimaryDocument:
    def test_target_form_is_primary(self):
        doc = {"type": "SC TO-I"}
        assert _is_primary_document(doc)

    def test_amendment_is_primary(self):
        doc = {"type": "SC TO-I/A"}
        assert _is_primary_document(doc)

    def test_exhibit_is_not_primary(self):
        doc = {"type": "EX-99.1"}
        assert not _is_primary_document(doc)

    def test_exhibit_filing_fees_is_not_primary(self):
        doc = {"type": "EX-FILING FEES"}
        assert not _is_primary_document(doc)

    def test_unrelated_form_is_not_primary(self):
        doc = {"type": "8-K"}
        assert not _is_primary_document(doc)

    def test_case_insensitive(self):
        doc = {"type": "sc to-i"}
        assert _is_primary_document(doc)


# ---------------------------------------------------------------------------
# is_target_form_type
# ---------------------------------------------------------------------------

class TestIsTargetFormType:
    @pytest.mark.parametrize("form,expected", [
        ("SC TO-I", True),
        ("SC TO-I/A", True),
        ("SC 13E3", True),
        ("SC 13E3/A", True),
        ("SC 14D9", True),
        ("SC 14D9/A", True),
        ("8-K", False),
        ("8-K/A", False),
        ("425", False),
        ("10-K", False),
        ("", False),
    ])
    def test_form_type_matching(self, form, expected):
        assert is_target_form_type(form) == expected


# ---------------------------------------------------------------------------
# TARGET_FORM_PREFIXES
# ---------------------------------------------------------------------------

class TestTargetFormPrefixes:
    def test_contains_three_forms(self):
        assert len(TARGET_FORM_PREFIXES) == 3
        prefixes = [p.upper() for p in TARGET_FORM_PREFIXES]
        assert "SC TO-I" in prefixes
        assert "SC 13E3" in prefixes
        assert "SC 14D9" in prefixes
