"""Housing.com – housing.com (buy, rent, new projects).

Search pages embed the result set in ``window.__INITIAL_STATE__`` (listing
dicts with ``listingId``, ``price``/``displayPrice``, ``coords``, ``url``,
``title``...) and publish schema.org JSON-LD for the same items.
Pagination: ``?page=N``.

Note: Housing.com (Akamai) answers 406 "Security Alert" to non-Indian
datacenter IPs. Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ..models import Record
from ._common import map_generic
from ._portal import PortalCrawler

BASE = "https://housing.com"


@register
class Housing(PortalCrawler):
    name = "housing"
    title = "Housing.com"
    homepage = BASE
    transactions = ("sale", "rent", "projects")
    urls = {
        "sale": BASE + "/in/buy/{city}/{city}",
        "rent": BASE + "/in/rent/{city}/{city}",
        "projects": BASE + "/in/buy/projects-in-{city}",
    }
    json_predicate = staticmethod(
        lambda d: ("listingId" in d or "projectId" in d) and any(k in d for k in ("price", "displayPrice", "priceRange", "minPrice")) and ("url" in d or "title" in d or "name" in d)
    )

    def json_key(self, d: dict) -> object:
        return d.get("listingId") or d.get("projectId") or d.get("id")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        kind = "project" if transaction == "projects" or ("projectId" in d and "listingId" not in d) else "listing"
        return map_generic(self.name, d, BASE, None if kind == "project" else transaction, city, kind=kind)
