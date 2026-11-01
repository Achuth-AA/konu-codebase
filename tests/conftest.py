import gzip
from pathlib import Path

import pytest

from konu_crawlers.http import Fetcher, Response

FIXTURES = Path(__file__).with_name("fixtures")


def load(name: str) -> str:
    p = FIXTURES / name
    if p.suffix == ".gz":
        return gzip.decompress(p.read_bytes()).decode("utf-8", errors="replace")
    gz = p.with_name(p.name + ".gz")
    if gz.exists():
        return gzip.decompress(gz.read_bytes()).decode("utf-8", errors="replace")
    return p.read_text(encoding="utf-8")


class FakeFetcher(Fetcher):
    """Serves canned pages; records every request made."""

    def __init__(self, pages: dict, default: str = "<html></html>") -> None:
        super().__init__(delay=0, respect_robots=False)
        self.pages = pages
        self.default = default
        self.calls: list[tuple[str, str, dict]] = []

    def _lookup(self, url: str, params: dict | None):
        if params:
            from urllib.parse import urlencode

            url = f"{url}?{urlencode(params)}"
        for key, val in self.pages.items():
            if key == url or (key.endswith("*") and url.startswith(key[:-1])):
                return val
        return self.default

    def request(self, method, url, *, check=True, **kw):  # type: ignore[override]
        self.calls.append((method, url, kw))
        val = self._lookup(url, kw.get("params"))
        if callable(val):
            val = val(method, url, kw)
        if isinstance(val, Response):
            resp = val
        elif isinstance(val, bytes):
            resp = Response(url, 200, "", {}, val)
        else:
            resp = Response(url, 200, val, {}, val.encode())
        if check:
            self.check_blocked(resp)
        return resp

    def render(self, url, **kw):  # type: ignore[override]
        return self.request("GET", url)


@pytest.fixture
def fake():
    return FakeFetcher
