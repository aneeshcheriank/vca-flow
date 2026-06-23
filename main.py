#!/usr/bin/env python3
"""
main.py — End-to-end corporate action extraction pipeline.

Runs the full workflow:
  1. Download today's voluntary SEC filings (SC TO-I, SC 13E3, SC 14D9).
  2. Run the LangGraph extraction + validation pipeline on each filing.
  3. Write results to output/corporate_actions/YYYY-MM-DD/ as JSON.

Usage:
  ./venv/bin/python main.py                              # today (download + process)
  ./venv/bin/python main.py --date 2026-06-22             # specific date
  ./venv/bin/python main.py --dry-run                     # preview only
  ./venv/bin/python main.py --download-only               # only download, skip processing
  ./venv/bin/python main.py --process-only                # only process, skip download
  ./venv/bin/python main.py --max-filings 5               # limit LLM calls for testing
  ./venv/bin/python main.py --cron                        # quiet mode
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

# Ensure the project root is on sys.path so `from src.…` imports resolve.
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.download_sec_voluntary_filings import (
    DEFAULT_TARGET_FORMS,
    load_manifest,
    process_dates,
    save_manifest,
    sec_session,
)
from src.chain import DEFAULT_OUTPUT_DIR as CHAIN_DEFAULT_OUTPUT_DIR
from src.chain import process_date as chain_process_date

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DOWNLOADS_DIR = ROOT / "output" / "sec_filings"
RESULTS_DIR = ROOT / "output" / "corporate_actions"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("main")


def setup_logging(cron_mode: bool = False) -> None:
    """Configure logging for the full pipeline."""
    level = logging.WARNING if cron_mode else logging.INFO
    handler = logging.StreamHandler(sys.stderr if cron_mode else sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # Configure all loggers used by the pipeline modules
    for name in ("main", "sec_filings", "chain"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.addHandler(handler)
        logger.setLevel(level)
        logger.propagate = False


# ---------------------------------------------------------------------------
# Step 1 — Download
# ---------------------------------------------------------------------------

def run_download(
    target_date: date,
    days: int = 1,
    dry_run: bool = False,
    no_manifest: bool = False,
) -> tuple[int, int, int]:
    """Download SEC filings for *target_date* (and *days* preceding it).

    Returns (downloaded, skipped, total_listings).
    """
    start_date = target_date - timedelta(days=days - 1)

    log.info("=" * 62)
    log.info("STEP 1 — Download SEC filings")
    log.info("Date range : %s  →  %s  (%d day%s)",
             start_date, target_date, days, "s" if days != 1 else "")
    log.info("Form types : %s", ", ".join(DEFAULT_TARGET_FORMS))
    log.info("Output dir : %s", DOWNLOADS_DIR)
    if dry_run:
        log.info("Mode       : DRY RUN")
    log.info("=" * 62)

    DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict = {} if no_manifest else load_manifest(DOWNLOADS_DIR)
    session = sec_session()

    downloaded, skipped, total = process_dates(
        session=session,
        start_date=start_date,
        end_date=target_date,
        form_types=list(DEFAULT_TARGET_FORMS),
        output_dir=DOWNLOADS_DIR,
        manifest=manifest,
        dry_run=dry_run,
    )

    if not dry_run:
        save_manifest(DOWNLOADS_DIR, manifest)

    if dry_run:
        log.info("DRY RUN: %d filing(s) would be downloaded (%d skipped) "
                 "out of %d total.", downloaded, skipped, total)
    else:
        log.info("Download complete: %d new, %d skipped/failed, %d total.",
                 downloaded, skipped, total)

    return downloaded, skipped, total


# ---------------------------------------------------------------------------
# Step 2 — Process (extraction + validation)
# ---------------------------------------------------------------------------

def run_processing(
    target_date: date,
    max_filings: int | None = None,
    dry_run: bool = False,
) -> Path:
    """Run the extraction + validation pipeline on downloaded filings.

    Reads from DOWNLOADS_DIR, writes results to RESULTS_DIR.

    Returns the path to the output JSON file.
    """
    date_str = target_date.isoformat()
    result_path = RESULTS_DIR / date_str / "corporate_action.json"

    log.info("=" * 62)
    log.info("STEP 2 — Extraction + Validation pipeline")
    log.info("Date       : %s", date_str)
    log.info("Input dir  : %s", DOWNLOADS_DIR)
    log.info("Output path: %s", result_path)
    if dry_run:
        log.info("Mode       : DRY RUN")
    if max_filings:
        log.info("Max filings: %d", max_filings)
    log.info("=" * 62)

    out_path = chain_process_date(
        date_str=date_str,
        output_dir=DOWNLOADS_DIR,
        max_filings=max_filings,
        dry_run=dry_run,
        result_path=result_path,
    )

    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end corporate action extraction from SEC EDGAR",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        help="Date to process (YYYY-MM-DD).  Default: today.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Number of days back from --date to download.  Default: 1.",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Preview filings without downloading or calling the LLM.",
    )
    parser.add_argument(
        "--max-filings",
        type=int,
        help="Limit the number of filings processed by the LLM (useful for testing).",
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Only download filings; skip the extraction + validation pipeline.",
    )
    parser.add_argument(
        "--process-only",
        action="store_true",
        help="Only run the pipeline on already-downloaded filings; skip download.",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Ignore download history and re-download everything.",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Quiet mode (WARNING+ only).",
    )
    args = parser.parse_args()

    setup_logging(cron_mode=args.cron)

    target_date = date.fromisoformat(args.date) if args.date else date.today()

    # --- Header ---
    log.info("=" * 62)
    log.info("Corporate Action Extraction Pipeline")
    log.info("Date: %s", target_date.isoformat())
    if args.dry_run:
        log.info("Mode: DRY RUN")
    log.info("=" * 62)

    # --- Step 1: Download ---
    if not args.process_only:
        downloaded, skipped, total = run_download(
            target_date=target_date,
            days=args.days,
            dry_run=args.dry_run,
            no_manifest=args.no_manifest,
        )
        if total == 0:
            log.info("No filings found — nothing to process.")
            if not args.download_only:
                # Still write an empty results file
                date_str = target_date.isoformat()
                out = RESULTS_DIR / date_str / "corporate_action.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps([], indent=2))
                log.info("Wrote empty result set to %s", out)
            return

    if args.download_only:
        log.info("Download-only mode — skipping extraction pipeline.")
        return

    # --- Step 2: Process ---
    out_path = run_processing(
        target_date=target_date,
        max_filings=args.max_filings,
        dry_run=args.dry_run,
    )

    log.info("=" * 62)
    log.info("Pipeline complete.")
    log.info("Results: %s", out_path)

    # Print summary from the output file
    if out_path.exists() and not args.dry_run:
        try:
            results = json.loads(out_path.read_text())
            success = sum(1 for r in results if r.get("extraction") is not None)
            errors = sum(1 for r in results if r.get("error"))
            skipped_val = len(results) - success - errors
            log.info("Summary: %d extracted, %d errors, %d skipped, %d total.",
                     success, errors, skipped_val, len(results))

            # Print confidence distribution
            conf_counts = {"Low": 0, "Medium": 0, "High": 0}
            for r in results:
                val = r.get("validation")
                if val and val.get("confidence"):
                    conf = val["confidence"]
                    if conf in conf_counts:
                        conf_counts[conf] += 1
            log.info("Confidence: High=%d Medium=%d Low=%d",
                     conf_counts["High"], conf_counts["Medium"], conf_counts["Low"])
        except (json.JSONDecodeError, KeyError):
            pass


if __name__ == "__main__":
    main()
