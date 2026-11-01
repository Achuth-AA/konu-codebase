"""Scheduler: due/backoff logic, hard timeouts, CSV output."""

import csv
import datetime as dt
import json
import subprocess

from konu_crawlers import scheduler as S
from konu_crawlers.schema import COLUMNS, CSV_COLUMNS


def cfg(tmp_path, **kw):
    c = S.load_config(None)
    c.update(data_dir=str(tmp_path), csv=str(tmp_path / "out.csv"), jobs=[], **kw)
    return c


def test_due_backoff_and_interval(tmp_path):
    c = cfg(tmp_path, blocked_retry_hours=12)
    st = S.Store(tmp_path / "k.db")
    job = {"source": "99acres", "type": "sale", "every_hours": 6}
    assert S.is_due(st, job, c)
    st.log_run("99acres:sale", S.now() - dt.timedelta(hours=7), "BLOCKED", 0, "")
    assert not S.is_due(st, job, c)  # blocked: wait 12h, not 6h
    st.log_run("99acres:sale", S.now() - dt.timedelta(hours=7), "OK", 5, "")
    assert S.is_due(st, job, c)


def test_hung_crawl_is_killed_and_logged(tmp_path, monkeypatch):
    c = cfg(tmp_path)
    st = S.Store(tmp_path / "k.db")

    def hang(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="crawl", timeout=kw["timeout"])

    monkeypatch.setattr(S.subprocess, "run", hang)
    S.run_job(st, {"source": "quikr", "type": "sale", "every_hours": 6, "timeout_minutes": 1}, c)
    assert st.last_run("quikr:sale")[1] == "TIMEOUT"


def test_upsert_dedupes_and_csv_has_every_record(tmp_path):
    c = cfg(tmp_path, cement_price=380, steel_price=58000)
    st = S.Store(tmp_path / "k.db")
    rec = {"source": "realestateindia", "kind": "listing", "source_id": "1", "url": "https://x/1", "title": "2 BHK Flat for Rent in Miyapur",
           "transaction": "rent", "price": 41000.0, "area_sqft": 1277.0, "bedrooms": 2.0, "locality": "Miyapur", "city": "hyderabad",
           "posted_at": "21 Oct, 2026"}
    project = {**rec, "kind": "project", "source_id": "p1", "url": "https://x/p"}
    assert st.upsert([rec, project]) == 2
    assert st.upsert([{**rec, "price": 42000.0}]) == 0  # same URL: updated, not duplicated
    st.save_details(S.Store.key(rec), {"floor": "10th", "total_floors": "15", "balconies": "2", "facing": "East"}, "ok")
    layout = {"source": "hmda", "kind": "layout", "source_id": "LO/1", "url": "https://hmda/LO.aspx", "title": "Sy. No. 12, Kollur",
              "raw": {"File_no": "LO/1", "Extent": "2.5 Acres", "Remarks": "ok"}}
    st.upsert([layout])
    assert S.write_csv(st, c) == 3  # listing + project + layout: every kind goes into the CSV
    rows = list(csv.DictReader(open(c["csv"])))
    assert list(rows[0]) == CSV_COLUMNS and CSV_COLUMNS[:len(COLUMNS)] == COLUMNS
    by_type = {r["Record_Type"]: r for r in rows}
    assert set(by_type) == {"listing", "project", "layout"}
    assert by_type["layout"]["Source"] == "hmda" and by_type["layout"]["Source_ID"] == "LO/1"
    assert by_type["layout"]["Other_Details"] == "File_no: LO/1 | Extent: 2.5 Acres | Remarks: ok"
    r = by_type["listing"]
    assert (r["Price_INR"], r["Floor_Number"], r["Balconies"], r["Facing"], r["YearMonth"], r["Cement_Price_Rs_per_bag_50kg"]) == \
        ("42000", "10.0", "2.0", "East", "2026-10", "380")


def test_rows_sharing_a_page_url_stay_separate(tmp_path):
    st = S.Store(tmp_path / "k.db")
    rows = [{"source": "hmda", "kind": "layout", "source_id": f"LO/{i}", "url": "https://hmda/LO.aspx"} for i in range(3)]
    assert st.upsert(rows) == 3
