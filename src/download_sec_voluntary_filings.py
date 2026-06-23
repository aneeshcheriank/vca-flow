#!/usr/bin/env python3
"""
download_sec_voluntary_filings.py

Download daily voluntary SEC filings related to corporate actions from EDGAR.

Uses the SEC Full-Text Search API (efts.sec.gov) to discover filings, then
downloads the full submission text from www.sec.gov/Archives.

Target form types (voluntary corporate action filings):
  - SC TO-I      — Tender Offer Statement (Issuer)
  - SC 13E3      — Going Private Transaction
  - SC 14D9      — Tender Offer Solicitation / Recommendation

These three form types are the primary carriers of voluntary corporate action
information in SEC EDGAR filings.

Usage:
  ./venv/bin/python download_sec_voluntary_filings.py                        # today
  ./venv/bin/python download_sec_voluntary_filings.py --date 2026-06-22      # specific date
  ./venv/bin/python download_sec_voluntary_filings.py --date 2026-06-20 --days 5  # date range
  ./venv/bin/python download_sec_voluntary_filings.py --dry-run              # preview only
  ./venv/bin/python download_sec_voluntary_filings.py --cron                 # quiet, for cron

SEC fair-access requirements:
  - Identify yourself: set USER_AGENT below (org name + email).
  - Rate limit: ≤ 10 requests/second.  This script uses 0.12 s delay.
"""

import argparse
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# User configuration — CHANGE THESE
# ---------------------------------------------------------------------------

USER_AGENT = "MyCompanyName my-email@example.com"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EFTS_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# Form types — voluntary filings covering corporate actions.
# NOTE: Only use base forms. The SEC API maps amendments (e.g. SC TO-I/A)
# to the base root_forms field automatically, so they are included without
# being listed separately.
DEFAULT_TARGET_FORMS = [
    "SC TO-I",
    "SC 13E3",
    "SC 14D9",
]

# SEC rate limit (≤ 10 req/s); 0.12 s → ~8.3 req/s, safe margin
SEC_DELAY = 0.12
EFTS_PAGE_SIZE = 100       # max allowed by the API
EFTS_MAX_OFFSET = 9_900    # SEC caps results at 10 000

DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "sec_filings"
MANIFEST_FILE = "manifest.json"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

log = logging.getLogger("sec_filings")


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
# HTTP session
# ---------------------------------------------------------------------------

def sec_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Encoding": "gzip, deflate",
    })
    return s


# ---------------------------------------------------------------------------
# SEC EDGAR Full-Text Search API
# ---------------------------------------------------------------------------

def search_filings(
    session: requests.Session,
    start_date: date,
    end_date: date,
    form_types: list[str],
    from_offset: int = 0,
    size: int = EFTS_PAGE_SIZE,
) -> dict | None:
    """
    Query the SEC full-text search index for filings.

    Returns the parsed JSON response, or None on failure.
    Retries transient server errors (500, 502, 503) up to 3 times
    with exponential backoff, since the SEC API is occasionally flaky.
    """
    forms_param = ",".join(form_types)
    params = {
        "dateRange": "custom",
        "startdt": start_date.isoformat(),
        "enddt": end_date.isoformat(),
        "from": from_offset,
        "size": size,
        "forms": forms_param,
    }

    log.debug("  Search API: offset=%d size=%d", from_offset, size)

    max_retries = 3
    for attempt in range(max_retries + 1):
        try:
            resp = session.get(EFTS_SEARCH_URL, params=params, timeout=30)

            if resp.status_code == 429:
                wait = 2 * (attempt + 1)
                log.warning("  Rate-limited (429). Waiting %d s ...", wait)
                time.sleep(wait)
                continue

            if resp.status_code in (500, 502, 503):
                if attempt < max_retries:
                    wait = 2 ** attempt  # 1, 2, 4 seconds
                    log.warning(
                        "  Server error (%d) at offset %d — retry %d/%d in %d s ...",
                        resp.status_code, from_offset, attempt + 1, max_retries, wait,
                    )
                    time.sleep(wait)
                    continue
                # final attempt — fall through to raise_for_status

            resp.raise_for_status()
            return resp.json()

        except requests.RequestException as exc:
            if attempt < max_retries:
                wait = 2 ** attempt
                log.warning("  Request error (%s) — retry %d/%d in %d s ...",
                            exc, attempt + 1, max_retries, wait)
                time.sleep(wait)
                continue
            log.error("  Search API error (final): %s", exc)
            return None

    return None


