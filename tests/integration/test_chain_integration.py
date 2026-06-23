"""Integration tests for the extraction pipeline (src/chain.py).

Tests the pipeline in dry-run mode (no LLM calls) against real filing data,
and verifies the output structure.
"""

import json

import pytest

from src.chain import (
    DEFAULT_OUTPUT_DIR,
    process_date,
    process_single,
)
from src.tools import list_filings_for_date


# ---------------------------------------------------------------------------
# process_date — dry run
# ---------------------------------------------------------------------------

class TestProcessDateDryRun:
    def test_dry_run_writes_output(self, date_dir, tmp_path):
        """Dry run should write a JSON file even without LLM calls."""
        date_str = "2026-06-22"
        output_path = tmp_path / "corporate_action.json"

        result_path = process_date(
            date_str=date_str,
            output_dir=DEFAULT_OUTPUT_DIR,
            max_filings=2,
            dry_run=True,
            result_path=output_path,
        )

        assert result_path == output_path
        assert output_path.exists()

        data = json.loads(output_path.read_text())
        assert isinstance(data, list)
        assert len(data) == 2
        for entry in data:
            assert "adsh" in entry
            assert "company" in entry
            assert "form_type" in entry
            # Dry run — no actual extraction
            assert entry["extraction"] is None
            assert entry["validation"] is None

    def test_dry_run_with_no_filings(self, tmp_path):
        """An empty date directory should produce an empty result."""
        date_str = "2020-01-01"  # no data for this date
        output_path = tmp_path / "empty.json"

        result_path = process_date(
            date_str=date_str,
            output_dir=DEFAULT_OUTPUT_DIR,
            dry_run=True,
            result_path=output_path,
        )

        data = json.loads(result_path.read_text())
        assert data == []

    def test_max_filings_limits_output(self, date_dir, tmp_path):
        """--max-filings should cap the number processed."""
        date_str = "2026-06-22"
        output_path = tmp_path / "limited.json"

        process_date(
            date_str=date_str,
            output_dir=DEFAULT_OUTPUT_DIR,
            max_filings=1,
            dry_run=True,
            result_path=output_path,
        )

        data = json.loads(output_path.read_text())
        assert len(data) == 1


# ---------------------------------------------------------------------------
# process_single — dry run
# ---------------------------------------------------------------------------

class TestProcessSingleDryRun:
    def test_returns_expected_structure(self, sc_14d9a_filing):
        """process_single with dry_run should return metadata without LLM calls."""
        from src.agents import ExtractionAgent, ValidationAgent, create_llm

        llm = create_llm()
        agent_extract = ExtractionAgent(llm)
        agent_validate = ValidationAgent(llm)

        result = process_single(
            sc_14d9a_filing,
            agent_extract,
            agent_validate,
            dry_run=True,
        )

        assert result is not None
        assert result["adsh"] == "0001140361-26-025974"
        assert "GENCO" in result["company"]
        assert result["form_type"] in ("SC 14D9/A", "SC 14D9_A")
        assert result["extraction"] is None  # dry run
        assert result["validation"] is None
        assert result["error"] is None


# ---------------------------------------------------------------------------
# Full pipeline structure validation
# ---------------------------------------------------------------------------

class TestOutputStructure:
    def test_all_listed_filings_are_processable(self, date_dir):
        """Every filing returned by list_filings_for_date should have a
        companion -meta.json file with the expected fields."""
        filings = list_filings_for_date(date_dir)
        assert len(filings) > 0

        for filing in filings:
            meta_path = filing.parent / f"{filing.stem}-meta.json"
            assert meta_path.exists(), f"Missing meta.json for {filing.name}"
            meta = json.loads(meta_path.read_text())
            assert "adsh" in meta
            assert "form" in meta
            assert "company" in meta
            assert "file_date" in meta

    def test_dry_run_output_matches_input_count(self, date_dir, tmp_path):
        """The number of entries in the output JSON should match the number
        of target filings in the input directory."""
        filings = list_filings_for_date(date_dir)
        expected_count = len(filings)

        date_str = "2026-06-22"
        output_path = tmp_path / "out.json"

        process_date(
            date_str=date_str,
            output_dir=DEFAULT_OUTPUT_DIR,
            dry_run=True,
            result_path=output_path,
        )

        data = json.loads(output_path.read_text())
        assert len(data) == expected_count
