"""CommonFloor – commonfloor.com (buy and rent).

Search pages (``/<city>-property/for-sale``) render listing cards with
JSON-LD; we read JSON-LD first and fall back to card text. Pagination:
``?page=N``.

Note: CommonFloor (Akamai) returns 403 to non-Indian datacenter IPs. Run
from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ..models import Record
from ._common import map_generic
from ._portal import PortalCrawler

BASE = "https://www.commonfloor.com"


@register
class CommonFloor(PortalCrawler):
    name = "commonfloor"
    title = "CommonFloor"
    homepage = BASE
    urls = {
        "sale": BASE + "/{city}-property/for-sale",
        "rent": BASE + "/{city}-property/for-rent",
    }
    json_predicate = staticmethod(lambda d: ("listingId" in d or "listing_id" in d or "propertyId" in d) and any(k in d for k in ("price", "rent", "expectedPrice")))

    def json_key(self, d: dict) -> object:
        return d.get("listingId") or d.get("listing_id") or d.get("propertyId")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        return map_generic(self.name, d, BASE + "/", transaction, city)