def fetch_all_listings(
    session: requests.Session,
    start_date: date,
    end_date: date,
    form_types: list[str],
) -> list[dict]:
    """
    Fetch *all* matching filing metadata from the SEC search index,
    paginating through results.
    """
    all_hits: list[dict] = []

    # First page — discover total count
    first_page = search_filings(session, start_date, end_date, form_types, from_offset=0)
    if first_page is None:
        return []

    total = first_page["hits"]["total"]["value"]
    hits = first_page["hits"]["hits"]
    all_hits.extend(hits)
    log.info("  %d total filings found (%d on first page)", total, len(hits))

    # Paginate
    offset = EFTS_PAGE_SIZE
    while offset < total and offset < EFTS_MAX_OFFSET:
        time.sleep(SEC_DELAY)
        page = search_filings(session, start_date, end_date, form_types, from_offset=offset)
        if page is None:
            break
        page_hits = page["hits"]["hits"]
        if not page_hits:
            break
        all_hits.extend(page_hits)
        log.debug("  Fetched offset %d (%d hits)", offset, len(page_hits))
        offset += EFTS_PAGE_SIZE

    if total > EFTS_MAX_OFFSET:
        log.warning(
            "  Results truncated at %d — SEC limits access to first 10 000 hits. "
            "Narrow your date range to capture everything.",
            EFTS_MAX_OFFSET,
        )

    return all_hits


# ---------------------------------------------------------------------------
# Filing download helpers
# ---------------------------------------------------------------------------

def _cik_raw(padded_cik: str) -> str:
    """Strip leading zeros from a 10-digit padded CIK."""
    return padded_cik.lstrip("0")


def _adsh_no_dashes(adsh: str) -> str:
    """Remove dashes from accession number for URL construction."""
    return adsh.replace("-", "")


def _build_filing_url(cik: str, adsh: str) -> str:
    """Build the .txt full-submission URL from CIK + accession number."""
    raw_cik = _cik_raw(cik)
    adsh_clean = _adsh_no_dashes(adsh)
    return f"{SEC_ARCHIVES_BASE}/{raw_cik}/{adsh_clean}/{adsh}.txt"


def _build_index_url(cik: str, adsh: str) -> str:
    """Build the -index.htm filing-index URL."""
    raw_cik = _cik_raw(cik)
    adsh_clean = _adsh_no_dashes(adsh)
    return f"{SEC_ARCHIVES_BASE}/{raw_cik}/{adsh_clean}/{adsh}-index.htm"


def download_filing(
    session: requests.Session,
    cik: str,
    adsh: str,
    form_type: str,
    company: str,
    file_date: str,
    output_dir: Path,
) -> Path | None:
    """
    Download a filing's full submission text and its filing index.

    Directory layout:
      output_dir / YYYY-MM-DD / <form_type> / <adsh>.txt
      output_dir / YYYY-MM-DD / <form_type> / <adsh>-index.htm
      output_dir / YYYY-MM-DD / <form_type> / <adsh>-meta.json

    Returns path to the downloaded .txt file, or None on failure.
    """
    form_dir = output_dir / file_date / form_type.replace("/", "_")
    form_dir.mkdir(parents=True, exist_ok=True)

    txt_path = form_dir / f"{adsh}.txt"
    idx_path = form_dir / f"{adsh}-index.htm"
    meta_path = form_dir / f"{adsh}-meta.json"

    # --- Save metadata ---
    if not meta_path.exists():
        meta = {
            "cik": cik,
            "adsh": adsh,
            "form": form_type,
            "company": company,
            "file_date": file_date,
            "downloaded_at": datetime.now().isoformat(),
        }
        meta_path.write_text(json.dumps(meta, indent=2))

    # --- Full submission text ---
    if txt_path.exists():
        log.debug("  Already downloaded: %s", txt_path.name)
    else:
        url = _build_filing_url(cik, adsh)
        log.info("  Downloading %s  %s  (%s)", form_type, company, adsh)
        try:
            resp = session.get(url, timeout=120)
            resp.raise_for_status()
            txt_path.write_bytes(resp.content)
            time.sleep(SEC_DELAY)
        except requests.RequestException as exc:
            log.error("  FAILED %s: %s", adsh, exc)
            # Remove partial file
            if txt_path.exists():
                txt_path.unlink()
            return None

    # --- Filing index (nice-to-have) ---
    if not idx_path.exists():
        url = _build_index_url(cik, adsh)
        try:
            resp = session.get(url, timeout=30)
            resp.raise_for_status()
            idx_path.write_bytes(resp.content)
            time.sleep(SEC_DELAY)
        except requests.RequestException:
            log.debug("  Index not available for %s", adsh)

    return txt_path


# ---------------------------------------------------------------------------
# Manifest — track processed filings across runs
# ---------------------------------------------------------------------------

