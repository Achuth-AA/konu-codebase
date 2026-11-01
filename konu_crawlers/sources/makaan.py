"""Makaan – makaan.com (buy and rent).

Makaan (same platform as PropTiger) attaches a ``<script type="text/x-config">``
JSON to every listing card with ``listingId``, ``price``, ``size``,
``bedrooms``, ``localityName``, lat/long, seller type... and adds JSON-LD.
Pagination: ``?page=N``.

Makaan has been folded into Housing.com (same owner): as of late 2026
makaan.com answers every search URL with a 301 to
``origin.makaan.com/housing-buyer-redirect``. When that redirect is seen this
crawler hands over to the Housing.com crawler and tags each record with
``raw["served_by"] = "housing.com"``, so ``makaan`` keeps working either way.

Note: Housing.com / Makaan (Akamai) answer 406 "Security Alert" to
non-Indian datacenter IPs. Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

import logging
from typing import Iterator

from ..base import register
from ..models import Record
from ._common import map_generic
from ._portal import PortalCrawler

BASE = "https://www.makaan.com"


@register
class Makaan(PortalCrawler):
    name = "makaan"
    title = "Makaan"
    homepage = BASE
    urls = {
        "sale": BASE + "/{city}-residential-property/buy-property-in-{city}-city",
        "rent": BASE + "/{city}-residential-property/rent-property-in-{city}-city",
    }
    json_predicate = staticmethod(lambda d: "listingId" in d and d.get("listingId") and any(k in d for k in ("price", "minPrice", "size")))

    def json_key(self, d: dict) -> object:
        return d.get("listingId")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        return map_generic(self.name, d, BASE + "/", transaction, city)

    def crawl(self, city: str = "hyderabad", transaction: str | None = None, max_pages: int = 1, **options: str) -> Iterator[Record]:
        t = self.resolve_transaction(transaction)
        probe = self.fetcher.get(self.start_url(city, t, options), allow_redirects=False, check=False)
        if probe.status in (301, 302, 307, 308) and "housing" in probe.headers.get("Location", "").lower():
            logging.getLogger(__name__).info("makaan.com redirects to Housing.com; crawling Housing.com instead")
            from .housing import Housing

            for rec in Housing(self.fetcher, self.render).crawl(city, t, max_pages, **options):
                rec.raw = {**rec.raw, "served_by": "housing.com"}
                rec.source = self.name
                yield rec
            return
        yield from super().crawl(city, t, max_pages, **options)
