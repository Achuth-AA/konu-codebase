"""Reusable crawler shapes.

``PortalCrawler``  – paginated search-result pages on a listing portal.
                     Extraction falls through: site JSON → JSON-LD → cards.
``TableDiscoveryCrawler`` – government sites that publish registers as HTML
                     tables: start pages are discovered by link keywords,
                     every data table found is emitted row-by-row.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, ClassVar, Iterator

from bs4 import BeautifulSoup

from ..base import Crawler, slug
from ..captcha import ask_human
from ..extract import clean_text, form_fields, table_rows
from ..http import BlockedError, absolute
from ..models import Record
from ._common import records_from_cards, records_from_json, records_from_ld

log = logging.getLogger(__name__)


class PortalCrawler(Crawler):
    #: transaction -> URL template; ``{city}`` is the slug, ``{City}`` title-case.
    urls: ClassVar[dict[str, str]] = {}
    #: page N URL from page-1 URL; default appends ``?page=N``.
    page_url: ClassVar[Callable[[str, int], str] | None] = None
    #: predicate selecting listing dicts inside embedded JSON.
    json_predicate: ClassVar[Callable[[dict], bool] | None] = None
    card_selector: ClassVar[str | None] = None

    def map_json(self, d: dict, city: str, transaction: str) -> Record | None:  # pragma: no cover - overridden
        return None

    def json_key(self, d: dict) -> object:
        return d.get("id") or id(d)

    def start_url(self, city: str, transaction: str, options: dict) -> str:
        return self.urls[transaction].format(city=slug(city), City=city.strip().title().replace(" ", "-"), **options)

    def next_url(self, first_url: str, page: int) -> str:
        if type(self).page_url is not None:
            return type(self).page_url(first_url, page)  # type: ignore[misc]
        sep = "&" if "?" in first_url else "?"
        return f"{first_url}{sep}page={page}"

    def parse(self, html: str, url: str, city: str, transaction: str) -> list[Record]:
        recs: list[Record] = []
        pred = type(self).json_predicate
        if pred is not None:
            recs = records_from_json(self.name, html, pred, lambda d: self.map_json(d, city, transaction), self.json_key)
        if not recs:
            recs = records_from_ld(self.name, html, transaction, city)
        if not recs:
            recs = records_from_cards(self.name, html, url, transaction, city, self.card_selector)
        for r in recs:
            r.transaction = r.transaction or transaction
            r.city = r.city or city
        return recs

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        first = self.start_url(city, t, options)
        seen: set[str] = set()
        for page in range(1, max_pages + 1):
            url = first if page == 1 else self.next_url(first, page)
            resp = self.page(url)
            recs = [r for r in self.parse(resp.text, resp.url, slug(city), t) if (r.source_id or r.url or r.title) not in seen]
            if not recs:
                if page == 1:
                    log.warning("%s: no listings found on %s (layout changed or empty result)", self.name, url)
                break
            for r in recs:
                seen.add(r.source_id or r.url or r.title or "")
                yield r


class TableDiscoveryCrawler(Crawler):
    """Find register pages by link text, then dump their tables."""

    #: transaction -> list of (keywords regex, max depth) used to discover pages
    discover: ClassVar[dict[str, list[str]]] = {}
    #: transaction -> explicit seed URLs tried before discovery
    seeds: ClassVar[dict[str, list[str]]] = {}
    record_kind: ClassVar[dict[str, str]] = {}
    min_rows: ClassVar[int] = 3

    def find_pages(self, transaction: str, limit: int) -> list[str]:
        pages = list(self.seeds.get(transaction, []))
        pats = [re.compile(p, re.I) for p in self.discover.get(transaction, [])]
        if not pats:
            return pages
        try:
            home = self.page(self.homepage)
        except BlockedError:
            if pages:
                return pages
            raise
        soup = BeautifulSoup(home.text, "lxml")
        for a in soup.find_all("a", href=True):
            label = f"{a.get_text(' ', strip=True)} {a.get('title', '')} {a['href']}"
            if any(p.search(label) for p in pats):
                u = absolute(home.url, a["href"])
                if u and u not in pages:
                    pages.append(u)
        return pages[: max(limit, len(self.seeds.get(transaction, [])))]

    def rows_to_records(self, rows: list[dict], url: str, transaction: str, city: str) -> list[Record]:
        out = []
        kind = self.record_kind.get(transaction, transaction)
        for i, row in enumerate(rows):
            vals = [v for k, v in row.items() if not k.endswith("__href") and v]
            if len(vals) < 2:
                continue
            href = next((v for k, v in row.items() if k.endswith("__href") and not v.startswith("javascript")), None)
            out.append(
                Record(
                    source=self.name,
                    kind=kind,
                    source_id=f"{url}#{i}",
                    url=absolute(url, href) if href else url,
                    title=clean_text(max(vals, key=len))[:300] if vals else None,
                    city=city,
                    raw={**row, "page": url},
                )
            )
        return out

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 5, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        urls = [options["url"]] if options.get("url") else self.find_pages(t, max_pages)
        if not urls:
            log.warning("%s: no %s pages discovered from %s", self.name, t, self.homepage)
        for url in urls[:max_pages]:
            resp = self.page(url)
            soup = BeautifulSoup(resp.text, "lxml")
            if soup.find("img", src=re.compile("captcha", re.I)) and options.get("submit"):
                img = soup.find("img", src=re.compile("captcha", re.I))
                data = form_fields(soup)
                data.update({k[5:]: v for k, v in options.items() if k.startswith("form_")})
                cap = self.fetcher.get(absolute(resp.url, img["src"]) or "", check=False)
                field = next((k for k in data if "captcha" in k.lower()), "captcha")
                data[field] = ask_human(cap.content, self.name, provided=options.get("captcha"))
                form = soup.find("form")
                action = absolute(resp.url, form.get("action")) if form and form.get("action") else resp.url
                soup = BeautifulSoup(self.fetcher.post(action or resp.url, data=data).text, "lxml")
            for table in soup.find_all("table"):
                if table.find("table"):
                    continue
                rows = table_rows(table)
                if len(rows) >= self.min_rows:
                    yield from self.rows_to_records(rows, resp.url, t, slug(city))