def load_manifest(output_dir: Path) -> dict:
    p = output_dir / MANIFEST_FILE
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            log.warning("Corrupt manifest — starting fresh")
    return {}


def save_manifest(output_dir: Path, manifest: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / MANIFEST_FILE).write_text(json.dumps(manifest, indent=2))


def is_processed(manifest: dict, adsh: str) -> bool:
    return adsh in manifest


def mark_processed(manifest: dict, adsh: str, ok: bool, local_path: str | None) -> None:
    manifest[adsh] = {
        "ok": ok,
        "local_path": local_path,
        "processed_at": datetime.now().isoformat(),
    }


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_dates(
    session: requests.Session,
    start_date: date,
    end_date: date,
    form_types: list[str],
    output_dir: Path,
    manifest: dict,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """
    Discover and download all matching filings for the date range.

    Returns (newly_downloaded, skipped, total_listings).
    """
    log.info("Searching SEC EDGAR for %s → %s ...", start_date, end_date)
    listings = fetch_all_listings(session, start_date, end_date, form_types)

    if not listings:
        log.info("No filings found for the given criteria.")
        return 0, 0, 0

    total = len(listings)
    downloaded = 0
    skipped = 0

    for hit in listings:
        src = hit["_source"]
        adsh = src["adsh"]
        cik = src["ciks"][0] if src.get("ciks") else "?"
        form_type = src.get("form", "?")
        company = src.get("display_names", ["Unknown"])[0]
        file_date = src.get("file_date", "?")

        if is_processed(manifest, adsh):
            log.debug("  Skipping (manifest): %s — %s", form_type, company)
            skipped += 1
            continue

        if dry_run:
            log.info("  [DRY] %-12s  %s  %s", form_type, company, adsh)
            downloaded += 1
            continue

        time.sleep(SEC_DELAY)
        local = download_filing(session, cik, adsh, form_type, company, file_date, output_dir)
        success = local is not None
        mark_processed(manifest, adsh, success, str(local) if local else None)

        if success:
            downloaded += 1
        else:
            skipped += 1  # failed downloads count as skipped

    return downloaded, skipped, total


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download daily voluntary SEC filings for corporate actions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--date",
        help="End date for filings (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=1,
        help="Number of days back from --date to fetch. Default: 1.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Output directory. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="List filings without downloading anything.",
    )
    parser.add_argument(
        "--cron",
        action="store_true",
        help="Quiet mode (WARNING+ only). Suitable for cron jobs.",
    )
    parser.add_argument(
        "--forms",
        nargs="+",
        help="Override target form types. Default: SC TO-I SC 13E3 SC 14D9",
    )
    parser.add_argument(
        "--no-manifest",
        action="store_true",
        help="Ignore download history and re-download everything.",
    )
    args = parser.parse_args()

    setup_logging(cron_mode=args.cron)

    # --- Form types ---
    form_types = args.forms if args.forms else list(DEFAULT_TARGET_FORMS)

    # --- Date range ---
    end_date = date.fromisoformat(args.date) if args.date else date.today()
    start_date = end_date - timedelta(days=args.days - 1)

    output_dir = Path(args.output_dir)
    manifest: dict = {} if args.no_manifest else load_manifest(output_dir)

    # --- Header ---
    log.info("=" * 62)
    log.info("SEC Voluntary Corporate Action Filings Downloader")
    log.info("Date range : %s  →  %s  (%d day%s)",
             start_date, end_date, args.days, "s" if args.days != 1 else "")
    log.info("Form types : %s", ", ".join(form_types))
    log.info("Output dir : %s", output_dir)
    if args.dry_run:
        log.info("Mode       : DRY RUN")
    log.info("=" * 62)

    session = sec_session()

    downloaded, skipped, total = process_dates(
        session=session,
        start_date=start_date,
        end_date=end_date,
        form_types=form_types,
        output_dir=output_dir,
        manifest=manifest,
        dry_run=args.dry_run,
    )

    # --- Save manifest ---
    if not args.dry_run:
        save_manifest(output_dir, manifest)

    # --- Summary ---
    log.info("=" * 62)
    if args.dry_run:
        log.info("DRY RUN: %d filing(s) would be downloaded (%d skipped/filtered) "
                 "out of %d total across %d day%s.",
                 downloaded, skipped, total, args.days, "s" if args.days != 1 else "")
    else:
        log.info("Done: %d downloaded, %d skipped/failed, %d total found across %d day%s.",
                 downloaded, skipped, total, args.days, "s" if args.days != 1 else "")
    log.info("Output: %s", output_dir)


if __name__ == "__main__":
    main()
