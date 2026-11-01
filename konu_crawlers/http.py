"""HTTP layer: polite fetching with retries, robots.txt, rate-limits and an
optional headless-browser backend for JavaScript-only pages."""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)

_CERT_DIR = Path(__file__).with_name("certs")


class FetchError(RuntimeError):
    pass


class BlockedError(FetchError):
    """The site refused us (WAF, geo-block, maintenance wall, robots.txt).

    Crawlers let this propagate so the CLI can report *why* a source produced
    nothing instead of silently writing an empty file.
    """


class CaptchaRequired(FetchError):
    pass


_BLOCK_SIGNATURES = [
    (re.compile(r"<title>\s*Access Denied\s*</title>", re.I), "Akamai 'Access Denied'"),
    (re.compile(r"<title>\s*Security Alert\s*</title>", re.I), "Akamai bot-manager 'Security Alert'"),
    (re.compile(r"The requested URL was rejected", re.I), "F5 / Volterra WAF rejection"),
    (re.compile(r"ERROR: The request could not be satisfied", re.I), "CloudFront geo/WAF block"),
    (re.compile(r"<title>\s*We'll be back soon!?\s*</title>", re.I), "maintenance wall (often geo-restricted)"),
    (re.compile(r"cf-browser-verification|<title>\s*Just a moment\.\.\.\s*</title>", re.I), "Cloudflare challenge"),
]

_GEO_REDIRECT = re.compile(r"/(403|404)\?ref=.*country=([A-Z]{2})", re.I)


def ca_bundle_path() -> str | bool:
    """A CA bundle = the environment's normal trust store + bundled extras.

    Several Telangana government hosts (rerait.telangana.gov.in,
    *.hmda.gov.in, ...) serve an incomplete chain that omits the
    "emSign SSL CA - G1" intermediate. Browsers fetch it via AIA; Python does
    not, so we ship it (and its root) in ``certs/`` and append them to
    whatever bundle would otherwise be used.
    """
    base = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if not base:
        try:
            import certifi

            base = certifi.where()
        except ImportError:  # pragma: no cover
            return True
    extras = sorted(_CERT_DIR.glob("*.pem"))
    if not extras:
        return base
    out = Path(tempfile.gettempdir()) / f"konu-crawlers-ca-{os.getpid()}.pem"
    if not out.exists():
        parts = [Path(base).read_text()]
        parts += [p.read_text() for p in extras]
        out.write_text("\n".join(parts))
    return str(out)


@dataclass
class Response:
    url: str
    status: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes = b""

    def json(self) -> Any:
        import json

        return json.loads(self.text)


