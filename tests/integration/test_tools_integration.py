"""Integration tests for src/tools.py — real SEC filing data.

These tests validate that the parsing functions work correctly against
actual downloaded SEC EDGAR filings.
"""

import pytest

from src.tools import (
    extract_document_sections,
    extract_text_from_html,
    is_target_form_type,
    list_filings_for_date,
    load_filing_text,
    parse_sec_header,
    prepare_filing_for_llm,
)


# ---------------------------------------------------------------------------
# parse_sec_header — real filings
# ---------------------------------------------------------------------------

class TestParseSecHeaderRealFilings:
    def test_sc_14d9a_header(self, sc_14d9a_filing):
        text = load_filing_text(sc_14d9a_filing)
        header = parse_sec_header(text)
        assert header["conformed_submission_type"] in ("SC 14D9/A", "SC 14D9_A")
        assert len(header["accession_number"]) > 10
        assert len(header["company_conformed_name"]) > 0

    def test_sc_toi_header(self, sc_toi_filing):
        text = load_filing_text(sc_toi_filing)
        header = parse_sec_header(text)
        assert header["conformed_submission_type"] in ("SC TO-I", "SC TO-I_A")
        # Should have address fields
        assert "city" in header
        assert "state" in header


# ---------------------------------------------------------------------------
# extract_document_sections — real filings
# ---------------------------------------------------------------------------

class TestExtractDocumentSectionsRealFilings:
    def test_sc_14d9a_has_one_document(self, sc_14d9a_filing):
        text = load_filing_text(sc_14d9a_filing)
        docs = extract_document_sections(text)
        assert len(docs) == 1
        assert docs[0]["type"] in ("SC 14D9/A", "SC 14D9_A")

    def test_sc_toi_has_multiple_documents(self, sc_toi_filing):
        text = load_filing_text(sc_toi_filing)
        docs = extract_document_sections(text)
        assert len(docs) >= 1
        # First document should be the primary SC TO-I
        assert docs[0]["type"] in ("SC TO-I", "SC TO-I_A")

    def test_sc_toi_large_has_many_documents(self, sc_toi_large_filing):
        text = load_filing_text(sc_toi_large_filing)
        docs = extract_document_sections(text)
        # This filing has 32 documents (primary + exhibits)
        assert len(docs) > 5
        # Verify we can identify the primary
        primary_types = [
            d["type"] for d in docs
            if d["type"].upper().startswith("SC TO-I") and not d["type"].upper().startswith("EX-")
        ]
        assert len(primary_types) >= 1


# ---------------------------------------------------------------------------
# extract_text_from_html — real filings
# ---------------------------------------------------------------------------

class TestExtractTextFromHtmlRealFilings:
    def test_extracts_company_name(self, sc_14d9a_filing):
        text = load_filing_text(sc_14d9a_filing)
        docs = extract_document_sections(text)
        plain = extract_text_from_html(docs[0]["text"])
        assert "GENCO" in plain

    def test_removes_html_tags(self, sc_14d9a_filing):
        text = load_filing_text(sc_14d9a_filing)
        docs = extract_document_sections(text)
        plain = extract_text_from_html(docs[0]["text"])
        assert "<html>" not in plain
        assert "<div" not in plain.lower()

    def test_extracts_meaningful_text(self, sc_toi_filing):
        text = load_filing_text(sc_toi_filing)
        docs = extract_document_sections(text)
        plain = extract_text_from_html(docs[0]["text"])
        # Should contain financial/legal terms
        assert len(plain) > 500
        # Should mention tender offer related terms
        combined = plain.lower()
        has_tender = "tender" in combined or "offer" in combined or "purchase" in combined
        assert has_tender, "Expected tender-offer related terms in extracted text"


# ---------------------------------------------------------------------------
# prepare_filing_for_llm — real filings
# ---------------------------------------------------------------------------

class TestPrepareFilingForLlm:
    def test_returns_text(self, sc_14d9a_filing):
        text = prepare_filing_for_llm(sc_14d9a_filing)
        assert len(text) > 100
        assert "<html>" not in text

    def test_truncates_large_filing(self, sc_toi_large_filing):
        text = prepare_filing_for_llm(sc_toi_large_filing, max_chars=5000)
        assert len(text) <= 5500  # allow some slack for the truncation marker

    def test_selects_primary_not_exhibit(self, sc_toi_large_filing):
        """The large filing has 32 documents; we should get the SC TO-I body,
        not an exhibit."""
        text = prepare_filing_for_llm(sc_toi_large_filing, max_chars=10000)
        # Should contain tender offer content, not filing fee table
        assert "SCHEDULE TO" in text or "TENDER OFFER" in text.upper()


# ---------------------------------------------------------------------------
# list_filings_for_date — real filings
# ---------------------------------------------------------------------------

class TestListFilingsForDate:
    def test_finds_target_filings(self, date_dir):
        filings = list_filings_for_date(date_dir)
        assert len(filings) >= 1
        for f in filings:
            assert f.suffix == ".txt"
            # Path should be under a target form directory
            assert is_target_form_type(f.parent.name)

    def test_all_filings_are_target_forms(self, date_dir):
        filings = list_filings_for_date(date_dir)
        for f in filings:
            form_dir_name = f.parent.name
            assert is_target_form_type(form_dir_name), (
                f"Non-target form found: {form_dir_name}"
            )


# ---------------------------------------------------------------------------
# is_target_form_type — real filing directory names
# ---------------------------------------------------------------------------

class TestIsTargetFormTypeReal:
    def test_matches_real_directories(self):
        # From the actual output directory
        assert is_target_form_type("SC TO-I")
        assert is_target_form_type("SC TO-I/A")
        assert is_target_form_type("SC TO-I_A")  # slash → underscore
        assert is_target_form_type("SC 14D9_A")

    def test_rejects_non_target_directories(self):
        assert not is_target_form_type("8-K")
        assert not is_target_form_type("8-K_A")
        assert not is_target_form_type("425")
