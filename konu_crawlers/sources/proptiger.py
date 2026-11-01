"""PropTiger – proptiger.com (new projects).

PropTiger is project-centric. Every project card carries a
``<script type="text/x-config">`` JSON with projectId, name, min/max price,
builder and address. The city overview page lists ~40 projects; each project
page adds lat/long and links "similar projects", so with ``--max-pages N`` we
snowball through up to N pages (city page first, then project pages) to grow
coverage. The SRP pages (``/apartments-flats-sale``) are client-rendered and
not used.
"""

from __future__ import annotations

from typing import Iterator

from ..base import Crawler, register, slug
from ..extract import clean_text, first, parse_inr, parse_number
from ..http import absolute
from ..models import Record
from ._common import records_from_json

BASE = "https://www.proptiger.com"


def map_project(d: dict, city: str | None = None) -> Record:
    addr = d.get("address") or ""
    builder = d.get("builder") if isinstance(d.get("builder"), dict) else {}
    return Record(
        source="proptiger",
        kind="project",
        source_id=str(d.get("projectId")),
        url=absolute(BASE + "/", d.get("URL")),
        title=clean_text(d.get("name")),
        transaction="sale",
        price=parse_inr(d.get("minPrice")) or None,
        price_max=parse_inr(d.get("maxPrice")) or None,
        locality=clean_text(d.get("localityLabel")) or (clean_text(addr.split(",")[0]) if addr else None),
        city=clean_text(d.get("cityName")) or city,
        address=clean_text(addr),
        latitude=parse_number(d.get("latitude")),
        longitude=parse_number(d.get("longitude")),
        project_name=clean_text(d.get("name")),
        builder=clean_text(first(builder, "displayName", "name")),
        posted_by="Builder",
        image_url=d.get("imageURL"),
        raw=d,
    )


def parse_page(html: str, city: str | None = None) -> list[Record]:
    return records_from_json(
        "proptiger", html,
        predicate=lambda d: "projectId" in d and ("minPrice" in d or "URL" in d) and d.get("projectId"),
        mapper=lambda d: map_project(d, city),
        key=lambda d: d.get("projectId"),
    )


@register
class PropTiger(Crawler):
    name = "proptiger"
    title = "PropTiger"
    homepage = BASE
    transactions = ("projects",)

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        self.resolve_transaction(transaction)
        c = slug(city)
        best: dict[str, Record] = {}
        queue = [f"{BASE}/{c}-real-estate"]
        fetched: set[str] = set()
        emitted: set[str] = set()
        while queue and len(fetched) < max_pages:
            url = queue.pop(0)
            if url in fetched:
                continue
            fetched.add(url)
            for rec in parse_page(self.page(url).text, c):
                if not rec.url or f"/{c}/" not in rec.url:
                    continue
                prev = best.get(rec.source_id or "")
                # Project pages carry lat/long that the city cards lack: keep the richer copy.
                if prev is None or (rec.latitude and not prev.latitude):
                    best[rec.source_id or ""] = rec
                if rec.url not in fetched and rec.url not in queue:
                    queue.append(rec.url)
        for pid, rec in best.items():
            if pid not in emitted:
                emitted.add(pid)
                yield rec
