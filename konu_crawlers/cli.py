"""Command line: ``python -m konu_crawlers --help``."""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import REGISTRY, BlockedError, CaptchaRequired, FetchError, Fetcher, get_crawler
from .output import Writer, out_path


def _options(pairs: list[str]) -> dict[str, str]:
    out = {}
    for p in pairs:
        if "=" not in p:
            raise SystemExit(f"--opt expects key=value, got {p!r}")
        k, v = p.split("=", 1)
        out[k.strip().replace("-", "_")] = v
    return out


def cmd_list(_: argparse.Namespace) -> int:
    print(f"{'source':<14} {'types':<38} notes")
    for name in sorted(REGISTRY):
        c = REGISTRY[name]
        notes = []
        if c.interactive:
            notes.append("captcha typed by operator")
        if c.needs_browser:
            notes.append("uses headless Chromium")
        print(f"{name:<14} {', '.join(c.transactions):<38} {c.title}{' – ' + '; '.join(notes) if notes else ''}")
    return 0


def cmd_crawl(a: argparse.Namespace) -> int:
    names = sorted(REGISTRY) if a.source == ["all"] else a.source
    for n in names:
        if n not in REGISTRY:
            raise SystemExit(f"unknown source {n!r}; run `python -m konu_crawlers list`")
    if len(names) > 1 and a.out and "{source}" not in a.out:
        raise SystemExit("with several sources, --out must contain {source}, e.g. data/{source}.jsonl")
    opts = _options(a.opt or [])
    if a.captcha:
        opts["captcha"] = a.captcha
    fetcher = Fetcher(delay=a.delay, proxy=a.proxy, respect_robots=not a.ignore_robots, retries=a.retries)
    summary = []
    try:
        for n in names:
            crawler = get_crawler(n, fetcher=fetcher, render=a.render)
            ttype = a.type if a.type in crawler.transactions else None
            if a.type and ttype is None and len(names) == 1:
                raise SystemExit(f"{n}: --type must be one of {', '.join(crawler.transactions)}")
            w = Writer(out_path(a.out, n), include_raw=not a.no_raw)
            t0 = time.time()
            status = "ok"
            try:
                for rec in crawler.crawl(city=a.city, transaction=ttype, max_pages=a.max_pages, **opts):
                    w.write(rec)
                    if a.limit and w.count >= a.limit:
                        break
            except BlockedError as e:
                status = f"BLOCKED: {e}"
            except CaptchaRequired as e:
                status = f"CAPTCHA: {e}"
            except (FetchError, ValueError) as e:
                status = f"ERROR: {e}"
            except Exception as e:  # noqa: BLE001 - keep going with other sources
                logging.exception("%s crashed", n)
                status = f"ERROR: {type(e).__name__}: {e}"
            finally:
                w.close()
            summary.append((n, ttype or crawler.transactions[0], w.count, time.time() - t0, status))
            print(f"[{n}] {w.count} records in {time.time() - t0:.1f}s – {status}", file=sys.stderr)
    finally:
        fetcher.close()
    if len(summary) > 1:
        print("\nsummary:", file=sys.stderr)
        for n, t, c, dt, st in summary:
            print(f"  {n:<14} {t:<14} {c:>6}  {dt:6.1f}s  {st[:160]}", file=sys.stderr)
    return 0 if any(c for _, _, c, _, _ in summary) else 1


def cmd_doctor(a: argparse.Namespace) -> int:
    from .doctor import run

    return run(a.source, a.out, a.proxy, a.delay, a.interactive, a.captcha)


def cmd_export(a: argparse.Namespace) -> int:
    from .export import load_materials, run

    mats = load_materials(a.materials, a.cement_price, a.steel_price)
    return run(a.inputs or ["data/*.jsonl"], a.out, not a.no_details, a.cache, mats, a.delay, a.proxy,
               [s.strip() for s in a.sources.split(",")] if a.sources else None)


def cmd_schedule(a: argparse.Namespace) -> int:
    from .scheduler import run

    return run(a.config, once=a.once)


