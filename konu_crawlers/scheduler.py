"""Continuous crawling: ``python -m konu_crawlers schedule``.

A long-running loop for a server. Each (source, type) pair in the schedule
config is a job with its own interval. When a job is due it runs the normal
``crawl`` command in a child process with a hard timeout (a hung site or
browser is killed, not waited on), the records are upserted into a SQLite
store (keyed by the site's id or URL), detail pages are fetched for new listings within
a time budget, and the single combined CSV (every record of every kind:
listings, projects, agents, layouts, permissions, ...) is rewritten atomically.

Sites that block us (geo / WAF) are retried after ``blocked_retry_hours``
instead of every cycle; captcha-gated sources are skipped (they need a
person). Progress is written to ``status.json`` next to the CSV and logged
to stdout for journald / Docker.
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from .details import fetch_details
from .export import BROWSER_SOURCES, load_materials
from .http import BlockedError, Fetcher
from .models import Record
from .schema import CSV_COLUMNS, record_key, to_csv_row

log = logging.getLogger("konu.scheduler")

DEFAULT_CONFIG: dict[str, Any] = {
    "city": "hyderabad",
    "data_dir": "data",
    "csv": "data/hyderabad_records.csv",
    "delay_seconds": 2.0,
    "blocked_retry_hours": 12,
    "detail_budget_minutes": 20,
    "detail_max_age_days": 30,
    "drop_unseen_after_days": 45,
    "materials_csv": None,
    "cement_price": None,
    "steel_price": None,
    "proxy": None,
    "gcs_bucket": None,
    "gcs_object": "hyderabad_records.csv",
    "jobs": [],
}

_STOP = False


def _stop(signum: int, _frame: Any) -> None:
    global _STOP
    _STOP = True
    log.info("signal %s received: finishing the current step, then exiting", signum)


def now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime) -> str:
    return t.isoformat(timespec="seconds")


class Store:
    """SQLite: every record ever seen (latest copy), detail facts, job runs."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS records (
                key TEXT PRIMARY KEY, source TEXT, kind TEXT, transaction_type TEXT,
                json TEXT, first_seen TEXT, last_seen TEXT);
            CREATE TABLE IF NOT EXISTS details (
                key TEXT PRIMARY KEY, json TEXT, fetched_at TEXT, status TEXT);
            CREATE TABLE IF NOT EXISTS runs (
                job TEXT, started TEXT, finished TEXT, status TEXT, records INTEGER, detail TEXT);
            CREATE INDEX IF NOT EXISTS runs_job ON runs(job, started);
            """
        )

    key = staticmethod(record_key)

    def upsert(self, recs: list[dict]) -> int:
        """Insert or refresh records; returns how many were new."""
        t = iso(now())
        new = 0
        for r in recs:
            key = self.key(r)
            cur = self.db.execute("SELECT 1 FROM records WHERE key=?", (key,)).fetchone()
            if cur:
                self.db.execute("UPDATE records SET json=?, last_seen=? WHERE key=?", (json.dumps(r, ensure_ascii=False, default=str), t, key))
            else:
                new += 1
                self.db.execute(
                    "INSERT INTO records VALUES (?,?,?,?,?,?,?)",
                    (key, r["source"], r["kind"], r.get("transaction"), json.dumps(r, ensure_ascii=False, default=str), t, t),
                )
        self.db.commit()
        return new

    def last_run(self, job: str) -> tuple[str, str] | None:
        row = self.db.execute("SELECT started, status FROM runs WHERE job=? ORDER BY started DESC LIMIT 1", (job,)).fetchone()
        return (row[0], row[1]) if row else None

    def log_run(self, job: str, started: dt.datetime, status: str, records: int, detail: str) -> None:
        self.db.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)", (job, iso(started), iso(now()), status, records, detail[:2000]))
        self.db.commit()

    def listings_needing_details(self, max_age_days: int) -> list[tuple[str, dict]]:
        cutoff = iso(now() - dt.timedelta(days=max_age_days))
        rows = self.db.execute(
            """SELECT r.key, r.json FROM records r LEFT JOIN details d ON d.key = r.key
               WHERE r.kind='listing' AND (d.key IS NULL OR d.fetched_at < ?)
               ORDER BY r.first_seen DESC""",
            (cutoff,),
        ).fetchall()
        return [(k, json.loads(j)) for k, j in rows]

    def save_details(self, key: str, d: dict, status: str) -> None:
        self.db.execute("INSERT OR REPLACE INTO details VALUES (?,?,?,?)", (key, json.dumps(d, ensure_ascii=False, default=str), iso(now()), status))
        self.db.commit()

    def all_rows(self, drop_after_days: int) -> list[tuple[dict, dict]]:
        """Every record of every kind seen in the last ``drop_after_days``, with its detail facts."""
        cutoff = iso(now() - dt.timedelta(days=drop_after_days))
        rows = self.db.execute(
            """SELECT r.json, d.json FROM records r LEFT JOIN details d ON d.key = r.key
               WHERE r.last_seen >= ?
               ORDER BY r.source, r.kind, r.first_seen""",
            (cutoff,),
        ).fetchall()
        return [(json.loads(a), json.loads(b) if b else {}) for a, b in rows]


def job_name(j: dict) -> str:
    return f"{j['source']}:{j['type']}"


def is_due(store: Store, j: dict, cfg: dict) -> bool:
    last = store.last_run(job_name(j))
    if not last:
        return True
    started, status = last
    wait_h = cfg["blocked_retry_hours"] if status in ("BLOCKED", "CAPTCHA") else j["every_hours"]
    return now() - dt.datetime.fromisoformat(started) >= dt.timedelta(hours=wait_h)


def run_job(store: Store, j: dict, cfg: dict) -> None:
    """Run one crawl in a child process with a hard timeout, then store results."""
    name = job_name(j)
    started = now()
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "out.jsonl"
        cmd = [sys.executable, "-m", "konu_crawlers", "crawl", j["source"], "--type", j["type"],
               "--city", cfg["city"], "--max-pages", str(j.get("max_pages", 3)), "--out", str(out),
               "--delay", str(cfg["delay_seconds"])]
        if cfg.get("proxy"):
            cmd += ["--proxy", cfg["proxy"]]
        env = {**os.environ, "KONU_NONINTERACTIVE": "1"}
        timeout = j.get("timeout_minutes", 20) * 60
        log.info("%s: starting (timeout %d min)", name, timeout // 60)
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
            summary = next((l for l in reversed(proc.stderr.splitlines()) if l.startswith(f"[{j['source']}]")), proc.stderr[-300:])
            status = "OK"
            for tag in ("BLOCKED", "CAPTCHA", "ERROR"):
                if f"– {tag}" in summary:
                    status = tag
        except subprocess.TimeoutExpired:
            summary, status = f"killed after {timeout // 60} min", "TIMEOUT"
        recs = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines() if l.strip()] if out.exists() else []
    new = store.upsert(recs)
    if recs and status != "OK":
        status = f"PARTIAL-{status}"  # e.g. robots.txt stopped page 2 after page 1 was saved
    store.log_run(name, started, status, len(recs), summary)
    log.info("%s: %s, %d records (%d new) in %.0fs – %s", name, status, len(recs), new,
             (now() - started).total_seconds(), summary[-200:])


def enrich(store: Store, cfg: dict) -> None:
    """Fetch detail pages for listings that lack them, within the time budget."""
    todo = store.listings_needing_details(cfg["detail_max_age_days"])
    if not todo:
        return
    deadline = time.monotonic() + cfg["detail_budget_minutes"] * 60
    blocked: set[str] = set()
    done = 0
    fetcher = Fetcher(delay=cfg["delay_seconds"], proxy=cfg.get("proxy"), retries=1, timeout=40)
    try:
        for key, r in todo:
            if _STOP or time.monotonic() > deadline:
                break
            if r["source"] in blocked:
                continue
            rec = Record(**r)
            try:
                d = fetch_details(rec, fetcher, render=rec.source in BROWSER_SOURCES)
                store.save_details(key, d, "ok")
            except BlockedError:
                blocked.add(rec.source)
                store.save_details(key, {}, "blocked")
            except Exception as e:  # noqa: BLE001 - one bad page must not stop the loop
                log.debug("details failed for %s: %s", key, e)
                store.save_details(key, {}, "error")
            done += 1
    finally:
        fetcher.close()
    log.info("details: fetched %d of %d pending%s", done, len(todo), f" (blocked: {', '.join(sorted(blocked))})" if blocked else "")


def write_csv(store: Store, cfg: dict) -> int:
    materials = load_materials(cfg.get("materials_csv"), cfg.get("cement_price"), cfg.get("steel_price"))
    path = Path(cfg["csv"])
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for r, d in store.all_rows(cfg["drop_unseen_after_days"]):
        rows.append(to_csv_row(Record(**r), d, materials))
    tmp = path.with_suffix(".csv.tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)  # readers never see a half-written file
    if cfg.get("gcs_bucket"):
        upload_to_gcs(path, cfg["gcs_bucket"], cfg.get("gcs_object") or path.name)
    return len(rows)


def upload_to_gcs(path: Path, bucket: str, name: str) -> None:
    """Upload with the VM's service account (metadata server token); no SDK needed."""
    import requests
    from urllib.parse import quote

    try:
        tok = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token",
            headers={"Metadata-Flavor": "Google"}, timeout=10,
        ).json()["access_token"]
        r = requests.post(
            f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o?uploadType=media&name={quote(name, safe='')}",
            headers={"Authorization": f"Bearer {tok}", "Content-Type": "text/csv"},
            data=path.read_bytes(), timeout=120,
        )
        r.raise_for_status()
        log.info("uploaded %s to gs://%s/%s", path.name, bucket, name)
    except Exception as e:  # noqa: BLE001 - the local CSV is still written
        log.warning("GCS upload failed: %s", e)


