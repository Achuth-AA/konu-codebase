"""IndiaProperty – indiaproperty.com (buy and rent).

Search pages render listing cards (with JSON-LD where present); we read
JSON-LD first and fall back to the card heuristics. Pagination: ``?page=N``.

Note: IndiaProperty's CloudFront distribution rejects non-Indian IPs
("The request could not be satisfied"). Run from an Indian network (or
``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ..models import Record
from ._common import map_generic
from ._portal import PortalCrawler

BASE = "https://www.indiaproperty.com"


@register
class IndiaProperty(PortalCrawler):
    name = "indiaproperty"
    title = "IndiaProperty"
    homepage = BASE
    urls = {
        "sale": BASE + "/residential-property-for-sale-in-{city}",
        "rent": BASE + "/residential-property-for-rent-in-{city}",
    }
    json_predicate = staticmethod(lambda d: ("propertyId" in d or "property_id" in d or "listingId" in d) and any(k in d for k in ("price", "expectedPrice", "rent")))

    def json_key(self, d: dict) -> object:
        return d.get("propertyId") or d.get("property_id") or d.get("listingId")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        return map_generic(self.name, d, BASE + "/", transaction, city)
