"""``python -m konu_crawlers doctor``: a diagnostic run to send back.

Runs every crawler (or the ones named) for one page of each ``--type``,
saves every raw response it received, and writes ``report.json`` with the
outcome per source/type plus a few sample records. The folder is zipped so
it can be shared in one file. Nothing is solved or bypassed: blocks and
captchas are recorded as they are.
"""

from __future__ import annotations

import datetime as dt
import json
import platform
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from . import REGISTRY, BlockedError, CaptchaRequired, FetchError, get_crawler
from .http import Fetcher, Response

MAX_SAVED_BYTES = 5_000_000  # per response; HMDA's registers are larger


class RecordingFetcher(Fetcher):
    """A Fetcher that also writes every response it gets to disk."""

    def __init__(self, folder: Path, **kw: Any) -> None:
        super().__init__(**kw)
        self.folder = folder
        self.log: list[dict] = []

    def _save(self, method: str, url: str, resp: Response | None, error: str | None) -> None:
        n = len(self.log) + 1
        entry: dict[str, Any] = {"n": n, "method": method, "url": url, "error": error}
        if resp is not None:
            body = resp.content or resp.text.encode("utf-8", "replace")
            ctype = resp.headers.get("Content-Type", resp.headers.get("content-type", ""))
            ext = ".png" if "image" in ctype else ".json" if "json" in ctype else ".html"
            name = f"{n:03d}_{re.sub(r'[^A-Za-z0-9]+', '_', url.split('://', 1)[-1])[:80]}{ext}"
            (self.folder / name).write_bytes(body[:MAX_SAVED_BYTES])
            entry.update({"final_url": resp.url, "status": resp.status, "bytes": len(body), "truncated": len(body) > MAX_SAVED_BYTES,
                          "file": name, "headers": {k: v for k, v in resp.headers.items()
                                                    if k.lower() in ("server", "content-type", "location", "akamai-grn", "x-cache", "cf-ray")}})
        self.log.append(entry)

    def request(self, method: str, url: str, *, check: bool = True, **kw: Any) -> Response:  # type: ignore[override]
        try:
            resp = super().request(method, url, check=False, **kw)
        except Exception as e:  # noqa: BLE001 - record and re-raise
            self._save(method, url, None, f"{type(e).__name__}: {e}")
            raise
        self._save(method, url, resp, None)
        if check:
            self.check_blocked(resp)
        return resp

    def render(self, url: str, **kw: Any) -> Response:  # type: ignore[override]
        try:
            resp = super().render(url, **kw)
        except BlockedError as e:
            self._save("RENDER", url, None, f"BlockedError: {e}")
            raise
        except Exception as e:  # noqa: BLE001
            self._save("RENDER", url, None, f"{type(e).__name__}: {e}")
            raise
        self._save("RENDER", url, resp, None)
        return resp


def _network_info(fetcher: Fetcher) -> dict:
    """Which country the sites see us in, via Cloudflare's public trace page."""
    try:
        r = fetcher.session.get("https://www.cloudflare.com/cdn-cgi/trace", timeout=15, verify=fetcher.verify)
        kv = dict(line.split("=", 1) for line in r.text.splitlines() if "=" in line)
        return {"country": kv.get("loc"), "ip_kind": "ipv6" if ":" in kv.get("ip", "") else "ipv4"}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


def run(sources: list[str], out: str, proxy: str | None, delay: float, interactive: bool, captcha: str | None) -> int:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    root = Path(out or f"konu-doctor-{stamp}")
    root.mkdir(parents=True, exist_ok=True)
    names = sorted(REGISTRY) if not sources or sources == ["all"] else sources
    for n in names:
        if n not in REGISTRY:
            raise SystemExit(f"unknown source {n!r}; run `python -m konu_crawlers list`")
    if not interactive:
        import os

        os.environ["KONU_NONINTERACTIVE"] = "1"
    report: dict[str, Any] = {
        "created": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "proxy": bool(proxy),
        "runs": [],
    }
    probe = Fetcher(proxy=proxy, respect_robots=False)
    report["network"] = _network_info(probe)
    print(f"network: {report['network']}", file=sys.stderr)
    for name in names:
        cls = REGISTRY[name]
        for t in cls.transactions:
            folder = root / f"{name}__{t}"
            folder.mkdir(exist_ok=True)
            f = RecordingFetcher(folder, delay=delay, proxy=proxy, retries=1)
            crawler = get_crawler(name, fetcher=f)
            opts = {"captcha": captcha} if captcha and name == "ts_rera" else {}
            t0 = time.time()
            recs, outcome, detail = [], "ok", ""
            try:
                for rec in crawler.crawl(city="hyderabad", transaction=t, max_pages=1, **opts):
                    recs.append(rec)
                    if len(recs) >= 50:
                        break
                if not recs:
                    outcome = "empty"
            except BlockedError as e:
                outcome, detail = "blocked", str(e)
            except CaptchaRequired as e:
                outcome, detail = "captcha", str(e)
            except (FetchError, ValueError) as e:
                outcome, detail = "error", str(e)
            except Exception as e:  # noqa: BLE001 - a parser crash is exactly what we want to see
                outcome, detail = "crash", f"{type(e).__name__}: {e}"
            finally:
                f.close()
            filled = {}
            for field in ("price", "area_sqft", "bedrooms", "locality", "latitude", "url", "title"):
                filled[field] = sum(1 for r in recs if getattr(r, field) not in (None, ""))
            run_info = {
                "source": name, "type": t, "outcome": outcome, "detail": detail[:500], "records": len(recs),
                "seconds": round(time.time() - t0, 1), "fields_filled": filled, "requests": f.log,
                "sample": [r.to_dict(include_raw=False) for r in recs[:3]],
            }
            report["runs"].append(run_info)
            (folder / "records.jsonl").write_text("".join(json.dumps(r.to_dict(), ensure_ascii=False, default=str) + "\n" for r in recs))
            print(f"[{name} {t}] {outcome:<8} {len(recs):>3} records  {detail[:110]}", file=sys.stderr)
    (root / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    summary = {}
    for r in report["runs"]:
        summary.setdefault(r["source"], []).append(f"{r['type']}={r['outcome']}({r['records']})")
    print("\nsummary:", file=sys.stderr)
    for s, parts in summary.items():
        print(f"  {s:<14} {'  '.join(parts)}", file=sys.stderr)
    archive = shutil.make_archive(str(root), "zip", root_dir=root)
    print(f"\nSaved {root}/ and {archive} – send the .zip back for parser fixes.", file=sys.stderr)
    return 0
