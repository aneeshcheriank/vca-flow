#!/usr/bin/env python3
"""
LangGraph orchestrator for the corporate-action extraction pipeline.

Wires together the Extraction Agent and Validation Agent into a simple
two-node LangGraph graph:

    extract_filing  ──→  validate_extraction  ──→  END

If the extraction node fails (LLM error, no text), the filing is skipped
and recorded as an error — no validation is attempted.

CLI usage:
  ./venv/bin/python src/chain.py                              # today
  ./venv/bin/python src/chain.py --date 2026-06-22             # specific date
  ./venv/bin/python src/chain.py --adsh 0001702510-26-000064  # single filing
  ./venv/bin/python src/chain.py --dry-run                     # preview only
  ./venv/bin/python src/chain.py --max-filings 5               # limit for testing
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path
from typing import Any, TypedDict

# Ensure the project root is on sys.path so `from src.…` imports resolve
# when this script is invoked as `python src/chain.py`.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv
from langgraph.graph import END, StateGraph

from src.agents import ExtractionAgent, ValidationAgent, create_llm
from src.schema import CorporateActionExtraction, ValidationResult
from src.tools import (
    list_filings_for_date,
    prepare_filing_for_llm,
)

load_dotenv()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("chain")


def setup_logging(cron_mode: bool = False) -> None:
    level = logging.WARNING if cron_mode else logging.INFO
    handler = logging.StreamHandler(sys.stderr if cron_mode else sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(handler)
    log.setLevel(level)


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = BASE_DIR / "output" / "sec_filings"


# ---------------------------------------------------------------------------
# LangGraph state
# ---------------------------------------------------------------------------

class PipelineState(TypedDict):
    """State carried through the LangGraph nodes."""
    filing_path: str
    filing_text: str
    company: str
    form_type: str
    file_date: str
    adsh: str
    extraction: Any          # CorporateActionExtraction | None
    validation: Any          # ValidationResult | None
    error: str


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def _extract_node(state: PipelineState, agent: ExtractionAgent) -> PipelineState:
    """Extraction node: prepare text + run LLM extraction."""
    filing_path = Path(state["filing_path"])

    log.info("  Extracting: %s  (%s)", state["adsh"], state["company"])

    try:
        text = prepare_filing_for_llm(filing_path)
    except Exception as exc:
        return {**state, "error": f"Failed to read filing: {exc}"}

    if not text.strip():
        return {**state, "error": "No usable text extracted from filing"}

    state["filing_text"] = text

    extraction = agent.extract(
        filing_text=text,
        company=state["company"],
        form_type=state["form_type"],
        file_date=state["file_date"],
        adsh=state["adsh"],
    )

    if extraction is None:
        return {**state, "error": "Extraction LLM call failed"}

    state["extraction"] = extraction
    return state


def _validate_node(state: PipelineState, agent: ValidationAgent) -> PipelineState:
    """Validation node: business rules + LLM verification."""
    log.info("  Validating: %s", state["adsh"])

    validation = agent.validate(
        extraction=state["extraction"],
        source_text=state["filing_text"],
    )
    state["validation"] = validation
    return state


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph() -> StateGraph:
    """Build the extraction → validation LangGraph graph.

    Returns a compiled graph (Runnable) ready to invoke.
    """
    llm = create_llm()
    extraction_agent = ExtractionAgent(llm)
    validation_agent = ValidationAgent(llm)

    graph = StateGraph(PipelineState)

    # Nodes
    graph.add_node(
        "extract",
        lambda s: _extract_node(s, extraction_agent),
    )
    graph.add_node(
        "validate",
        lambda s: _validate_node(s, validation_agent),
    )

    # Edges
    graph.set_entry_point("extract")

    # Conditional: only validate if extraction succeeded
    def should_validate(state: PipelineState) -> str:
        if state.get("error"):
            return END  # type: ignore[return-value]
        return "validate"

    graph.add_conditional_edges("extract", should_validate, {
        "validate": "validate",
        END: END,
    })
    graph.add_edge("validate", END)

    return graph.compile()


# ---------------------------------------------------------------------------
# Processing helpers
# ---------------------------------------------------------------------------

def _read_meta_json(filing_path: Path) -> dict[str, str]:
    """Read the companion -meta.json file next to a .txt filing."""
    # Build path to companion -meta.json: <adsh>.txt → <adsh>-meta.json
    parent = filing_path.parent
    stem = filing_path.stem  # <adsh>
    meta_candidate = parent / f"{stem}-meta.json"

    if meta_candidate.exists():
        try:
            return json.loads(meta_candidate.read_text())
        except json.JSONDecodeError:
            pass
    return {}


def process_single(
    filing_path: Path,
    agent_extract: ExtractionAgent,
    agent_validate: ValidationAgent,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Process a single filing through the pipeline.

    Returns a dict with keys 'adsh', 'extraction', 'validation', 'error',
    or None if the filing should be skipped.
    """
    # --- Gather metadata ---
    meta = _read_meta_json(filing_path)
    adsh = meta.get("adsh", filing_path.stem)
    company = meta.get("company", "Unknown")
    form_type = meta.get("form", "?")
    file_date = meta.get("file_date", "?")

    log.info("Filing: %s  %s  %s  %s", form_type, company, adsh, file_date)

    if dry_run:
        log.info("  [DRY RUN] would extract & validate")
        return {
            "adsh": adsh,
            "company": company,
            "form_type": form_type,
            "file_date": file_date,
            "extraction": None,
            "validation": None,
            "error": None,
        }

    # Build initial state
    state: PipelineState = {
        "filing_path": str(filing_path),
        "filing_text": "",
        "company": company,
        "form_type": form_type,
        "file_date": file_date,
        "adsh": adsh,
        "extraction": None,
        "validation": None,
        "error": "",
    }

    # Extraction
    state = _extract_node(state, agent_extract)
    if state.get("error"):
        log.warning("  SKIPPED: %s", state["error"])
        return {
            "adsh": adsh,
            "company": company,
            "form_type": form_type,
            "file_date": file_date,
            "extraction": None,
            "validation": None,
            "error": state["error"],
        }

    # Validation
    state = _validate_node(state, agent_validate)

    extraction: CorporateActionExtraction | None = state.get("extraction")
    validation: ValidationResult | None = state.get("validation")

    confidence = validation.confidence.value if validation else "N/A"
    log.info("  Result: confidence=%s", confidence)

    return {
        "adsh": adsh,
        "company": company,
        "form_type": form_type,
        "file_date": file_date,
        "extraction": extraction.model_dump() if extraction else None,
        "validation": validation.model_dump() if validation else None,
        "error": None,
    }