def write_status(store: Store, cfg: dict, rows: int) -> None:
    jobs = {}
    for j in cfg["jobs"]:
        r = store.db.execute(
            "SELECT started, finished, status, records, detail FROM runs WHERE job=? ORDER BY started DESC LIMIT 1", (job_name(j),)
        ).fetchone()
        jobs[job_name(j)] = dict(zip(("started", "finished", "status", "records", "detail"), r)) if r else None
    status = {"updated": iso(now()), "csv": cfg["csv"], "csv_rows": rows, "jobs": jobs}
    Path(cfg["data_dir"], "status.json").write_text(json.dumps(status, indent=2))


def load_config(path: str | None) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if path:
        cfg.update(json.loads(Path(path).read_text()))
    for k in ("proxy", "gcs_bucket"):
        cfg[k] = os.environ.get(f"KONU_{k.upper()}") or cfg.get(k)
    return cfg


def run(config: str | None, once: bool = False) -> int:
    cfg = load_config(config)
    if not cfg["jobs"]:
        raise SystemExit("schedule config has no jobs (see config/schedule.json)")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    store = Store(Path(cfg["data_dir"]) / "konu.db")
    log.info("scheduler started: %d jobs, CSV %s", len(cfg["jobs"]), cfg["csv"])
    while not _STOP:
        due = [j for j in cfg["jobs"] if j.get("enabled", True) and is_due(store, j, cfg)]
        for j in due:
            if _STOP:
                break
            run_job(store, j, cfg)
        if due and not _STOP:
            enrich(store, cfg)
            rows = write_csv(store, cfg)
            write_status(store, cfg, rows)
            log.info("CSV updated: %d records in %s", rows, cfg["csv"])
        if once:
            break
        for _ in range(60):  # check for due jobs once a minute, wake fast on SIGTERM
            if _STOP:
                break
            time.sleep(1)
    log.info("scheduler stopped")
    return 0