class Fetcher:
    """Thin wrapper over ``requests`` that every crawler shares.

    * one request per ``delay`` seconds per host (default 1.5s)
    * retries with exponential back-off on network errors / 429 / 5xx
    * honours robots.txt unless ``respect_robots=False``
    * detects WAF / geo-block pages and raises :class:`BlockedError`
    * ``render()`` loads a page in headless Chromium (Playwright) for sites
      whose listing pages are only produced client-side
    """

    def __init__(
        self,
        delay: float = 1.5,
        timeout: float = 45.0,
        retries: int = 3,
        user_agent: str | None = None,
        proxy: str | None = None,
        respect_robots: bool = True,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.delay = delay
        self.timeout = timeout
        self.retries = retries
        self.respect_robots = respect_robots
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent or os.environ.get("KONU_USER_AGENT") or DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-IN,en;q=0.9",
            }
        )
        if extra_headers:
            self.session.headers.update(extra_headers)
        proxy = proxy or os.environ.get("KONU_PROXY")
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})
        self.proxy = proxy
        self.verify = ca_bundle_path()
        self._last: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._lock = threading.Lock()
        self._browser = None
        self._pw = None

    # ------------------------------------------------------------------ utils
    def _throttle(self, host: str) -> None:
        with self._lock:
            wait = self._last.get(host, 0) + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.session.get(root + "/robots.txt", timeout=20, verify=self.verify)
                if r.status_code == 200 and "html" not in r.headers.get("content-type", ""):
                    rp.parse(r.text.splitlines())
                else:
                    rp = None
            except requests.RequestException:
                rp = None
            self._robots[root] = rp
        rp = self._robots[root]
        return True if rp is None else rp.can_fetch(self.session.headers["User-Agent"], url)

    @staticmethod
    def check_blocked(resp: Response) -> None:
        m = _GEO_REDIRECT.search(resp.url)
        if m:
            raise BlockedError(f"{resp.url}: geo-blocked (site redirected to /{m.group(1)} for country={m.group(2)})")
        head = resp.text[:5000]
        for rx, why in _BLOCK_SIGNATURES:
            if rx.search(head):
                raise BlockedError(f"{resp.url}: HTTP {resp.status} – {why}")
        if resp.status in (401, 403, 406, 451):
            raise BlockedError(f"{resp.url}: HTTP {resp.status}")

    # --------------------------------------------------------------- requests
    def request(self, method: str, url: str, *, check: bool = True, **kw: Any) -> Response:
        if not self.allowed(url):
            raise BlockedError(f"{url}: disallowed by robots.txt")
        host = urlparse(url).netloc
        kw.setdefault("timeout", self.timeout)
        kw.setdefault("verify", self.verify)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(host)
            try:
                r = self.session.request(method, url, **kw)
            except requests.RequestException as e:
                last_exc = e
                log.warning("%s %s failed (%s), attempt %d", method, url, e, attempt + 1)
                # A reset during the TLS handshake repeats identically (geo-blocks):
                # retry once, not ``retries`` times.
                if isinstance(e, requests.ConnectionError) and attempt >= 1 and re.search(r"reset|RemoteDisconnected|aborted", str(e)):
                    break
                time.sleep(2 ** (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                last_exc = FetchError(f"{url}: HTTP {r.status_code}")
                retry_after = r.headers.get("Retry-After")
                sleep = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** (attempt + 1)
                log.warning("%s %s -> %s, sleeping %.0fs", method, url, r.status_code, sleep)
                time.sleep(min(sleep, 60))
                continue
            resp = Response(r.url, r.status_code, r.text, dict(r.headers), r.content)
            if check:
                self.check_blocked(resp)
            return resp
        if isinstance(last_exc, requests.ConnectionError):
            raise BlockedError(f"{url}: connection refused/reset ({last_exc}) – the host may be geo-restricted to India")
        raise FetchError(str(last_exc))

    def get(self, url: str, **kw: Any) -> Response:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> Response:
        return self.request("POST", url, **kw)

    # ---------------------------------------------------------------- browser
    def render(self, url: str, wait_ms: int = 4000, wait_for: str | None = None, scrolls: int = 0) -> Response:
        """Load ``url`` in headless Chromium and return the rendered HTML."""
        if not self.allowed(url):
            raise BlockedError(f"{url}: disallowed by robots.txt")
        self._throttle(urlparse(url).netloc)
        if self._browser is None:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as e:  # pragma: no cover
                raise FetchError("render mode needs `pip install playwright` (and `playwright install chromium`)") from e
            self._pw = sync_playwright().start()
            launch: dict[str, Any] = {}
            if self.proxy:
                launch["proxy"] = {"server": self.proxy}
            exe = os.environ.get("KONU_CHROMIUM")
            if exe:
                launch["executable_path"] = exe
            self._browser = self._pw.chromium.launch(**launch)
            self._ctx = self._browser.new_context(
                user_agent=self.session.headers["User-Agent"], locale="en-IN", ignore_https_errors=False
            )
        page = self._ctx.new_page()
        try:
            r = page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=wait_ms + 10000)
                except Exception:  # noqa: BLE001 - selector is a hint, not a requirement
                    pass
            page.wait_for_timeout(wait_ms)
            for _ in range(scrolls):
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1200)
            resp = Response(page.url, r.status if r else 0, page.content())
        finally:
            page.close()
        self.check_blocked(resp)
        return resp

    def close(self) -> None:
        if self._browser is not None:
            self._browser.close()
            self._pw.stop()
            self._browser = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def absolute(base: str, href: str | None) -> str | None:
    if not href or href.startswith(("javascript:", "#", "mailto:", "tel:")):
        return None
    return urljoin(base, href)
