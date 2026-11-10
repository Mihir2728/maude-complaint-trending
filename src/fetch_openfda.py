"""Download MAUDE adverse-event reports from the openFDA device/event API.

Writes one gzipped JSON-lines file per calendar year to data/raw/.

Usage:
    python src/fetch_openfda.py                        # uses config.yaml
    OPENFDA_API_KEY=xxxx python src/fetch_openfda.py   # higher rate limits

openFDA notes (https://open.fda.gov/apis/device/event/):
  * max 1,000 records per request;
  * `skip` pagination is capped at 25,000 records per query, so the date range
    is split into calendar months (far below that cap for one product code);
  * every request carries the full parameter set (search, limit, skip, api_key);
  * HTTP 404 with "No matches found" means an empty result, not an error.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
from datetime import date

import requests

sys.path.insert(0, os.path.dirname(__file__))
from common import RAW_DIR, load_config  # noqa: E402

BASE_URL = "https://api.fda.gov/device/event.json"
PAGE_SIZE = 1000
MAX_SKIP = 25000


def build_search(product_codes: list[str], start: str, end: str) -> str:
    codes = " OR ".join(f'device.device_report_product_code:"{c}"' for c in product_codes)
    return f"({codes}) AND date_received:[{start} TO {end}]"


def month_chunks(date_from: str, date_to: str):
    """Yield (year, start, end) for each calendar month, as YYYYMMDD strings."""
    d0, d1 = date.fromisoformat(date_from), date.fromisoformat(date_to)
    cur = date(d0.year, d0.month, 1)
    while cur <= d1:
        nxt = date(cur.year + (cur.month == 12), cur.month % 12 + 1, 1)
        start = max(d0, cur)
        end = min(d1, date.fromordinal(nxt.toordinal() - 1))
        yield cur.year, start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
        cur = nxt


def get_with_retry(session: requests.Session, params: dict, tries: int = 6):
    for attempt in range(tries):
        try:
            r = session.get(BASE_URL, params=params, timeout=60)
        except requests.RequestException as e:
            wait = 2 ** attempt
            print(f"   network error ({e.__class__.__name__}); retrying in {wait}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code == 404:  # openFDA: no matches
            return None
        if r.status_code in (429, 500, 502, 503, 504):
            wait = 2 ** attempt
            print(f"   HTTP {r.status_code}; retrying in {wait}s", flush=True)
            time.sleep(wait)
            continue
        if r.status_code >= 400:
            raise RuntimeError(f"openFDA HTTP {r.status_code}: {r.text[:300]}")
        return r.json()
    raise RuntimeError(f"openFDA request failed after {tries} tries: {params.get('search')}")


def fetch_query(session, search: str, api_key: str | None):
    """Yield (total, results) pages for one query using skip pagination."""
    skip = 0
    while True:
        params = {"search": search, "limit": PAGE_SIZE, "skip": skip}
        if api_key:
            params["api_key"] = api_key
        payload = get_with_retry(session, params)
        if not payload or not payload.get("results"):
            return
        total = payload.get("meta", {}).get("results", {}).get("total", 0)
        yield total, payload["results"]
        skip += PAGE_SIZE
        if skip >= total:
            return
        if skip >= MAX_SKIP:
            print(f"   WARNING: >{MAX_SKIP:,} reports in one month; only the first "
                  f"{MAX_SKIP:,} were downloaded.", flush=True)
            return
        time.sleep(0.25)  # stay well under 240 requests/minute


def main() -> None:
    cfg = load_config()
    api_key = os.environ.get("OPENFDA_API_KEY")
    print("   using API key" if api_key else "   no API key (fine; lower daily limit)", flush=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    remaining = int(cfg.get("max_records", 150000))
    session = requests.Session()
    session.headers["User-Agent"] = "maude-complaint-trending (portfolio project)"

    files: dict[int, gzip.GzipFile] = {}
    year_counts: dict[int, int] = {}
    try:
        for year, start, end in month_chunks(cfg["date_from"], cfg["date_to"]):
            if year not in files:
                if files:
                    prev = max(files)
                    print(f"[{prev}] {year_counts[prev]:,} reports", flush=True)
                files[year] = gzip.open(RAW_DIR / f"events_{year}.jsonl.gz", "wt", encoding="utf-8")
                year_counts[year] = 0
            search = build_search(cfg["product_codes"], start, end)
            for _, batch in fetch_query(session, search, api_key):
                for rec in batch:
                    files[year].write(json.dumps(rec) + "\n")
                year_counts[year] += len(batch)
                remaining -= len(batch)
            print(f"   {start[:4]}-{start[4:6]}: {year_counts[year]:,} so far this year", flush=True)
            if remaining <= 0:
                print("Reached max_records cap in config.yaml; stopping early.")
                break
    finally:
        for f in files.values():
            f.close()
    if year_counts:
        last = max(year_counts)
        print(f"[{last}] {year_counts[last]:,} reports")
    print(f"Done: {sum(year_counts.values()):,} reports written to {RAW_DIR}")


if __name__ == "__main__":
    main()