def process_date(
    date_str: str,
    output_dir: Path,
    max_filings: int | None = None,
    dry_run: bool = False,
    result_path: Path | None = None,
) -> Path:
    """Process all target filings for a given date.

    Reads filings from *output_dir* / <date_str> / and writes results to
    *result_path* if given, otherwise to *output_dir* / <date_str> / corporate_action.json.

    Returns the path to the output JSON file.
    """
    date_dir = output_dir / date_str
    filings = list_filings_for_date(date_dir)

    if not filings:
        log.info("No target filings found for %s.", date_str)
        out_path = result_path or (date_dir / "corporate_action.json")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps([], indent=2))
        return out_path

    if max_filings:
        filings = filings[:max_filings]

    log.info("Found %d target filing(s) for %s.", len(filings), date_str)

    llm = create_llm()
    agent_extract = ExtractionAgent(llm)
    agent_validate = ValidationAgent(llm)

    results: list[dict[str, Any]] = []
    for filing_path in filings:
        result = process_single(filing_path, agent_extract, agent_validate, dry_run)
        if result is not None:
            results.append(result)

    # --- Write output ---
    out_path = result_path or (date_dir / "corporate_action.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str))
    log.info("Wrote %d result(s) to %s", len(results), out_path)

    # Summary
    success_count = sum(1 for r in results if r.get("extraction") is not None)
    error_count = sum(1 for r in results if r.get("error"))
    log.info("Summary: %d extracted, %d errors, %d total.",
             success_count, error_count, len(results))

    return out_path


def process_adsh(
    adsh: str,
    output_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    """Process a single filing by accession number.

    Searches the output directory for a filing matching *adsh*.
    """
    # Search for the filing
    for date_dir in sorted(output_dir.iterdir()):
        if not date_dir.is_dir():
            continue
        for form_dir in sorted(date_dir.iterdir()):
            if not form_dir.is_dir():
                continue
            txt_path = form_dir / f"{adsh}.txt"
            if txt_path.exists():
                llm = create_llm()
                agent_extract = ExtractionAgent(llm)
                agent_validate = ValidationAgent(llm)
                return process_single(txt_path, agent_extract, agent_validate, dry_run)

    log.error("Filing not found for ADSH: %s", adsh)
    return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract corporate actions from downloaded SEC filings",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--date",
        help="Date to process filings for (YYYY-MM-DD).  Default: today.",
    )
    parser.add_argument(
        "--adsh",
        help="Process a single filing by accession number.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Root output directory.  Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="List filings that would be processed without calling the LLM.",
    )
    parser.add_argument(
        "--max-filings",
        type=int,
        help="Limit the number of filings processed (useful for testing).",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Quiet mode (WARNING+ only).",
    )
    args = parser.parse_args()

    setup_logging(cron_mode=args.cron)

    output_dir = Path(args.output_dir)

    # --- Header ---
    log.info("=" * 62)
    log.info("SEC Corporate Action Extraction Pipeline")
    if args.dry_run:
        log.info("Mode: DRY RUN")
    log.info("=" * 62)

    if args.adsh:
        # Single-filing mode
        result = process_adsh(args.adsh, output_dir, dry_run=args.dry_run)
        if result and not args.dry_run:
            # Pretty-print to stdout
            print(json.dumps(result, indent=2, default=str))
    else:
        # Date mode
        end_date = date.fromisoformat(args.date) if args.date else date.today()
        date_str = end_date.isoformat()
        process_date(
            date_str=date_str,
            output_dir=output_dir,
            max_filings=args.max_filings,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
