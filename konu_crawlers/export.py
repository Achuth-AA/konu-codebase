"""``python -m konu_crawlers export``: every crawled record → one combined CSV.

Reads the records written by ``crawl`` (JSONL) from every source - listings,
projects, agents, layouts, permissions, market values, land records - and
writes one CSV row per record in :data:`konu_crawlers.schema.CSV_COLUMNS`
order. For listings it also loads the detail page for the facts search pages
lack (cached under ``data/details_cache`` so re-runs only fetch new
listings).
"""

from __future__ import annotations

import csv
import glob
import hashlib
import json
import logging
import sys
from pathlib import Path

from .details import fetch_details
from .http import BlockedError, FetchError, Fetcher
from .models import Record
from .schema import CSV_COLUMNS, record_key, to_csv_row

log = logging.getLogger(__name__)
BROWSER_SOURCES = {"quikr"}


def load_materials(path: str | None, cement: float | None, steel: float | None) -> dict:
    """``YearMonth -> {cement, steel}``; ``"*"`` holds a flat value for every month."""
    out: dict[str, dict] = {}
    if path:
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                ym = (row.get("YearMonth") or row.get("year_month") or "").strip()
                if ym:
                    out[ym] = {"cement": float(row["cement"]) if row.get("cement") else None,
                               "steel": float(row["steel"]) if row.get("steel") else None}
    if cement is not None or steel is not None:
        out["*"] = {"cement": cement, "steel": steel}
    return out


def _cache_path(cache: Path, rec: Record) -> Path:
    key = rec.source_id or hashlib.sha1((rec.url or "").encode()).hexdigest()[:16]
    return cache / rec.source / f"{key}.json"


def run(inputs: list[str], out: str, details: bool, cache_dir: str, materials: dict, delay: float,
        proxy: str | None, sources: list[str] | None) -> int:
    files = sorted({f for pat in inputs for f in glob.glob(pat)})
    recs: dict[str, Record] = {}
    for f in files:
        for line in open(f, encoding="utf-8"):
            r = Record(**json.loads(line))
            if sources and r.source not in sources:
                continue
            recs.setdefault(record_key(r.to_dict(include_raw=False)), r)
    kinds: dict[str, int] = {}
    for r in recs.values():
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    print(f"{len(recs)} records from {len(files)} files ({', '.join(f'{n} {k}' for k, n in sorted(kinds.items()))})", file=sys.stderr)
    cache = Path(cache_dir)
    fetcher = Fetcher(delay=delay, proxy=proxy, retries=1)
    blocked: set[str] = set()
    stats: dict[str, list[int]] = {}
    rows = []
    try:
        for i, rec in enumerate(recs.values(), 1):
            d: dict = {}
            cp = _cache_path(cache, rec)
            wants_details = rec.kind == "listing" and bool(rec.url)
            if wants_details and cp.exists():
                d = json.loads(cp.read_text())
            elif wants_details and details and rec.source not in blocked:
                try:
                    d = fetch_details(rec, fetcher, render=rec.source in BROWSER_SOURCES)
                    cp.parent.mkdir(parents=True, exist_ok=True)
                    cp.write_text(json.dumps(d, ensure_ascii=False, default=str))
                except BlockedError as e:
                    blocked.add(rec.source)  # one block per site is enough to know
                    print(f"  {rec.source}: detail pages blocked here – {str(e)[:120]}", file=sys.stderr)
                except (FetchError, Exception) as e:  # noqa: BLE001 - one bad page must not stop the export
                    log.warning("details failed for %s: %s", rec.url, e)
            s = stats.setdefault(rec.source, [0, 0])
            s[0] += 1
            s[1] += 1 if d else 0
            rows.append(to_csv_row(rec, d, materials))
            if i % 50 == 0:
                print(f"  {i}/{len(recs)}", file=sys.stderr)
    finally:
        fetcher.close()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} rows to {out}", file=sys.stderr)
    for src, (n, with_d) in sorted(stats.items()):
        print(f"  {src:<16} {n:>5} rows, {with_d:>5} with detail-page facts", file=sys.stderr)
    return 0 if rows else 1