def cmd_ingest(a: argparse.Namespace) -> int:
    """Load JSONL files from `crawl` (and cached detail pages) into the scheduler's store."""
    import glob
    import json as _json
    from pathlib import Path

    from .export import _cache_path
    from .models import Record
    from .scheduler import Store, load_config

    cfg = load_config(a.config)
    store = Store(Path(cfg["data_dir"]) / "konu.db")
    n = new = d = 0
    for f in sorted({x for pat in a.inputs for x in glob.glob(pat)}):
        recs = [_json.loads(l) for l in open(f, encoding="utf-8") if l.strip()]
        new += store.upsert(recs)
        n += len(recs)
        for r in recs:
            cp = _cache_path(Path(a.cache), Record(**r))
            if cp.exists():
                store.save_details(Store.key(r), _json.loads(cp.read_text()), "ok")
                d += 1
    print(f"ingested {n} records ({new} new), {d} cached detail pages, into {cfg['data_dir']}/konu.db", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="konu_crawlers", description="Crawl Hyderabad real-estate sources into JSONL/CSV.")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list sources and the --type values each supports").set_defaults(func=cmd_list)
    c = sub.add_parser("crawl", help="crawl one or more sources")
    c.add_argument("source", nargs="+", help="source name(s) or 'all'")
    c.add_argument("--city", default="hyderabad")
    c.add_argument("--type", help="sale | rent | projects | agents | layouts | permissions | ... (see `list`)")
    c.add_argument("--max-pages", type=int, default=1, help="result pages per source (default 1)")
    c.add_argument("--limit", type=int, help="stop after N records per source")
    c.add_argument("--out", help="output file (.jsonl or .csv); may contain {source}. Default: stdout JSONL")
    c.add_argument("--no-raw", action="store_true", help="omit the raw source payload")
    c.add_argument("--delay", type=float, default=1.5, help="seconds between requests to one host (default 1.5)")
    c.add_argument("--retries", type=int, default=3)
    c.add_argument("--proxy", help="HTTP(S) proxy URL, e.g. one in India for geo-restricted sites (or KONU_PROXY)")
    c.add_argument("--render", action=argparse.BooleanOptionalAction, default=None, help="force/disable headless-browser fetching")
    c.add_argument("--ignore-robots", action="store_true", help="do not consult robots.txt")
    c.add_argument("--captcha", help="captcha text for interactive sources (if you read the image yourself)")
    c.add_argument("--opt", action="append", metavar="KEY=VALUE", help="source-specific option (repeatable), e.g. --opt district=Hyderabad")
    c.set_defaults(func=cmd_crawl)
    d = sub.add_parser("doctor", help="one page per source/type, saving every raw response + report.json into a zip to send back")
    d.add_argument("source", nargs="*", help="source name(s); default all")
    d.add_argument("--out", help="folder to write (default konu-doctor-<timestamp>)")
    d.add_argument("--proxy", help="HTTP(S) proxy URL (or KONU_PROXY)")
    d.add_argument("--delay", type=float, default=1.5)
    d.add_argument("--interactive", action="store_true", help="prompt for TS-RERA / IGRS / Bhu Bharati captchas")
    d.add_argument("--captcha", help="TS-RERA captcha text, if you read the image yourself")
    d.set_defaults(func=cmd_doctor)
    e = sub.add_parser("export", help="every crawled record (listings, projects, agents, layouts, ...) -> one combined CSV")
    e.add_argument("inputs", nargs="*", help="JSONL files/globs from `crawl` (default data/*.jsonl)")
    e.add_argument("--out", default="data/hyderabad_records.csv")
    e.add_argument("--sources", help="comma-separated sources to include (default all)")
    e.add_argument("--no-details", action="store_true", help="skip listing detail pages (faster, fewer columns filled)")
    e.add_argument("--cache", default="data/details_cache", help="where detail-page facts are cached")
    e.add_argument("--materials", help="CSV with YearMonth,cement,steel for the material-price columns")
    e.add_argument("--cement-price", type=float, help="flat cement price (Rs per 50 kg bag) for every row")
    e.add_argument("--steel-price", type=float, help="flat TMT steel price (Rs per tonne) for every row")
    e.add_argument("--delay", type=float, default=1.5)
    e.add_argument("--proxy")
    e.set_defaults(func=cmd_export)
    sc = sub.add_parser("schedule", help="run continuously: crawl on per-source intervals, keep data/hyderabad_records.csv current")
    sc.add_argument("--config", default="config/schedule.json")
    sc.add_argument("--once", action="store_true", help="run every due job once, update the CSV, then exit")
    sc.set_defaults(func=cmd_schedule)
    ig = sub.add_parser("ingest", help="load crawl JSONL files into the scheduler's store")
    ig.add_argument("inputs", nargs="+")
    ig.add_argument("--config", default="config/schedule.json")
    ig.add_argument("--cache", default="data/details_cache")
    ig.set_defaults(func=cmd_ingest)
    a = p.parse_args(argv)
    level = logging.DEBUG if a.verbose else logging.INFO if a.cmd == "schedule" else logging.WARNING
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return a.func(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
