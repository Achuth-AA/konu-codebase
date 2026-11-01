"""JustDial – justdial.com (real-estate agents and listings).

The "Real Estate Agents" category page for a city lists businesses with
name, address, rating, votes and locality. JustDial publishes them as
schema.org JSON-LD (``LocalBusiness`` / ``RealEstateAgent`` in an
``ItemList``) and in its Next.js ``__NEXT_DATA__`` (``docid`` dicts); we read
either. Pagination: ``/page-N``.

Note: JustDial (Akamai) returns 403 to non-Indian datacenter IPs. Run from
an Indian network (or ``--proxy``).
"""

from __future__ import annotations

from ..base import register
from ..extract import clean_text, first, parse_number
from ..http import absolute
from ..models import Record
from ._common import AGENT_LD_TYPES, _geo, record_from_ld
from ._portal import PortalCrawler

BASE = "https://www.justdial.com"
CATEGORIES = {
    "agents": "/{City}/Real-Estate-Agents/nct-10401947",
    "builders": "/{City}/Builders-And-Developers/nct-10063180",
}


def map_business(d: dict, city: str) -> Record:
    addr = d.get("address")
    if isinstance(addr, dict):
        addr = ", ".join(str(v) for k, v in addr.items() if not k.startswith("@") and isinstance(v, str))
    lat, lon = _geo(d)
    return Record(
        source="justdial",
        kind="agent",
        source_id=str(first(d, "docid", "docId", "id", "@id") or "") or None,
        url=absolute(BASE, first(d, "url", "detailUrl", "seoUrl")),
        title=clean_text(first(d, "name", "compname", "companyName")),
        locality=clean_text(first(d, "area", "locality", "address.addressLocality")),
        city=city,
        address=clean_text(addr) if isinstance(addr, str) else clean_text(first(d, "fullAddress", "addr")),
        latitude=lat or parse_number(first(d, "lat", "latitude")),
        longitude=lon or parse_number(first(d, "lon", "lng", "longitude")),
        contact_name=clean_text(first(d, "contactPerson", "contact_person")),
        status=str(first(d, "aggregateRating.ratingValue", "rating", "totalRating") or "") or None,
        image_url=first(d, "image", "logo", "thumbnail") if isinstance(first(d, "image", "logo", "thumbnail"), str) else None,
        raw=d,
    )


@register
class JustDial(PortalCrawler):
    name = "justdial"
    title = "JustDial"
    homepage = BASE
    transactions = ("agents", "builders")
    urls = {k: BASE + v for k, v in CATEGORIES.items()}
    page_url = staticmethod(lambda first, n: f"{first}/page-{n}")
    json_predicate = staticmethod(lambda d: ("docid" in d or "docId" in d) and ("name" in d or "compname" in d))

    def json_key(self, d: dict) -> object:
        return d.get("docid") or d.get("docId")

    def map_json(self, d: dict, city: str, transaction: str) -> Record:
        return map_business(d, city)

    def parse(self, html: str, url: str, city: str, transaction: str) -> list[Record]:
        from ..extract import ld_items

        recs = super().parse(html, url, city, transaction) if type(self).json_predicate else []
        recs = [r for r in recs if r.kind == "agent"]
        if not recs:
            recs = [map_business(d, city) for d in ld_items(html, *AGENT_LD_TYPES, "RealEstateAgent") if d.get("name")]
        for r in recs:
            r.transaction = None
            if transaction == "builders":
                r.kind = "agent"
                r.property_type = "builder"
        return recs
