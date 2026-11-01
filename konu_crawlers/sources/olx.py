"""OLX – olx.in (Real Estate: houses & apartments for sale / rent).

Category pages (``/<city>_g<location-id>/for-sale-houses-apartments_c1725``,
``..._c1723`` for rent) embed ``window.__APP`` whose ``states.items.elements``
holds ad dicts (id, title, price.value.raw, locations_resolved, parameters
[type, bedrooms, bathrooms, furnishing, area], created_at, images).
Pagination: ``?page=N``.

The location id is OLX's: Hyderabad = 4058526. For other cities pass
``--location-id`` (visible in the URL when you pick the city on olx.in).

Note: OLX (Akamai) returns 403 / HTTP2 resets to non-Indian datacenter IPs.
Run from an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ..extract import parse_bhk, parse_number
from ..models import Record
from ._common import area_to_sqft, map_generic
from ._portal import PortalCrawler

BASE = "https://www.olx.in"
LOCATION_IDS = {"hyderabad": "4058526", "bangalore": "4058803", "bengaluru": "4058803", "chennai": "4059162", "mumbai": "4058997", "pune": "4059014", "delhi": "4058659"}


def _params(d: dict) -> dict[str, str]:
    out = {}
    for p in d.get("parameters") or []:
        if isinstance(p, dict) and p.get("key"):
            out[p["key"]] = p.get("formatted_value") or p.get("value_name") or p.get("value")
    return out


@register
class OLX(PortalCrawler):
    name = "olx"
    title = "OLX"
    homepage = BASE
    urls = {
        "sale": BASE + "/{city}_g{location_id}/for-sale-houses-apartments_c1725",
        "rent": BASE + "/{city}_g{location_id}/for-rent-houses-apartments_c1723",
    }
    json_predicate = staticmethod(lambda d: "id" in d and "title" in d and ("locations_resolved" in d or ("price" in d and "parameters" in d)))

    def start_url(self, city: str, transaction: str, options: dict) -> str:
        loc = options.get("location_id") or LOCATION_IDS.get(city.strip().lower())
        if not loc:
            raise ValueError(f"olx: unknown OLX location id for {city!r}; pass --location-id")
        return super().start_url(city, transaction, {**options, "location_id": loc})

    def json_key(self, d: dict) -> object:
        return d.get("ad_id") or d.get("id")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        rec = map_generic(self.name, d, BASE, transaction, city)
        p = _params(d)
        rec.url = rec.url or f"{BASE}/item/{d.get('id')}"
        rec.property_type = p.get("type") or rec.property_type
        rec.bedrooms = parse_number(p.get("rooms") or p.get("bedrooms")) or parse_bhk(rec.title) or rec.bedrooms
        rec.bathrooms = parse_number(p.get("bathrooms")) or rec.bathrooms
        rec.furnishing = p.get("furnishing") or rec.furnishing
        rec.area_sqft = area_to_sqft(p.get("sqft") or p.get("superbuiltup_area") or p.get("carpet_area")) or rec.area_sqft
        rec.posted_by = p.get("listed_by") or rec.posted_by
        rec.project_name = p.get("project_name") or rec.project_name
        return rec
